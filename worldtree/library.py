"""Persistent library operations, server-side search, and bulk entry changes."""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

from .models import (
    ENTRY_TEMPLATES,
    EntryValidationError,
    WorldTreeEntry,
    prepared_entry_payload,
)

DATA_VERSION = 2
MAX_PAGE_SIZE = 100
DEFAULT_PAGE_SIZE = 30
MAX_IMPORT_ENTRIES = 2_000


class RevisionConflict(RuntimeError):
    """Raised when a page attempts to save an out-of-date library view."""


@dataclass(frozen=True)
class ImportReport:
    added: int
    replaced: int
    renamed: int
    skipped: int
    invalid: int
    messages: list[str]

    def as_dict(self) -> dict[str, Any]:
        return {
            "added": self.added,
            "replaced": self.replaced,
            "renamed": self.renamed,
            "skipped": self.skipped,
            "invalid": self.invalid,
            "messages": self.messages,
        }


class WorldTreeLibrary:
    """The single source of truth for persisted worldbook entries.

    The object is synchronous on purpose. AstrBot invokes hooks and plugin Page
    handlers on one event loop, and the plugin wraps all mutations in an async
    lock. Keeping this layer synchronous makes each mutation atomic with
    respect to an individual event-loop turn and keeps it straightforward to
    unit test.
    """

    def __init__(self, config: Any) -> None:
        self._config = config
        self._entries: dict[str, WorldTreeEntry] = {}
        self._revision = 0
        self._loaded = False
        self.on_changed: list[Callable[[], None]] = []

    @property
    def revision(self) -> int:
        return self._revision

    @property
    def entries(self) -> list[WorldTreeEntry]:
        return self.list_entries()

    def load(self) -> dict[str, Any]:
        """Load config data defensively so one bad imported row cannot break boot."""

        raw_entries = self._config.get("entry_storage", [])
        repaired = False
        skipped: list[str] = []
        if not isinstance(raw_entries, list):
            raw_entries = []
            repaired = True
            skipped.append("entry_storage 不是列表，已按空列表处理")

        entries: dict[str, WorldTreeEntry] = {}
        names: set[str] = set()
        for index, raw in enumerate(raw_entries, start=1):
            if not isinstance(raw, dict):
                repaired = True
                skipped.append(f"第 {index} 项不是对象，已跳过")
                continue
            try:
                entry = WorldTreeEntry.from_dict(raw)
            except EntryValidationError as exc:
                repaired = True
                skipped.append(f"第 {index} 项无效：{exc}")
                continue

            if entry.id in entries:
                entry = WorldTreeEntry.from_dict({**entry.to_dict(), "id": ""})
                repaired = True
                skipped.append(f"条目“{entry.name}”的重复 ID 已自动修复")
            if entry.name.casefold() in names:
                new_name = self._unique_name(entry.name, names)
                entry = WorldTreeEntry.from_dict({**entry.to_dict(), "name": new_name})
                repaired = True
                skipped.append(f"重复名称已重命名为“{new_name}”")
            entries[entry.id] = entry
            names.add(entry.name.casefold())
            if entry.to_dict() != raw:
                repaired = True

        self._entries = entries
        self._revision = self._read_revision()
        self._loaded = True
        if repaired or int(self._config.get("data_version", 0) or 0) != DATA_VERSION:
            self._persist(increment=False)
        return {
            "loaded": len(entries),
            "repaired": repaired,
            "messages": skipped,
            "revision": self._revision,
        }

    def list_entries(self) -> list[WorldTreeEntry]:
        return sorted(
            self._entries.values(),
            key=lambda item: (item.priority, item.folder.casefold(), item.name.casefold(), item.id),
        )

    def get(self, entry_id: str) -> WorldTreeEntry | None:
        return self._entries.get(entry_id)

    def get_by_name(self, name: str) -> WorldTreeEntry | None:
        key = name.strip().casefold()
        return next((entry for entry in self._entries.values() if entry.name.casefold() == key), None)

    def templates(self) -> list[dict[str, Any]]:
        return [
            {"key": item.key, "label": item.label, "defaults": dict(item.defaults)}
            for item in ENTRY_TEMPLATES.values()
        ]

    def paged_entries(
        self,
        *,
        page: int = 1,
        page_size: int = DEFAULT_PAGE_SIZE,
        query: str = "",
        status: str = "all",
        folder: str = "",
        tag: str = "",
    ) -> dict[str, Any]:
        """Search and paginate on the backend instead of expanding every entry."""

        page = max(1, int(page))
        page_size = min(MAX_PAGE_SIZE, max(1, int(page_size)))
        query = query.strip()
        if len(query) > 200:
            raise EntryValidationError("搜索关键词不能超过 200 个字符")
        if status not in {"all", "enabled", "disabled"}:
            raise EntryValidationError("状态筛选只能是 all、enabled 或 disabled")

        wanted_folder = folder.strip().casefold()
        wanted_tag = tag.strip().casefold()
        words = [word.casefold() for word in query.split()]

        def matches(entry: WorldTreeEntry) -> bool:
            if status == "enabled" and not entry.enabled:
                return False
            if status == "disabled" and entry.enabled:
                return False
            if wanted_folder and entry.folder.casefold() != wanted_folder:
                return False
            if wanted_tag and wanted_tag not in {item.casefold() for item in entry.tags}:
                return False
            if not words:
                return True
            haystack = "\n".join(
                [
                    entry.name,
                    entry.content,
                    entry.folder,
                    " ".join(entry.tags),
                    " ".join(entry.keywords),
                    " ".join(entry.scope),
                ]
            ).casefold()
            return all(word in haystack for word in words)

        matching = [entry for entry in self.list_entries() if matches(entry)]
        total = len(matching)
        total_pages = max(1, (total + page_size - 1) // page_size)
        page = min(page, total_pages)
        start = (page - 1) * page_size
        items = matching[start : start + page_size]
        folders = sorted(
            {entry.folder for entry in self._entries.values() if entry.folder},
            key=str.casefold,
        )
        tags = sorted(
            {tag for entry in self._entries.values() for tag in entry.tags}, key=str.casefold
        )
        return {
            "revision": self._revision,
            "entries": [entry.summary() for entry in items],
            "pagination": {
                "page": page,
                "page_size": page_size,
                "total": total,
                "total_pages": total_pages,
            },
            "facets": {"folders": folders, "tags": tags},
            "stats": self.stats(),
            "templates": self.templates(),
        }

    def stats(self) -> dict[str, int]:
        entries = list(self._entries.values())
        return {
            "total": len(entries),
            "enabled": sum(1 for item in entries if item.enabled),
            "disabled": sum(1 for item in entries if not item.enabled),
            "folders": len({item.folder for item in entries if item.folder}),
            "tags": len({tag.casefold() for item in entries for tag in item.tags}),
            "scheduled": sum(1 for item in entries if item.has_cron),
        }

    def create(self, payload: dict[str, Any], *, expected_revision: int | None = None) -> WorldTreeEntry:
        self._check_revision(expected_revision)
        data = prepared_entry_payload(payload)
        data["id"] = ""
        data["created_at"] = int(time.time())
        data["updated_at"] = data["created_at"]
        entry = WorldTreeEntry.from_dict(data)
        self._ensure_name_available(entry.name)
        self._entries[entry.id] = entry
        self._persist()
        return entry

    def update(
        self,
        entry_id: str,
        payload: dict[str, Any],
        *,
        expected_revision: int | None = None,
    ) -> WorldTreeEntry:
        self._check_revision(expected_revision)
        existing = self._require_entry(entry_id)
        allowed = {
            "name",
            "content",
            "enabled",
            "priority",
            "keywords",
            "keyword_mode",
            "scope",
            "cron",
            "duration",
            "times",
            "probability",
            "folder",
            "tags",
            "template",
        }
        data = existing.to_dict()
        for key in allowed:
            if key in payload:
                data[key] = payload[key]
        data["id"] = existing.id
        data["created_at"] = existing.created_at
        data["updated_at"] = int(time.time())
        data = prepared_entry_payload(data)
        entry = WorldTreeEntry.from_dict(data)
        self._ensure_name_available(entry.name, excluding_id=entry_id)
        self._entries[entry_id] = entry
        self._persist()
        return entry

    def delete(self, entry_id: str, *, expected_revision: int | None = None) -> WorldTreeEntry:
        self._check_revision(expected_revision)
        entry = self._require_entry(entry_id)
        self._entries.pop(entry_id, None)
        self._persist()
        return entry

    def bulk(
        self,
        entry_ids: Iterable[str],
        *,
        action: str,
        value: Any = None,
        expected_revision: int | None = None,
    ) -> dict[str, Any]:
        self._check_revision(expected_revision)
        unique_ids = list(dict.fromkeys(str(item) for item in entry_ids))
        if not unique_ids:
            raise EntryValidationError("请至少选择一个条目")
        selected = [self._entries[item] for item in unique_ids if item in self._entries]
        missing = [item for item in unique_ids if item not in self._entries]
        if not selected:
            raise EntryValidationError("没有找到所选条目")

        changed: list[str] = []
        now = int(time.time())
        if action == "set_enabled":
            enabled = value if isinstance(value, bool) else None
            if enabled is None:
                raise EntryValidationError("批量开关需要布尔值")
            for entry in selected:
                if entry.enabled != enabled:
                    self._entries[entry.id] = WorldTreeEntry.from_dict(
                        {**entry.to_dict(), "enabled": enabled, "updated_at": now}
                    )
                    changed.append(entry.id)
        elif action == "set_folder":
            if not isinstance(value, str):
                raise EntryValidationError("文件夹必须是文本")
            for entry in selected:
                if entry.folder != value.strip():
                    self._entries[entry.id] = WorldTreeEntry.from_dict(
                        {**entry.to_dict(), "folder": value, "updated_at": now}
                    )
                    changed.append(entry.id)
        elif action in {"add_tag", "remove_tag"}:
            if not isinstance(value, str) or not value.strip():
                raise EntryValidationError("标签不能为空")
            tag = value.strip()
            for entry in selected:
                tags = list(entry.tags)
                existing = {item.casefold() for item in tags}
                if action == "add_tag" and tag.casefold() not in existing:
                    tags.append(tag)
                elif action == "remove_tag":
                    tags = [item for item in tags if item.casefold() != tag.casefold()]
                    if tags == entry.tags:
                        continue
                else:
                    continue
                self._entries[entry.id] = WorldTreeEntry.from_dict(
                    {**entry.to_dict(), "tags": tags, "updated_at": now}
                )
                changed.append(entry.id)
        elif action == "delete":
            for entry in selected:
                self._entries.pop(entry.id, None)
                changed.append(entry.id)
        else:
            raise EntryValidationError("不支持的批量操作")

        if changed:
            self._persist()
        return {"changed": changed, "missing": missing, "revision": self._revision}

    def import_entries(
        self,
        raw_entries: Iterable[dict[str, Any]],
        *,
        strategy: str = "skip",
        expected_revision: int | None = None,
    ) -> ImportReport:
        """Import a batch with explicit conflict handling and one atomic save."""

        self._check_revision(expected_revision)
        if strategy not in {"skip", "replace", "rename"}:
            raise EntryValidationError("导入冲突策略只能是 skip、replace 或 rename")
        raw_list = list(raw_entries)
        if len(raw_list) > MAX_IMPORT_ENTRIES:
            raise EntryValidationError(f"单次最多导入 {MAX_IMPORT_ENTRIES} 个条目")

        added = replaced = renamed = skipped = invalid = 0
        messages: list[str] = []
        changed = False
        names = {item.name.casefold() for item in self._entries.values()}
        for index, raw in enumerate(raw_list, start=1):
            try:
                if not isinstance(raw, dict):
                    raise EntryValidationError("不是对象")
                data = prepared_entry_payload(raw)
                data["id"] = ""
                data.setdefault("created_at", int(time.time()))
                data["updated_at"] = int(time.time())
                entry = WorldTreeEntry.from_dict(data)
            except EntryValidationError as exc:
                invalid += 1
                messages.append(f"第 {index} 项未导入：{exc}")
                continue

            existing = self.get_by_name(entry.name)
            if existing is not None:
                if strategy == "skip":
                    skipped += 1
                    messages.append(f"已跳过同名条目“{entry.name}”")
                    continue
                if strategy == "replace":
                    entry = WorldTreeEntry.from_dict(
                        {
                            **entry.to_dict(),
                            "id": existing.id,
                            "created_at": existing.created_at,
                            "updated_at": int(time.time()),
                        }
                    )
                    self._entries[entry.id] = entry
                    replaced += 1
                    changed = True
                    continue
                new_name = self._unique_name(entry.name, names)
                entry = WorldTreeEntry.from_dict({**entry.to_dict(), "name": new_name})
                renamed += 1

            # A name that appeared earlier in the current import also needs a
            # deterministic policy rather than silently overwriting itself.
            if entry.name.casefold() in names:
                if strategy == "skip":
                    skipped += 1
                    messages.append(f"已跳过同批次重名条目“{entry.name}”")
                    continue
                new_name = self._unique_name(entry.name, names)
                entry = WorldTreeEntry.from_dict({**entry.to_dict(), "name": new_name})
                renamed += 1

            self._entries[entry.id] = entry
            names.add(entry.name.casefold())
            added += 1
            changed = True

        if changed:
            self._persist()
        return ImportReport(added, replaced, renamed, skipped, invalid, messages[:30])

    def mark_cron_fired(self, entry_id: str) -> bool:
        entry = self._entries.get(entry_id)
        if entry is None or not entry.enabled:
            return False
        entry.mark_cron_fired()
        return True

    def activation_candidates(self, ctx, text: str) -> list[WorldTreeEntry]:
        return [entry for entry in self.list_entries() if entry.try_activate(ctx, text)]

    def _read_revision(self) -> int:
        try:
            value = int(self._config.get("library_revision", 0) or 0)
        except (TypeError, ValueError):
            return 0
        return max(0, value)

    def _persist(self, *, increment: bool = True) -> None:
        if increment:
            self._revision += 1
        self._config["entry_storage"] = [entry.to_dict() for entry in self.list_entries()]
        self._config["library_revision"] = self._revision
        self._config["data_version"] = DATA_VERSION
        self._config.save_config()
        self._emit_changed()

    def _emit_changed(self) -> None:
        for callback in tuple(self.on_changed):
            callback()

    def _check_revision(self, expected_revision: int | None) -> None:
        if expected_revision is not None and expected_revision != self._revision:
            raise RevisionConflict(
                "条目库已被其他操作更新。请刷新列表后再保存，避免覆盖新修改。"
            )

    def _require_entry(self, entry_id: str) -> WorldTreeEntry:
        entry = self._entries.get(entry_id)
        if entry is None:
            raise EntryValidationError("找不到该条目")
        return entry

    def _ensure_name_available(self, name: str, *, excluding_id: str | None = None) -> None:
        existing = self.get_by_name(name)
        if existing is not None and existing.id != excluding_id:
            raise EntryValidationError(f"已存在同名条目“{name}”")

    @staticmethod
    def _unique_name(base: str, names: set[str]) -> str:
        stem = base.strip() or "未命名条目"
        number = 2
        candidate = stem
        while candidate.casefold() in names:
            suffix = f" ({number})"
            candidate = stem[: max(1, 80 - len(suffix))] + suffix
            number += 1
        return candidate
