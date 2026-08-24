"""Session-local activation state, kept separate from global entry definitions."""

from __future__ import annotations

import time
from collections.abc import Iterable
from dataclasses import dataclass, field

from .models import ActivationContext, WorldTreeEntry


@dataclass
class _SessionState:
    active: dict[str, WorldTreeEntry] = field(default_factory=dict)
    blocked_ids: set[str] = field(default_factory=set)
    touched_at: float = field(default_factory=time.time)


class WorldTreeSessionStore:
    """Stores runtime state per UMO without ever modifying entry configuration.

    This intentionally fixes an upstream semantic pitfall: adding/removing a
    session ID from a global scope does not mean "enable/disable in this
    session". Scope remains a global access rule; this class owns temporary
    per-session pins and blocks instead.
    """

    def __init__(self) -> None:
        self._states: dict[str, _SessionState] = {}

    def _state(self, session_id: str) -> _SessionState:
        state = self._states.setdefault(session_id, _SessionState())
        state.touched_at = time.time()
        return state

    def activate(
        self,
        ctx: ActivationContext,
        entries: Iterable[WorldTreeEntry],
        *,
        allow_same_priority: bool,
    ) -> list[str]:
        """Attach newly triggered entries without refreshing existing lifetimes."""

        state = self._state(ctx.session_id)
        self._discard_expired(state)
        attached: list[str] = []
        for definition in entries:
            if definition.id in state.blocked_ids:
                continue
            existing = state.active.get(definition.id)
            if existing and existing.active:
                # Repeated keyword matches must not reset duration/times.
                continue
            instance = definition.clone_runtime()
            instance.enter_session()
            if not allow_same_priority:
                for old_id, old in list(state.active.items()):
                    if old.priority == instance.priority and old_id != instance.id:
                        state.active.pop(old_id, None)
            state.active[instance.id] = instance
            # A scheduled signal is consumed only after a session actually
            # accepts the entry. A blocked session must not steal the signal
            # from another eligible conversation.
            definition.consume_cron_signal()
            attached.append(instance.id)
        return attached

    def pin(
        self,
        ctx: ActivationContext,
        definition: WorldTreeEntry,
        *,
        allow_same_priority: bool,
    ) -> bool:
        """Manually activate one scope-permitted entry for this session only."""

        if not definition.enabled or not definition._scope_allows(ctx):
            return False
        state = self._state(ctx.session_id)
        state.blocked_ids.discard(definition.id)
        existing = state.active.get(definition.id)
        if existing and existing.active:
            return True
        instance = definition.clone_runtime()
        instance.enter_session()
        if not allow_same_priority:
            for old_id, old in list(state.active.items()):
                if old.priority == instance.priority and old_id != instance.id:
                    state.active.pop(old_id, None)
        state.active[instance.id] = instance
        return True

    def block(self, session_id: str, entry_ids: Iterable[str]) -> list[str]:
        state = self._state(session_id)
        changed: list[str] = []
        for entry_id in entry_ids:
            if entry_id not in state.blocked_ids:
                changed.append(entry_id)
            state.blocked_ids.add(entry_id)
            state.active.pop(entry_id, None)
        return changed

    def unblock(self, session_id: str, entry_ids: Iterable[str]) -> list[str]:
        state = self._state(session_id)
        changed: list[str] = []
        for entry_id in entry_ids:
            if entry_id in state.blocked_ids:
                state.blocked_ids.remove(entry_id)
                changed.append(entry_id)
        return changed

    def active_for(self, ctx: ActivationContext) -> list[WorldTreeEntry]:
        state = self._states.get(ctx.session_id)
        if not state:
            return []
        state.touched_at = time.time()
        self._discard_expired(state)
        return sorted(
            (
                entry
                for entry in state.active.values()
                if entry.id not in state.blocked_ids and entry.can_consume(ctx)
            ),
            key=lambda item: (item.priority, item.name.casefold(), item.id),
        )

    def status(self, session_id: str) -> tuple[list[WorldTreeEntry], set[str]]:
        state = self._states.get(session_id)
        if not state:
            return [], set()
        state.touched_at = time.time()
        self._discard_expired(state)
        active = sorted(
            state.active.values(), key=lambda item: (item.priority, item.name.casefold())
        )
        return active, set(state.blocked_ids)

    def clear(self, session_id: str, *, include_blocks: bool = False) -> None:
        state = self._states.get(session_id)
        if not state:
            return
        state.active.clear()
        if include_blocks:
            state.blocked_ids.clear()
        state.touched_at = time.time()
        if not state.blocked_ids:
            self._states.pop(session_id, None)

    def reconcile(self, definitions: Iterable[WorldTreeEntry]) -> None:
        """Apply global edits/deletes to already-active session copies."""

        current = {entry.id: entry for entry in definitions}
        for session_id, state in list(self._states.items()):
            for entry_id, active in list(state.active.items()):
                definition = current.get(entry_id)
                if definition is None or not definition.enabled:
                    state.active.pop(entry_id, None)
                    continue
                state.active[entry_id] = definition.with_runtime_from(active)
            state.blocked_ids.intersection_update(current)
            if not state.active and not state.blocked_ids:
                self._states.pop(session_id, None)

    def prune_idle(self, *, max_idle_seconds: int) -> int:
        now = time.time()
        stale = [
            session_id
            for session_id, state in self._states.items()
            if now - state.touched_at > max_idle_seconds
        ]
        for session_id in stale:
            self._states.pop(session_id, None)
        return len(stale)

    @staticmethod
    def _discard_expired(state: _SessionState) -> None:
        for entry_id, entry in list(state.active.items()):
            if not entry.active:
                state.active.pop(entry_id, None)
