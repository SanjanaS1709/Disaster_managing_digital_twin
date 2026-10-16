"""Atomic local JSON and versioned Firestore snapshot storage."""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
from uuid import uuid4


class LocalStore:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def load(self):
        if not self.path.exists():
            return None
        try:
            return json.loads(self.path.read_text(encoding='utf-8'))
        except (OSError, json.JSONDecodeError) as error:
            raise RuntimeError(f'Persistent simulation store is unreadable: {self.path}') from error

    def save(self, document):
        payload = json.dumps(document, ensure_ascii=False, allow_nan=False, indent=2)
        temporary = None
        try:
            with NamedTemporaryFile('w', encoding='utf-8', dir=self.path.parent, delete=False) as handle:
                temporary = Path(handle.name)
                handle.write(payload)
            os.replace(temporary, self.path)
        except OSError as error:
            if temporary:
                temporary.unlink(missing_ok=True)
            raise RuntimeError(f'Could not persist simulation store: {self.path}') from error


class FirestoreStore:
    """Immutable chunked snapshots published with an atomic compare-and-swap.

    One writer per service is supported. A stale process cannot overwrite a newer
    head: the update-time precondition fails and its manager rolls back. Readers
    follow only a committed head, so partial uploads are never visible.
    Unreferenced generations are retained for operator-controlled cleanup.
    """
    compact_roads = True
    CHUNK_BYTES = 200_000

    def __init__(self, project_id=None, service_account_json=None,
                 collection='agentic_disaster_twin', namespace='simulation', client=None):
        self._loaded = False
        self._update_time = None
        self.collection = collection
        self.namespace = namespace
        if client is not None:
            self.client = client
            return
        import firebase_admin
        from firebase_admin import credentials, firestore
        if not project_id:
            raise RuntimeError('FIREBASE_PROJECT_ID is required for Firestore')
        name = 'module1-' + hashlib.sha256((project_id + (service_account_json or '')).encode()).hexdigest()[:16]
        try:
            app = firebase_admin.get_app(name)
        except ValueError:
            if os.getenv('FIRESTORE_EMULATOR_HOST'):
                from google.auth.credentials import AnonymousCredentials
                class EmulatorCredential(credentials.Base):
                    def get_credential(self):
                        return AnonymousCredentials()
                credential = EmulatorCredential()
            else:
                # This variable is a secure file PATH, never inline private-key JSON.
                credential = credentials.Certificate(service_account_json) if service_account_json else credentials.ApplicationDefault()
            app = firebase_admin.initialize_app(credential, {'projectId': project_id}, name=name)
        self.client = firestore.client(app=app)

    def _root(self):
        return self.client.collection(self.collection).document(self.namespace)

    def load(self):
        snapshot = self._root().get()
        if not snapshot.exists:
            self._loaded, self._update_time = True, None
            return None
        head = snapshot.to_dict()
        if head.get('schema_version') != 1:
            raise RuntimeError('Unsupported Firestore storage schema')
        generation = self._root().collection('generations').document(head['generation'])
        chunks = []
        for index in range(head['chunks']):
            chunk = generation.collection('chunks').document(str(index)).get()
            if not chunk.exists:
                raise RuntimeError('Committed Firestore snapshot is incomplete')
            chunks.append(chunk.to_dict()['payload'])
        payload = b''.join(chunks)
        if hashlib.sha256(payload).hexdigest() != head['sha256']:
            raise RuntimeError('Firestore snapshot checksum mismatch')
        document = json.loads(payload)
        self._loaded, self._update_time = True, snapshot.update_time
        return document

    def save(self, document):
        if not self._loaded:
            raise RuntimeError('Load Firestore store before saving')
        from google.cloud.firestore_v1 import LastUpdateOption
        payload = json.dumps(document, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode('utf-8')
        generation_id = uuid4().hex
        generation = self._root().collection('generations').document(generation_id)
        chunks = [payload[i:i + self.CHUNK_BYTES] for i in range(0, len(payload), self.CHUNK_BYTES)]
        for start in range(0, len(chunks), 20):
            batch = self.client.batch()
            for index in range(start, min(start + 20, len(chunks))):
                batch.create(generation.collection('chunks').document(str(index)), {'payload': chunks[index]})
            batch.commit()
        head = {'schema_version': 1, 'generation': generation_id, 'chunks': len(chunks),
                'sha256': hashlib.sha256(payload).hexdigest()}
        batch = self.client.batch()
        if self._update_time is None:
            batch.create(self._root(), head)
        else:
            batch.update(self._root(), head, option=LastUpdateOption(self._update_time))
        # An ambiguous commit fails closed: restart/reload before accepting more
        # writes if the server committed but the response was lost.
        result = batch.commit()
        self._update_time = result[0].update_time


def configured_store(storage_path, namespace='simulation'):
    backend = os.getenv('SIMULATION_STORAGE_BACKEND', 'local').lower()
    if backend == 'local':
        return LocalStore(storage_path)
    if backend == 'firestore':
        return FirestoreStore(project_id=os.getenv('FIREBASE_PROJECT_ID'),
                              service_account_json=os.getenv('FIREBASE_SERVICE_ACCOUNT_JSON') or None,
                              collection=os.getenv('FIRESTORE_COLLECTION', 'agentic_disaster_twin'),
                              namespace=namespace)
    raise RuntimeError('SIMULATION_STORAGE_BACKEND must be local or firestore')
