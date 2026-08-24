"""APScheduler integration for standard five-field cron lore entries."""

from __future__ import annotations

from typing import TYPE_CHECKING

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

if TYPE_CHECKING:
    from .library import WorldTreeLibrary
    from .models import WorldTreeEntry


def _normalise_weekday_field(field: str) -> str:
    """Translate crontab numeric weekdays (0/7=Sunday) for APScheduler."""

    def one(token: str) -> str:
        token = token.strip().casefold()
        if token in {"sun", "mon", "tue", "wed", "thu", "fri", "sat", "*"}:
            return token
        if token.isdigit():
            value = int(token)
            if not 0 <= value <= 7:
                raise ValueError(f"invalid weekday: {token}")
            return "6" if value in {0, 7} else str(value - 1)
        raise ValueError(f"invalid weekday: {token}")

    def part(source: str) -> str:
        base, *step = source.split("/", 1)
        if "-" in base:
            start, end = base.split("-", 1)
            return f"{one(start)}-{one(end)}" + (f"/{step[0]}" if step else "")
        return one(base) + (f"/{step[0]}" if step else "")

    return ",".join(part(item) for item in field.split(","))


def build_trigger(expression: str) -> CronTrigger:
    """Build a trigger using documented standard crontab weekday semantics."""

    minute, hour, day, month, weekday = expression.split()
    return CronTrigger(
        minute=minute,
        hour=hour,
        day=day,
        month=month,
        day_of_week=_normalise_weekday_field(weekday),
    )


class WorldTreeCronScheduler:
    """Reloadable scheduler which only signals entries; it never injects itself."""

    def __init__(self, library: WorldTreeLibrary, log) -> None:
        self._library = library
        self._log = log
        self._scheduler = AsyncIOScheduler()
        self._started = False
        self.invalid_entries: dict[str, str] = {}
        self._library.on_changed.append(self.reload)

    def start(self) -> None:
        if self._started:
            return
        self._register_all()
        self._scheduler.start()
        self._started = True
        self._log.info("[worldtree] cron scheduler started")

    def shutdown(self) -> None:
        if not self._started:
            return
        self._scheduler.shutdown(wait=False)
        self._started = False
        self._log.info("[worldtree] cron scheduler stopped")

    def reload(self) -> None:
        if not self._started:
            return
        self._scheduler.remove_all_jobs()
        self._register_all()

    def _register_all(self) -> None:
        self.invalid_entries.clear()
        for entry in self._library.entries:
            if entry.enabled and entry.has_cron:
                self._register(entry)

    def _register(self, entry: WorldTreeEntry) -> None:
        try:
            trigger = build_trigger(entry.cron)
        except (TypeError, ValueError) as exc:
            self.invalid_entries[entry.id] = str(exc)
            self._log.warning(
                "[worldtree] invalid cron for %s: %s (%s)", entry.name, entry.cron, exc
            )
            return
        self._scheduler.add_job(
            self._on_trigger,
            trigger=trigger,
            args=[entry.id],
            id=f"worldtree:{entry.id}",
            replace_existing=True,
            misfire_grace_time=60,
            coalesce=True,
        )

    def _on_trigger(self, entry_id: str) -> None:
        if self._library.mark_cron_fired(entry_id):
            self._log.debug("[worldtree] cron signal opened for entry %s", entry_id)
