"""Thread-safe, in-memory simulation runs and deterministic event replay."""
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import logging
import hashlib
import json
import os
from pathlib import Path
from threading import RLock
from uuid import uuid4

from .models import (DisasterEvent, EventHistory, EventRecord, Road, ScheduledEvent,
                     SimulationStatus, StateSnapshot, TwinState)
from .road_loader import load_roads
from .sample_data import create_initial_state
from .simulation import reduce_event
from .storage import configured_store

logger = logging.getLogger(__name__)


@dataclass
class Run:
    baseline: TwinState
    state: TwinState
    run_id: str = field(default_factory=lambda: str(uuid4()))
    phase: str = 'ready'
    accepted: list[EventRecord] = field(default_factory=list)
    scheduled: list[ScheduledEvent] = field(default_factory=list)
    # Each journal operation increases revision exactly once. Clock-only
    # operations are recorded too, so replay reproduces timestamps and revisions.
    journal: list[DisasterEvent | datetime] = field(default_factory=list)
    sequence: int = 0
    network: dict = field(default_factory=dict)


class StateManager:
    def __init__(self, storage_path: str | None = None, roads_loader=None, store=None) -> None:
        self._lock = RLock()
        self._store = store if store is not None else (configured_store(storage_path) if storage_path else None)
        self._roads_loader = roads_loader or load_roads
        self._archives: dict[str, Run] = {}
        self._run = self._new_run()
        self._restore()

    def _state_document(self, state: TwinState) -> dict:
        document = state.model_dump(mode="json")
        if not getattr(self._store, "compact_roads", False):
            return document
        document["road_states"] = {road.id: {"status": road.status, "risk_score": road.risk_score}
                                   for road in state.roads if road.status != "open" or road.risk_score != 0}
        document.pop("roads")
        return document

    def _run_document(self, r: Run) -> dict:
        return {"run_id": r.run_id, "phase": r.phase, "network": r.network, "state": self._state_document(r.state),
                "baseline": self._state_document(r.baseline),
                "accepted": [record.model_dump(mode="json") for record in r.accepted],
                "scheduled": [record.model_dump(mode="json") for record in r.scheduled],
                "sequence": r.sequence,
                "journal": [item.model_dump(mode="json") if isinstance(item, DisasterEvent) else {"clock": item.isoformat()} for item in r.journal]}

    def _document(self):
        document = self._run_document(self._run)
        document["archives"] = {run_id: self._run_document(run) for run_id, run in self._archives.items()}
        return document

    def _persist(self):
        if self._store:
            self._store.save(self._document())

    def _restore(self):
        if not self._store:
            return
        document = self._store.load()
        if not document:
            self._persist()
            return
        try:
            from .models import EventRecord, ScheduledEvent
            active_ids = {road.id for road in self._run.baseline.roads}
            active_baseline = self._run.baseline
            self._run = self._load_run(document, active_baseline)
            self._archives = {key: self._load_run(value, active_baseline)
                              for key, value in document.get('archives', {}).items()}
            restored_ids = {road.id for road in self._run.baseline.roads}
            if restored_ids != active_ids:
                logger.warning("Ignoring persisted simulation state whose road IDs do not match the active network")
                self._run = self._new_run()
                self._persist()
        except (KeyError, TypeError, ValueError) as error:
            raise RuntimeError("Persistent simulation store has an unsupported schema") from error

    def _load_state(self, document: dict, active_baseline: TwinState) -> TwinState:
        if "roads" in document:
            return TwinState.model_validate(document)
        raw = active_baseline.model_dump(mode="json")
        raw.update({key: value for key, value in document.items() if key not in {"roads", "road_states"}})
        by_id = {road["id"]: road for road in raw["roads"]}
        for road_id, dynamic in document.get("road_states", {}).items():
            if road_id not in by_id:
                raise ValueError("Persisted state references an unknown active road")
            by_id[road_id].update(dynamic)
        return TwinState.model_validate(raw)

    def _load_run(self, document: dict, active_baseline: TwinState) -> Run:
        if 'roads' not in document['baseline'] and document.get('network') != self._network_reference(active_baseline):
            raise RuntimeError('Persisted network version differs from local GIS; restore the matching dataset before starting')
        baseline = self._load_state(document["baseline"], active_baseline)
        run = Run(baseline=baseline, state=self._load_state(document["state"], active_baseline),
                  run_id=document["run_id"], phase=document["phase"],
                  accepted=[EventRecord.model_validate(x) for x in document["accepted"]],
                  scheduled=[ScheduledEvent.model_validate(x) for x in document["scheduled"]],
                  sequence=int(document["sequence"]), network=document.get('network', self._network_reference(baseline)),
                  journal=[DisasterEvent.model_validate(x) if "clock" not in x else datetime.fromisoformat(x["clock"]) for x in document["journal"]])
        return run

    @staticmethod
    def _network_reference(state):
        static = [road.model_dump(mode='json', exclude={'status', 'risk_score'}) for road in state.roads]
        encoded = json.dumps(sorted(static, key=lambda road: road['id']), sort_keys=True, separators=(',', ':')).encode()
        return {'sha256': hashlib.sha256(encoded).hexdigest(), 'road_count': len(static),
                'reference': 'data/processed/chennai_roads.graphml',
                'source': state.roads[0].source if state.roads else None}

    def _new_run(self) -> Run:
        state = create_initial_state()
        state.roads = [Road(**road) for road in self._roads_loader()]
        state = TwinState.model_validate(state.model_dump())
        return Run(baseline=state.model_copy(deep=True), state=state, network=self._network_reference(state))

    def _select_run(self, run_id: str | None) -> Run:
        if run_id is None or run_id == self._run.run_id:
            return self._run
        if run_id not in self._archives:
            raise ValueError('Unknown run ID')
        return self._archives[run_id]

    def get_state(self) -> TwinState:
        with self._lock:
            return self._run.state.model_copy(deep=True)

    def reset(self) -> TwinState:
        with self._lock:
            previous = self._run
            replacement = self._new_run()  # A load failure leaves the live run intact.
            self._archives[previous.run_id] = previous
            self._run = replacement
            try:
                self._persist()
            except Exception:
                self._run = previous
                self._archives.pop(previous.run_id, None)
                raise
            logger.info('Reset simulation; new run=%s', replacement.run_id)
            return self.get_state()

    def status(self) -> SimulationStatus:
        with self._lock:
            r = self._run
            return SimulationStatus(simulation_id=r.state.simulation_id, run_id=r.run_id,
                                    phase=r.phase, timestamp=r.state.timestamp,
                                    revision=r.state.revision, accepted_events=len(r.accepted),
                                    scheduled_events=len(r.scheduled))

    def transition(self, action: str) -> SimulationStatus:
        with self._lock:
            expected, target = {'start': ('ready', 'running'), 'pause': ('running', 'paused'),
                                'resume': ('paused', 'running')}[action]
            if self._run.phase != expected:
                raise ValueError(f'{action} requires phase {expected}; current phase is {self._run.phase}')
            previous = self._run.phase
            self._run.phase = target
            try:
                self._persist()
            except Exception:
                self._run.phase = previous
                raise
            return self.status()

    def _validate_new_id(self, event: DisasterEvent) -> None:
        r = self._run
        if any(record.event.event_id == event.event_id for record in r.accepted + r.scheduled):
            raise ValueError('Event already processed or scheduled')

    def apply_event(self, event: DisasterEvent) -> TwinState:
        with self._lock:
            event = DisasterEvent.model_validate(event.model_dump())
            self._validate_new_id(event)
            r = self._run
            if any(record.event.timestamp <= event.timestamp for record in r.scheduled):
                raise ValueError('Advance scheduled events before injecting an event at or beyond their timestamp')
            candidate = reduce_event(r.state, event)
            now = datetime.now(timezone.utc)
            record = EventRecord(event=event, revision=candidate.revision, ingested_at=now, accepted_at=now)
            previous_state = r.state
            r.state = candidate
            r.accepted.append(record)
            r.journal.append(event)
            try:
                self._persist()
            except Exception:
                r.state, r.accepted, r.journal = previous_state, r.accepted[:-1], r.journal[:-1]
                raise
            logger.info('Accepted event=%s revision=%s run=%s', event.event_id, candidate.revision, r.run_id)
            return self.get_state()

    def schedule(self, event: DisasterEvent) -> ScheduledEvent:
        with self._lock:
            event = DisasterEvent.model_validate(event.model_dump())
            self._validate_new_id(event)
            r = self._run
            if event.timestamp <= r.state.timestamp:
                raise ValueError('Scheduled event must be strictly later than simulation clock')
            # Validate the entire projected queue in its eventual execution order.
            # This catches cumulative shortages and out-of-order rainfall changes.
            record = ScheduledEvent(event=event, sequence=r.sequence, ingested_at=datetime.now(timezone.utc))
            queue = sorted([*r.scheduled, record], key=lambda item: (item.event.timestamp, item.sequence))
            candidate = r.state
            for item in queue:
                candidate = reduce_event(candidate, item.event)
            r.sequence += 1
            previous_queue, previous_sequence = r.scheduled, r.sequence - 1
            r.scheduled = queue
            try:
                self._persist()
            except Exception:
                r.scheduled, r.sequence = previous_queue, previous_sequence
                raise
            logger.info('Scheduled event=%s timestamp=%s run=%s', event.event_id, event.timestamp, r.run_id)
            return record.model_copy(deep=True)

    def advance(self, seconds: float) -> SimulationStatus:
        with self._lock:
            r = self._run
            if r.phase == 'ready':
                raise ValueError('Start simulation before advancing')
            if not 0 < seconds <= 31536000:
                raise ValueError('seconds must be positive and at most 31536000')
            try:
                target = r.state.timestamp + timedelta(seconds=seconds)
            except OverflowError as error:
                raise ValueError('Simulation timestamp exceeds supported range') from error
            if target <= r.state.timestamp:
                raise ValueError('Advance must be at least one microsecond')
            due = [item for item in r.scheduled if item.event.timestamp <= target]
            candidate = r.state.model_copy(deep=True)
            records: list[EventRecord] = []
            operations: list[DisasterEvent | datetime] = []
            for item in due:
                candidate = reduce_event(candidate, item.event)
                records.append(EventRecord(event=item.event.model_copy(deep=True), revision=candidate.revision,
                                           ingested_at=item.ingested_at, accepted_at=datetime.now(timezone.utc)))
                operations.append(item.event.model_copy(deep=True))
            if candidate.timestamp < target:
                candidate.timestamp = target
                candidate.revision += 1
                operations.append(target)
            candidate = TwinState.model_validate(candidate.model_dump())
            # Nothing above mutates the live run; publish only a complete batch.
            previous_state, previous_accepted, previous_journal, previous_queue = r.state, r.accepted, r.journal, r.scheduled
            r.state = candidate
            r.accepted = [*r.accepted, *records]
            r.journal = [*r.journal, *operations]
            r.scheduled = r.scheduled[len(due):]
            try:
                self._persist()
            except Exception:
                r.state, r.accepted, r.journal, r.scheduled = previous_state, previous_accepted, previous_journal, previous_queue
                raise
            logger.info('Advanced run=%s timestamp=%s events=%s', r.run_id, target, len(due))
            return self.status()

    def get_events(self) -> list[DisasterEvent]:
        with self._lock:
            return [record.event.model_copy(deep=True) for record in self._run.accepted]

    def history(self, run_id: str | None = None) -> EventHistory:
        with self._lock:
            r = self._select_run(run_id)
            return EventHistory(run_id=r.run_id, accepted=r.accepted, scheduled=r.scheduled).model_copy(deep=True)

    def runs(self) -> list[str]:
        with self._lock:
            return [*self._archives, self._run.run_id]

    def snapshot(self, revision: int | None = None, run_id: str | None = None) -> StateSnapshot:
        """Replay immutable baseline + journal; never apply effects to live state."""
        with self._lock:
            r = self._select_run(run_id)
            revision = r.state.revision if revision is None else revision
            if not r.baseline.revision <= revision <= r.state.revision:
                raise ValueError('Revision is outside this run history')
            state = r.baseline.model_copy(deep=True)
            for operation in r.journal[:revision - r.baseline.revision]:
                if isinstance(operation, DisasterEvent):
                    state = reduce_event(state, operation)
                else:
                    state.timestamp = operation
                    state.revision += 1
            return StateSnapshot(run_id=r.run_id, revision=state.revision, timestamp=state.timestamp, state=state)


state_manager = StateManager(os.getenv("SIMULATION_DB_PATH", str(Path(__file__).resolve().parents[1] / "data" / "simulation_state.json")))
