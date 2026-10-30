"""Assistant conversation persistence and approved guidance retrieval."""

from __future__ import annotations

import os
import re
from datetime import datetime, timedelta, timezone
from threading import Lock
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from app.storage import create_firestore_client

HISTORY_MESSAGES = 12
SESSION_TTL_DAYS = 30
KNOWLEDGE_CACHE_SECONDS = 300
MAX_KNOWLEDGE_DOCUMENTS = 200
MAX_RETRIEVED_SOURCES = 4
STOP_WORDS = {
    "a", "about", "an", "and", "are", "as", "at", "be", "by", "can",
    "do", "for", "from", "how", "i", "in", "is", "it", "me", "my", "of",
    "on", "or", "the", "this", "to", "what", "when", "where", "with", "you",
}


class AssistantKnowledgeDocument(BaseModel):
    """A reviewed, attributable guidance record suitable for retrieval."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=120)
    title: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1, max_length=6000)
    keywords: list[str] = Field(min_length=1, max_length=40)
    source_title: str = Field(min_length=1, max_length=200)
    source_url: str = Field(min_length=8, max_length=1000)
    reviewed: bool
    reviewed_by: str = Field(min_length=1, max_length=120)
    reviewed_at: str = Field(min_length=10, max_length=40)
    active: bool = True

    @field_validator("id")
    @classmethod
    def id_must_be_a_single_firestore_segment(cls, value: str) -> str:
        if "/" in value or value in {".", ".."}:
            raise ValueError("knowledge id must be a single Firestore document ID")
        return value

    @field_validator("source_url")
    @classmethod
    def source_must_be_http(cls, value: str) -> str:
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("knowledge source_url must be an absolute HTTP(S) URL")
        return value

    @field_validator("reviewed_at")
    @classmethod
    def reviewed_at_must_be_iso_date(cls, value: str) -> str:
        try:
            datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as error:
            raise ValueError("reviewed_at must be an ISO-8601 date or timestamp") from error
        return value

    @field_validator("keywords")
    @classmethod
    def normalize_keywords(cls, values: list[str]) -> list[str]:
        normalized = list(dict.fromkeys(value.strip().lower() for value in values if value.strip()))
        if not normalized:
            raise ValueError("knowledge documents need at least one nonblank keyword")
        return normalized


class MemoryAssistantRepository:
    """Development fallback; history is intentionally process-local."""

    def __init__(self) -> None:
        self._sessions: dict[UUID, list[dict[str, str]]] = {}
        self._lock = Lock()

    def get_history(self, session_id: UUID) -> list[dict[str, object]]:
        with self._lock:
            return [
                {"role": message["role"], "content": message["content"]}
                for message in self._sessions.get(session_id, [])
            ]

    def get_display_history(self, session_id: UUID) -> list[dict[str, object]]:
        with self._lock:
            return [dict(message) for message in self._sessions.get(session_id, [])]

    def save_exchange(
        self, session_id: UUID, user_message: str, answer: str,
        sources: list[dict[str, str]] | None = None,
    ) -> None:
        with self._lock:
            history = self._sessions.setdefault(session_id, [])
            history.extend([
                {"role": "user", "content": user_message},
                {"role": "assistant", "content": answer, "sources": sources or []},
            ])
            self._sessions[session_id] = history[-HISTORY_MESSAGES:]

    def delete_session(self, session_id: UUID) -> None:
        with self._lock:
            self._sessions.pop(session_id, None)

    def retrieve_guidance(self, query: str) -> list[AssistantKnowledgeDocument]:
        return []

    def upsert_knowledge(self, documents: list[AssistantKnowledgeDocument]) -> None:
        raise RuntimeError("Knowledge import requires ASSISTANT_STORAGE_BACKEND=firestore")


class FirestoreAssistantRepository:
    """Persist bounded chat sessions and retrieve only reviewed source records."""

    def __init__(self, client=None, collection: str | None = None, namespace: str | None = None) -> None:
        self.client = client or create_firestore_client(
            os.getenv("FIREBASE_PROJECT_ID"),
            os.getenv("FIREBASE_SERVICE_ACCOUNT_JSON") or None,
        )
        root = self.client.collection(
            collection or os.getenv("FIRESTORE_COLLECTION", "agentic_disaster_twin")
        ).document(namespace or os.getenv("FIRESTORE_NAMESPACE", "simulation"))
        assistant_root = root.collection("assistant_data")
        self._sessions = assistant_root.document("sessions").collection("items")
        self._knowledge = assistant_root.document("knowledge").collection("documents")
        self._knowledge_cache: list[AssistantKnowledgeDocument] = []
        self._knowledge_cache_at = 0.0
        self._cache_lock = Lock()

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc)

    def get_display_history(self, session_id: UUID) -> list[dict[str, object]]:
        reference = self._sessions.document(str(session_id))
        snapshot = reference.get()
        if not snapshot.exists:
            return []
        document = snapshot.to_dict() or {}
        expiry = document.get("expires_at")
        if isinstance(expiry, datetime) and expiry <= self._now():
            reference.delete()
            return []
        history = document.get("messages", [])
        if not isinstance(history, list):
            raise RuntimeError("Stored assistant conversation has an invalid format")
        safe_history: list[dict[str, object]] = []
        for message in history[-HISTORY_MESSAGES:]:
            if (
                isinstance(message, dict)
                and message.get("role") in {"user", "assistant"}
                and isinstance(message.get("content"), str)
            ):
                safe_message: dict[str, object] = {
                    "role": message["role"], "content": message["content"]
                }
                if message["role"] == "assistant" and isinstance(message.get("sources"), list):
                    safe_message["sources"] = [
                        source for source in message["sources"]
                        if isinstance(source, dict)
                        and isinstance(source.get("title"), str)
                        and isinstance(source.get("url"), str)
                    ][:MAX_RETRIEVED_SOURCES]
                safe_history.append(safe_message)
        return safe_history

    def get_history(self, session_id: UUID) -> list[dict[str, object]]:
        return [
            {"role": message["role"], "content": message["content"]}
            for message in self.get_display_history(session_id)
        ]

    def save_exchange(
        self, session_id: UUID, user_message: str, answer: str,
        sources: list[dict[str, str]] | None = None,
    ) -> None:
        from google.cloud.firestore_v1 import transactional

        reference = self._sessions.document(str(session_id))
        transaction = self.client.transaction()

        @transactional
        def append_exchange(transaction):
            snapshot = reference.get(transaction=transaction)
            document = snapshot.to_dict() if snapshot.exists else {}
            history = document.get("messages", []) if isinstance(document, dict) else []
            if not isinstance(history, list):
                history = []
            history.extend([
                {"role": "user", "content": user_message},
                {"role": "assistant", "content": answer, "sources": sources or []},
            ])
            now = self._now()
            transaction.set(reference, {
                "messages": history[-HISTORY_MESSAGES:],
                "updated_at": now,
                "expires_at": now + timedelta(days=SESSION_TTL_DAYS),
            })

        append_exchange(transaction)

    def delete_session(self, session_id: UUID) -> None:
        self._sessions.document(str(session_id)).delete()

    def _reviewed_documents(self) -> list[AssistantKnowledgeDocument]:
        import time

        with self._cache_lock:
            if time.monotonic() - self._knowledge_cache_at < KNOWLEDGE_CACHE_SECONDS:
                return list(self._knowledge_cache)
            records = []
            query = self._knowledge.where("reviewed", "==", True).limit(MAX_KNOWLEDGE_DOCUMENTS)
            for snapshot in query.stream():
                raw = snapshot.to_dict() or {}
                raw.setdefault("id", snapshot.id)
                if raw.get("active") is not True:
                    continue
                try:
                    records.append(AssistantKnowledgeDocument.model_validate(raw))
                except ValidationError:
                    # Bad or incomplete records are excluded from model context.
                    continue
            self._knowledge_cache = records
            self._knowledge_cache_at = time.monotonic()
            return list(records)

    def retrieve_guidance(self, query: str) -> list[AssistantKnowledgeDocument]:
        query_terms = set(re.findall(r"[a-z0-9]+", query.lower())) - STOP_WORDS
        if not query_terms:
            return []
        ranked: list[tuple[int, AssistantKnowledgeDocument]] = []
        for document in self._reviewed_documents():
            terms = {
                term
                for keyword in document.keywords
                for term in re.findall(r"[a-z0-9]+", keyword.lower())
            }
            terms.update(re.findall(r"[a-z0-9]+", document.title.lower()))
            overlap = query_terms & terms
            if overlap:
                ranked.append((len(overlap), document))
        ranked.sort(key=lambda item: (-item[0], item[1].id))
        return [document for _, document in ranked[:MAX_RETRIEVED_SOURCES]]

    def upsert_knowledge(self, documents: list[AssistantKnowledgeDocument]) -> None:
        for document in documents:
            if not document.reviewed:
                raise ValueError(f"Knowledge document {document.id!r} is not marked reviewed")
            self._knowledge.document(document.id).set(document.model_dump())
        with self._cache_lock:
            self._knowledge_cache_at = 0.0


_repository = None
_repository_lock = Lock()


def get_assistant_repository():
    global _repository
    with _repository_lock:
        if _repository is None:
            backend = os.getenv("ASSISTANT_STORAGE_BACKEND", "memory").strip().lower()
            if backend == "memory":
                _repository = MemoryAssistantRepository()
            elif backend == "firestore":
                _repository = FirestoreAssistantRepository()
            else:
                raise RuntimeError("ASSISTANT_STORAGE_BACKEND must be memory or firestore")
        return _repository


