"""Validated backend boundary for external agent proposals."""
from copy import deepcopy
from threading import RLock
import os
from pathlib import Path
from .storage import configured_store
from .models import ActionApplicationResult, DecisionStatus, ProposedAction
from .road_loader import routing_graph


class DecisionManager:
    def __init__(self, store=None, graph_builder=None):
        self._lock = RLock()
        self._store = store
        self._graph_builder = graph_builder
        self._items: dict[str, ProposedAction] = {}
        self._decisions: dict[str, DecisionStatus] = {}
        document = store.load() if store else None
        if document:
            self._items = {key: ProposedAction.model_validate(value) for key, value in document['items'].items()}
            self._decisions = {key: DecisionStatus.model_validate(value) for key, value in document['decisions'].items()}

    def _save(self, items, decisions):
        if self._store:
            self._store.save({'items': {key: value.model_dump(mode='json') for key, value in items.items()},
                              'decisions': {key: value.model_dump(mode='json') for key, value in decisions.items()}})

    def propose(self, action: ProposedAction, state, expected_revision: int):
        with self._lock:
            if action.action_id in self._items:
                raise ValueError('Action already exists')
            if expected_revision != state.revision:
                raise ValueError('Stale state revision')
            if action.type == 'ROUTE_ASSIGNMENT':
                if not action.route_road_ids:
                    raise ValueError('Route must contain at least one road')
                graph = self._graph_builder(state) if self._graph_builder else routing_graph(state)
                edges = {edge['road_id']: (u, v, k) for u, v, k, edge in graph.edges(keys=True, data=True)}
                previous = None
                for road_id in action.route_road_ids:
                    if road_id not in edges:
                        raise ValueError(f'Unknown, blocked or disconnected road: {road_id}')
                    edge = edges[road_id]
                    if previous is not None and previous[1] != edge[0]:
                        raise ValueError('Route road IDs are not directionally connected')
                    previous = edge
            elif action.type == 'RESOURCE_TRANSFER':
                if not action.unit_id or not action.item or not action.quantity or action.quantity <= 0:
                    raise ValueError('Resource transfer requires unit_id, item and positive quantity')
                unit = next((u for u in state.relief_units if u.id == action.unit_id), None)
                if unit is None or action.target_id not in {s.id for s in state.shelters}:
                    raise ValueError('Unknown resource transfer entity')
                if unit.inventory.get(action.item, 0) < action.quantity:
                    raise ValueError('Insufficient inventory for proposed transfer')
            action = action.model_copy(update={'status': 'pending_approval' if action.requires_approval else 'approved'})
            items = {**self._items, action.action_id: action}
            decisions = {**self._decisions, action.action_id: DecisionStatus(action_id=action.action_id, status=action.status, state_revision=expected_revision, reason=action.reason)}
            self._save(items, decisions)
            self._items, self._decisions = items, decisions
            return deepcopy(self._decisions[action.action_id])

    def list(self):
        with self._lock:
            return [deepcopy(x) for x in self._decisions.values()]

    def get(self, action_id):
        if action_id not in self._decisions:
            raise ValueError('Decision not found')
        return deepcopy(self._decisions[action_id])

    def decide(self, action_id, approved, state, actor='operator'):
        with self._lock:
            if action_id not in self._items:
                raise ValueError('Decision not found')
            action = self._items[action_id]
            current = deepcopy(self._decisions[action_id])
            if current.status not in {'pending_approval', 'approved'}:
                raise ValueError('Decision is no longer actionable')
            if current.state_revision != state.revision:
                raise ValueError('Decision state revision is stale')
            if not approved:
                current.status, current.decided_by = 'rejected', actor
            elif action.type == 'ROUTE_ASSIGNMENT':
                current.status, current.decided_by = 'applied', actor
                current.state_revision = state.revision
            else:
                raise ValueError('Resource transfer requires a state event integration before it can be applied')
            decisions = {**self._decisions, action_id: current}
            items = {**self._items, action_id: action.model_copy(update={'status': current.status})}
            self._save(items, decisions)
            self._items, self._decisions = items, decisions
            return deepcopy(current)


_decision_path = os.getenv('DECISION_DB_PATH', str(Path(__file__).resolve().parents[1] / 'data' / 'decisions.json'))
decision_manager = DecisionManager(configured_store(_decision_path, namespace='decisions') if _decision_path else None)
