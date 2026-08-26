"""Validated data model and activation rules for WorldTree Lore entries."""

from __future__ import annotations

import math
import random
import re
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

MAX_ENTRY_NAME_LENGTH = 80
MAX_ENTRY_CONTENT_LENGTH = 50_000
MAX_KEYWORDS = 32
MAX_KEYWORD_LENGTH = 256
MAX_TAGS = 20
MAX_TAG_LENGTH = 40
MAX_SCOPES = 64
MAX_SCOPE_LENGTH = 256
MAX_FOLDER_LENGTH = 80
MAX_CRON_LENGTH = 128
MAX_DURATION_SECONDS = 31_536_000
MAX_TIMES = 1_000_000
MAX_MATCH_TEXT_CHARS = 6_000
DEFAULT_CRON_WINDOW_SECONDS = 300


class EntryValidationError(ValueError):
    """Raised when an entry supplied by a command, API, or import is invalid."""


@dataclass(frozen=True)
class ActivationContext:
    """The stable identity data used when deciding whether an entry may apply."""

    user_id: str = ""
    group_id: str = ""
    session_id: str = ""
    is_admin: bool = False

    @classmethod
    def from_values(
        cls,
        *,
        user_id: Any = "",
        group_id: Any = "",
        session_id: Any = "",
        is_admin: bool = False,
    ) -> ActivationContext:
        return cls(
            user_id="" if user_id is None else str(user_id),
            group_id="" if group_id is None else str(group_id),
            session_id="" if session_id is None else str(session_id),
            is_admin=bool(is_admin),
        )


@dataclass(frozen=True)
class EntryTemplate:
    """A small set of useful starting values for new entries."""

    key: str
    label: str
    defaults: dict[str, Any]


ENTRY_TEMPLATES: dict[str, EntryTemplate] = {
    "common": EntryTemplate(
        "common",
        "常规条目",
        {
            "enabled": True,
            "priority": 50,
            "keywords": [],
            "duration": 180,
            "times": 5,
            "probability": 1.0,
        },
    ),
    "resident": EntryTemplate(
        "resident",
        "常驻条目",
        {
            "enabled": True,
            "priority": 10,
            "keywords": ["re:.*"],
            "duration": 0,
            "times": 0,
            "probability": 1.0,
        },
    ),
    "chance": EntryTemplate(
        "chance",
        "随机条目",
        {
            "enabled": True,
            "priority": 30,
            "keywords": ["re:.*"],
            "duration": 180,
            "times": 1,
            "probability": 0.05,
        },
    ),
    "schedule": EntryTemplate(
        "schedule",
        "日程条目",
        {
            "enabled": True,
            "priority": 80,
            "keywords": [],
            "cron": "0 0 * * *",
            "duration": 86_400,
            "times": 0,
            "probability": 1.0,
        },
    ),
    "group": EntryTemplate(
        "group",
        "群聊限定条目",
        {
            "enabled": True,
            "priority": 120,
            "keywords": ["re:.*"],
            "duration": 0,
            "times": 0,
            "probability": 1.0,
        },
    ),
    "user": EntryTemplate(
        "user",
        "用户限定条目",
        {
            "enabled": True,
            "priority": 150,
            "keywords": ["re:.*"],
            "duration": 0,
            "times": 0,
            "probability": 1.0,
        },
    ),
}


def template_defaults(template: Any) -> tuple[str, dict[str, Any]]:
    """Return a supported template key and a defensive copy of its defaults."""

    key = str(template or "common").strip().lower()
    # These two keys are kept for imported upstream files. They become ordinary
    # entries; their imported scope still controls where they are usable.
    aliases = {"default": "common"}
    key = aliases.get(key, key)
    if key not in ENTRY_TEMPLATES:
        raise EntryValidationError(
            f"未知的条目模板：{template!s}。可选：{', '.join(ENTRY_TEMPLATES)}"
        )
    return key, dict(ENTRY_TEMPLATES[key].defaults)


def _coerce_bool(value: Any, field_name: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        mapping = {
            "true": True,
            "1": True,
            "yes": True,
            "on": True,
            "false": False,
            "0": False,
            "no": False,
            "off": False,
        }
        parsed = mapping.get(value.strip().casefold())
        if parsed is not None:
            return parsed
    raise EntryValidationError(f"{field_name} 必须是布尔值")


def _coerce_int(
    value: Any,
    field_name: str,
    *,
    minimum: int,
    maximum: int,
) -> int:
    if isinstance(value, bool):
        raise EntryValidationError(f"{field_name} 必须是整数")
    try:
        if isinstance(value, float) and not value.is_integer():
            raise ValueError
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise EntryValidationError(f"{field_name} 必须是整数") from exc
    if not minimum <= parsed <= maximum:
        raise EntryValidationError(f"{field_name} 必须在 {minimum} 到 {maximum} 之间")
    return parsed


def _coerce_float(
    value: Any,
    field_name: str,
    *,
    minimum: float,
    maximum: float,
) -> float:
    if isinstance(value, bool):
        raise EntryValidationError(f"{field_name} 必须是数字")
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise EntryValidationError(f"{field_name} 必须是数字") from exc
    if not math.isfinite(parsed) or not minimum <= parsed <= maximum:
        raise EntryValidationError(f"{field_name} 必须在 {minimum} 到 {maximum} 之间")
    return parsed


def _split_string(value: str) -> list[str]:
    """Accept useful form input while keeping canonical storage as a list."""

    return [part.strip() for part in re.split(r"[,\n\r]", value) if part.strip()]


def _normalise_string_list(
    value: Any,
    field_name: str,
    *,
    maximum_items: int,
    maximum_length: int,
    case_insensitive: bool = False,
) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        values: Iterable[Any] = _split_string(value)
    elif isinstance(value, (list, tuple, set)):
        values = value
    else:
        raise EntryValidationError(f"{field_name} 必须是文本列表")

    result: list[str] = []
    seen: set[str] = set()
    for item in values:
        if not isinstance(item, str):
            raise EntryValidationError(f"{field_name} 的每一项都必须是文本")
        item = item.strip()
        if not item:
            continue
        if len(item) > maximum_length:
            raise EntryValidationError(
                f"{field_name} 的单项长度不能超过 {maximum_length} 个字符"
            )
        key = item.casefold() if case_insensitive else item
        if key in seen:
            continue
        seen.add(key)
        result.append(item)

    if len(result) > maximum_items:
        raise EntryValidationError(f"{field_name} 最多允许 {maximum_items} 项")
    return result


def _normalise_id(value: Any) -> str:
    if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_-]{8,80}", value):
        return value
    return uuid4().hex


def _normalise_timestamp(value: Any, fallback: int) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError):
        return fallback
    return result if result > 0 else fallback


def _normalise_keyword_mode(value: Any) -> str:
    mode = str(value or "modern").strip().lower()
    if mode not in {"modern", "legacy_regex"}:
        raise EntryValidationError("keyword_mode 必须是 modern 或 legacy_regex")
    return mode


def legacy_keywords_to_modern(keywords: Any) -> list[str] | None:
    """Rewrite whole-list regex keywords into the explicit ``re:`` form.

    ``legacy_regex`` is the upstream convention where every keyword is a regex.
    The same entry behaves identically under ``modern`` matching once each
    pattern carries an ``re:`` prefix, so both conventions can be collapsed into
    one without changing what any entry matches. The prefix is added
    unconditionally: a legacy keyword that already reads ``re:foo`` is a regex
    matching the literal text ``re:foo``, so it becomes ``re:re:foo``.

    Returns ``None`` when the rewrite cannot be done losslessly, letting callers
    keep such an entry on the legacy mode instead of corrupting it.
    """

    if not isinstance(keywords, list):
        return None
    result: list[str] = []
    for raw in keywords:
        if not isinstance(raw, str):
            return None
        keyword = raw.strip()
        if not keyword:
            continue
        rewritten = f"re:{keyword}"
        if len(rewritten) > MAX_KEYWORD_LENGTH:
            return None
        result.append(rewritten)
    return result


def _compile_regex(source: str, *, name: str) -> re.Pattern[str]:
    try:
        return re.compile(source, re.IGNORECASE)
    except re.error as exc:
        raise EntryValidationError(f"条目“{name}”包含无效正则：{source} ({exc})") from exc


@dataclass
class WorldTreeEntry:
    """One worldbook entry plus its intentionally non-persistent runtime state."""

    id: str
    name: str
    content: str
    enabled: bool = True
    priority: int = 50
    keywords: list[str] = field(default_factory=list)
    keyword_mode: str = "modern"
    scope: list[str] = field(default_factory=list)
    cron: str = ""
    duration: int = 180
    times: int = 5
    probability: float = 1.0
    folder: str = ""
    tags: list[str] = field(default_factory=list)
    template: str = "common"
    created_at: int = field(default_factory=lambda: int(time.time()))
    updated_at: int = field(default_factory=lambda: int(time.time()))
    _activated_at: float | None = field(default=None, init=False, repr=False)
    _inject_count: int = field(default=0, init=False, repr=False)
    _cron_fired_at: float | None = field(default=None, init=False, repr=False)
    _matchers: list[tuple[str, str | re.Pattern[str]]] = field(
        default_factory=list, init=False, repr=False
    )

    def __post_init__(self) -> None:
        self._compile_matchers()

    @classmethod
    def from_dict(
        cls,
        payload: dict[str, Any],
        *,
        now: int | None = None,
    ) -> WorldTreeEntry:
        """Create a canonical, fully validated entry from an untrusted mapping."""

        if not isinstance(payload, dict):
            raise EntryValidationError("条目必须是对象")
        data = dict(payload)
        now = int(time.time()) if now is None else now
        template, defaults = template_defaults(
            data.get("template") or data.get("__template_key")
        )

        def value(key: str, fallback: Any) -> Any:
            return data[key] if key in data else defaults.get(key, fallback)

        raw_name = value("name", "")
        if not isinstance(raw_name, str):
            raise EntryValidationError("名称必须是文本")
        name = raw_name.strip()
        if not name:
            raise EntryValidationError("名称不能为空")
        if len(name) > MAX_ENTRY_NAME_LENGTH:
            raise EntryValidationError(
                f"名称长度不能超过 {MAX_ENTRY_NAME_LENGTH} 个字符"
            )

        raw_content = value("content", "")
        if not isinstance(raw_content, str):
            raise EntryValidationError("注入内容必须是文本")
        if not raw_content.strip():
            raise EntryValidationError("注入内容不能为空")
        if len(raw_content) > MAX_ENTRY_CONTENT_LENGTH:
            raise EntryValidationError(
                f"注入内容长度不能超过 {MAX_ENTRY_CONTENT_LENGTH} 个字符"
            )

        raw_folder = value("folder", "")
        if not isinstance(raw_folder, str):
            raise EntryValidationError("文件夹必须是文本")
        folder = raw_folder.strip()
        if len(folder) > MAX_FOLDER_LENGTH:
            raise EntryValidationError(
                f"文件夹长度不能超过 {MAX_FOLDER_LENGTH} 个字符"
            )

        raw_cron = value("cron", "")
        if not isinstance(raw_cron, str):
            raise EntryValidationError("Cron 表达式必须是文本")
        cron = raw_cron.strip()
        if len(cron) > MAX_CRON_LENGTH:
            raise EntryValidationError(
                f"Cron 表达式长度不能超过 {MAX_CRON_LENGTH} 个字符"
            )
        if cron and len(cron.split()) != 5:
            raise EntryValidationError("Cron 表达式必须是标准的 5 段格式：分 时 日 月 周")

        keyword_mode = _normalise_keyword_mode(value("keyword_mode", "modern"))
        keywords = _normalise_string_list(
            value("keywords", []),
            "关键词",
            maximum_items=MAX_KEYWORDS,
            maximum_length=MAX_KEYWORD_LENGTH,
        )
        scope = _normalise_string_list(
            value("scope", []),
            "生效范围",
            maximum_items=MAX_SCOPES,
            maximum_length=MAX_SCOPE_LENGTH,
        )
        tags = _normalise_string_list(
            value("tags", []),
            "标签",
            maximum_items=MAX_TAGS,
            maximum_length=MAX_TAG_LENGTH,
            case_insensitive=True,
        )

        entry = cls(
            id=_normalise_id(data.get("id")),
            name=name,
            content=raw_content,
            enabled=_coerce_bool(value("enabled", True), "启用状态"),
            priority=_coerce_int(
                value("priority", 50), "优先级", minimum=-9_999, maximum=9_999
            ),
            keywords=keywords,
            keyword_mode=keyword_mode,
            scope=scope,
            cron=cron,
            duration=_coerce_int(
                value("duration", 180),
                "生效时长",
                minimum=0,
                maximum=MAX_DURATION_SECONDS,
            ),
            times=_coerce_int(
                value("times", 5), "生效次数", minimum=0, maximum=MAX_TIMES
            ),
            probability=_coerce_float(
                value("probability", 1.0), "生效概率", minimum=0.0, maximum=1.0
            ),
            folder=folder,
            tags=tags,
            template=template,
            created_at=_normalise_timestamp(data.get("created_at"), now),
            updated_at=_normalise_timestamp(data.get("updated_at"), now),
        )
        return entry

    def _compile_matchers(self) -> None:
        self._matchers.clear()
        for keyword in self.keywords:
            if self.keyword_mode == "legacy_regex":
                self._matchers.append(("regex", _compile_regex(keyword, name=self.name)))
            elif keyword.casefold().startswith("re:"):
                source = keyword[3:].strip()
                if not source:
                    raise EntryValidationError(
                        f"条目“{self.name}”的正则关键词不能只有 re:"
                    )
                self._matchers.append(("regex", _compile_regex(source, name=self.name)))
            else:
                self._matchers.append(("literal", keyword.casefold()))

    @property
    def active(self) -> bool:
        """Whether this session-local copy is still valid for injection."""

        if self._activated_at is None:
            return False
        now = time.time()
        if self.duration > 0 and now >= self._activated_at + self.duration:
            return False
        return not (self.times > 0 and self._inject_count >= self.times)

    @property
    def remaining_seconds(self) -> int | None:
        if self._activated_at is None:
            return 0
        if self.duration == 0:
            return None
        return max(0, int(self._activated_at + self.duration - time.time()))

    @property
    def remaining_times(self) -> int | None:
        if self._activated_at is None:
            return 0
        if self.times == 0:
            return None
        return max(0, self.times - self._inject_count)

    @property
    def has_cron(self) -> bool:
        return bool(self.cron and len(self.cron.split()) == 5)

    def to_dict(self) -> dict[str, Any]:
        """Return only persistent data; activation state never survives a restart."""

        return {
            "id": self.id,
            "name": self.name,
            "content": self.content,
            "enabled": self.enabled,
            "priority": self.priority,
            "keywords": list(self.keywords),
            "keyword_mode": self.keyword_mode,
            "scope": list(self.scope),
            "cron": self.cron,
            "duration": self.duration,
            "times": self.times,
            "probability": self.probability,
            "folder": self.folder,
            "tags": list(self.tags),
            "template": self.template,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    def summary(self, *, preview_length: int = 180) -> dict[str, Any]:
        preview = " ".join(self.content.split())
        if len(preview) > preview_length:
            preview = preview[: preview_length - 1] + "…"
        return {
            "id": self.id,
            "name": self.name,
            "enabled": self.enabled,
            "priority": self.priority,
            "keywords": list(self.keywords),
            "keyword_mode": self.keyword_mode,
            "scope": list(self.scope),
            "cron": self.cron,
            "duration": self.duration,
            "times": self.times,
            "probability": self.probability,
            "folder": self.folder,
            "tags": list(self.tags),
            "template": self.template,
            "updated_at": self.updated_at,
            "preview": preview,
        }

    def clone_runtime(self) -> WorldTreeEntry:
        """Make an isolated session copy from the persistent definition."""

        return WorldTreeEntry.from_dict(self.to_dict())

    def with_runtime_from(self, previous: WorldTreeEntry) -> WorldTreeEntry:
        """Adopt a new definition without resetting an active session lifecycle."""

        clone = self.clone_runtime()
        clone._activated_at = previous._activated_at
        clone._inject_count = previous._inject_count
        return clone

    def enter_session(self) -> None:
        self._activated_at = time.time()
        self._inject_count = 0

    def consume(self) -> None:
        self._inject_count += 1

    def mark_cron_fired(self) -> None:
        self._cron_fired_at = time.time()

    def consume_cron_signal(self) -> None:
        """Close a pending schedule signal after a session accepts it."""

        self._cron_fired_at = None

    def _cron_window_open(self, now: float) -> bool:
        if self._cron_fired_at is None:
            return False
        # A zero-duration entry is allowed to remain active indefinitely once
        # triggered, but a cron signal itself must not remain pending forever.
        window = self.duration if self.duration > 0 else DEFAULT_CRON_WINDOW_SECONDS
        return now <= self._cron_fired_at + window

    def _scope_allows(self, ctx: ActivationContext) -> bool:
        if not self.scope:
            return True
        legacy_values = {ctx.user_id, ctx.group_id, ctx.session_id}
        for scope in self.scope:
            if scope == "admin" and ctx.is_admin:
                return True
            if scope in legacy_values:
                return True
            if scope.startswith("user:") and scope[5:] == ctx.user_id:
                return True
            if scope.startswith("group:") and scope[6:] == ctx.group_id:
                return True
            if scope.startswith("session:") and scope[8:] == ctx.session_id:
                return True
        return False

    def matches_text(self, text: str) -> bool:
        if not text or not self._matchers:
            return False
        text = text[:MAX_MATCH_TEXT_CHARS]
        folded = text.casefold()
        for kind, matcher in self._matchers:
            if kind == "literal" and isinstance(matcher, str) and matcher in folded:
                return True
            if kind == "regex" and isinstance(matcher, re.Pattern) and matcher.search(text):
                return True
        return False

    def try_activate(self, ctx: ActivationContext, text: str) -> bool:
        """Apply pure eligibility gates and consume one cron signal when used."""

        if not self.enabled or not self._scope_allows(ctx):
            return False
        now = time.time()
        text_hit = self.matches_text(text)
        cron_hit = self._cron_window_open(now)
        if not text_hit and not cron_hit:
            return False
        if self.probability <= 0:
            return False
        return not (
            self.probability < 1 and random.random() >= self.probability
        )

    def can_consume(self, ctx: ActivationContext) -> bool:
        return self.enabled and self.active and self._scope_allows(ctx)


def prepared_entry_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Merge a new entry payload with the selected template without mutation."""

    template, defaults = template_defaults(payload.get("template"))
    result = dict(defaults)
    result.update(payload)
    result["template"] = template
    return result
