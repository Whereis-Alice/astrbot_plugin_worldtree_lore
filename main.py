"""AstrBot entrypoint for WorldTree Lore.

WorldTree Lore is intentionally an independent plugin namespace. It can run
beside the upstream Worldbook plugin without sharing commands, data, Web APIs,
LLM tools, or configuration identifiers.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.provider import ProviderRequest
from astrbot.api.star import Context, Star, StarTools
from astrbot.api.web import (
    PluginUploadFile,
    error_response,
    file_response,
    json_response,
    request,
)
from astrbot.core.agent.message import TextPart

from .worldtree.files import MAX_IMPORT_BYTES, dump_entries, load_entries_from_bytes
from .worldtree.library import RevisionConflict, WorldTreeLibrary
from .worldtree.models import (
    CHARACTER_TEMPLATE,
    ENTRY_TEMPLATES,
    ActivationContext,
    EntryValidationError,
    WorldTreeEntry,
)
from .worldtree.rendering import render_content, standard_values
from .worldtree.scheduler import WorldTreeCronScheduler
from .worldtree.sessions import WorldTreeSessionStore

PLUGIN_NAME = "astrbot_plugin_worldtree_lore"
PAGE_ROUTE = f"/{PLUGIN_NAME}"
LLM_ENTRY_TOOL_NAME = "worldtree_lore_add_entry"
MAX_EXPORT_FILES = 12
#: Marks a turn whose activation already ran in the waiting hook, so the later
#: request hook must not roll probabilities or cron signals a second time.
TURN_RESOLVED_EXTRA = "worldtree_lore_turn_resolved"
#: The event extra AstrBot reads when choosing the provider for one turn.
SELECTED_PROVIDER_EXTRA = "selected_provider"
#: Opening/closing wrapper for reference lore: facts the reply may draw on.
LORE_WRAPPER = (
    (
        "<worldtree_lore_context>\n"
        "以下内容由机器人管理员维护，是本轮回复可参考的世界设定、规则或背景。"
        "请结合用户当前问题使用，不要把这段标签或原文当作需要向用户复述的内容。"
    ),
    "\n</worldtree_lore_context>",
)
#: Opening/closing wrapper for character entries: who the reply should be.
PERSONA_WRAPPER = (
    (
        "<worldtree_persona>\n"
        "以下是本轮回复应当扮演的角色设定。请以这个角色的身份、语气和说话习惯回应，"
        "不要复述或提及这段标签本身；除用户明确要求外不要跳出角色。"
    ),
    "\n</worldtree_persona>",
)


class WorldTreeLorePlugin(Star):
    """Searchable, session-safe worldbook context for AstrBot 4.26+."""

    @filter.command_group("世界树", alias={"世界树书", "worldtree"})
    def worldtree(self):
        """世界树命令组。"""

    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self._config = config
        self.library = WorldTreeLibrary(config)
        self.sessions = WorldTreeSessionStore()
        self.scheduler = WorldTreeCronScheduler(self.library, logger)
        self.library.on_changed.append(self._reconcile_sessions)
        self._mutation_lock = asyncio.Lock()
        self._data_dir: Path | None = None
        self._export_dir: Path | None = None

        context.register_web_api(
            f"{PAGE_ROUTE}/entries",
            self.web_entries,
            ["GET"],
            "List and search WorldTree Lore entries",
        )
        context.register_web_api(
            f"{PAGE_ROUTE}/entry/<entry_id>",
            self.web_entry,
            ["GET"],
            "Get one WorldTree Lore entry",
        )
        context.register_web_api(
            f"{PAGE_ROUTE}/entry/create",
            self.web_create_entry,
            ["POST"],
            "Create a WorldTree Lore entry",
        )
        context.register_web_api(
            f"{PAGE_ROUTE}/entry/<entry_id>/save",
            self.web_save_entry,
            ["POST"],
            "Update a WorldTree Lore entry",
        )
        context.register_web_api(
            f"{PAGE_ROUTE}/entry/<entry_id>/toggle",
            self.web_toggle_entry,
            ["POST"],
            "Toggle a WorldTree Lore entry",
        )
        context.register_web_api(
            f"{PAGE_ROUTE}/entry/<entry_id>/duplicate",
            self.web_duplicate_entry,
            ["POST"],
            "Duplicate a WorldTree Lore entry",
        )
        context.register_web_api(
            f"{PAGE_ROUTE}/entry/<entry_id>/delete",
            self.web_delete_entry,
            ["POST"],
            "Delete a WorldTree Lore entry",
        )
        context.register_web_api(
            f"{PAGE_ROUTE}/entries/bulk",
            self.web_bulk_entries,
            ["POST"],
            "Bulk update WorldTree Lore entries",
        )
        context.register_web_api(
            f"{PAGE_ROUTE}/import/<strategy>/<revision>",
            self.web_import_entries,
            ["POST"],
            "Import a WorldTree Lore file",
        )
        context.register_web_api(
            f"{PAGE_ROUTE}/export",
            self.web_export_entries,
            ["GET"],
            "Export WorldTree Lore entries",
        )
        context.register_web_api(
            f"{PAGE_ROUTE}/providers",
            self.web_providers,
            ["GET"],
            "List providers available for WorldTree Lore model overrides",
        )
        context.register_web_api(
            f"{PAGE_ROUTE}/diagnostics",
            self.web_diagnostics,
            ["GET"],
            "Get WorldTree Lore diagnostics",
        )

    async def initialize(self) -> None:
        """Load persisted entries, repair safe migrations, and start cron jobs."""

        self._data_dir = StarTools.get_data_dir(PLUGIN_NAME)
        self._export_dir = self._data_dir / "exports"
        self._export_dir.mkdir(parents=True, exist_ok=True)
        report = self.library.load()
        for message in report["messages"]:
            logger.warning("[worldtree] %s", message)
        self.scheduler.start()
        logger.info(
            "[worldtree] loaded %s entries (revision %s)",
            report["loaded"],
            report["revision"],
        )

    async def terminate(self) -> None:
        """Stop only this plugin's scheduler; persisted data is already saved."""

        self.scheduler.shutdown()

    # ------------------------------------------------------------------
    # LLM request hook
    # ------------------------------------------------------------------

    @filter.on_llm_request()
    async def on_llm_request(
        self, event: AstrMessageEvent, req: ProviderRequest
    ) -> None:
        """Resolve eligible lore as temporary request context for the current turn."""

        try:
            ctx = self._activation_context(event)
            self._filter_llm_entry_tool(req, ctx)
            if self._safe_extra(event, TURN_RESOLVED_EXTRA):
                # Activation already happened in the waiting hook for this turn.
                active = self.sessions.active_for(ctx)
            else:
                active = self._resolve_active(event, ctx)
            self._apply_model_override(req, active)
            persona, lore, injected = self._build_injection(event, active)
            if not persona and not lore:
                return

            target = str(
                self._config.get("injection_target", "extra_user_content")
            ).strip()
            persona_target = str(
                self._config.get("character_injection_target", "system_prompt")
            ).strip()
            if persona_target not in {"system_prompt", "extra_user_content"}:
                persona_target = target
            # The persona block goes first so the role definition is read before
            # the reference lore that supports it.
            if persona:
                self._write_payload(req, persona, persona_target)
            if lore:
                self._write_payload(req, lore, target)
            for entry in injected:
                entry.consume()
            logger.debug(
                "[worldtree] injected %d entry(s): %s",
                len(injected),
                ", ".join(entry.name for entry in injected),
            )
        except Exception:  # noqa: BLE001 - request hooks must not break normal replies
            # A malformed user event or one problematic entry must never stop
            # AstrBot's normal LLM request path.
            logger.exception("[worldtree] failed to prepare lore context")

    @filter.on_waiting_llm_request()
    async def on_waiting_llm_request(self, event: AstrMessageEvent) -> None:
        """Choose this turn's provider before AstrBot builds the agent.

        AstrBot picks the provider before the regular request hook runs, so a
        provider override has to be decided here. This hook must never stop the
        event and must never raise: a truthy return value or an exception would
        cancel the whole LLM request.
        """

        try:
            if not self._bool_config("enable_model_switch", True):
                return
            ctx = self._activation_context(event)
            active = self._resolve_active(event, ctx)
            self._mark_turn_resolved(event)
            provider_id = next(
                (entry.provider for entry in active if entry.provider), ""
            )
            if not provider_id:
                return
            provider = None
            try:
                provider = self.context.get_provider_by_id(provider_id)
            except Exception:  # noqa: BLE001 - provider lookups are third-party code
                logger.exception(
                    "[worldtree] 查询提供商 %s 时出错，本轮继续使用默认提供商",
                    provider_id,
                )
                return
            if provider is None:
                # Setting an unknown provider ID would make AstrBot fail the
                # whole request instead of silently falling back.
                logger.warning(
                    "[worldtree] 条目指定的提供商 %s 不存在，本轮继续使用默认提供商",
                    provider_id,
                )
                return
            event.set_extra(SELECTED_PROVIDER_EXTRA, provider_id)
            logger.debug("[worldtree] switched provider to %s for this turn", provider_id)
        except Exception:  # noqa: BLE001 - this hook must never break a reply
            logger.exception("[worldtree] failed to select a provider for this turn")

    @filter.llm_tool(name=LLM_ENTRY_TOOL_NAME)
    async def llm_add_entry(
        self,
        event: AstrMessageEvent,
        name: str,
        content: str,
        keywords: str = "",
    ) -> str:
        """Create one reviewable WorldTree Lore entry for an administrator.

        The tool is disabled by default. When an administrator explicitly
        enables it in plugin settings, the model may save compact reusable
        context. Created entries are grouped under "LLM 创建" so they are easy
        to audit in the WorldTree Console.

        Args:
            name(string): Unique entry name, no more than 80 characters.
            content(string): Lore, rule, preference, or background to inject.
            keywords(string): Optional trigger phrases separated by commas or
                new lines. Plain phrases are literal; prefix regex with re:.

        Returns:
            A plain text result describing whether the entry was created.
        """

        if not self._bool_config("enable_llm_entry_tool", False):
            return "世界树条目写入工具未启用；请由管理员在插件配置中开启。"
        ctx = self._activation_context(event)
        if not ctx.is_admin:
            return "拒绝写入：只有 AstrBot 管理员可以让模型创建世界树条目。"

        entry_name = str(name or "").strip()
        entry_content = str(content or "").strip()
        trigger_keywords = self._split_tool_keywords(keywords)
        if not trigger_keywords and entry_name:
            trigger_keywords = [entry_name]
        try:
            async with self._mutation_lock:
                entry = self.library.create(
                    {
                        "template": "common",
                        "name": entry_name,
                        "content": entry_content,
                        "keywords": trigger_keywords,
                        "keyword_mode": "modern",
                        "folder": "LLM 创建",
                        "tags": ["LLM 创建"],
                    }
                )
        except EntryValidationError as exc:
            return f"世界树条目创建失败：{exc}"
        except Exception:  # noqa: BLE001 - report unexpected persistence failures safely
            logger.exception("[worldtree] %s failed", LLM_ENTRY_TOOL_NAME)
            return "世界树条目创建失败：发生内部错误，请管理员查看日志。"
        return f"已创建世界树条目“{entry.name}”，请管理员在管理台中复核。"

    def _build_injection(
        self, event: AstrMessageEvent, entries: list[WorldTreeEntry]
    ) -> tuple[str, str, list[WorldTreeEntry]]:
        """Render active entries into a persona block and a lore block.

        Character entries describe who the bot *is*, so they are wrapped in their
        own block that can be delivered as a system prompt, while reference lore
        stays in the neutral context block. Both blocks share one budget.
        """

        max_count = self._int_config(
            "max_inject_count", 6, minimum=0, maximum=50
        )
        max_chars = self._int_config(
            "max_injected_chars", 12_000, minimum=500, maximum=100_000
        )
        if max_count == 0:
            return "", "", []

        persona_sections: list[str] = []
        lore_sections: list[str] = []
        used = 0
        selected: list[WorldTreeEntry] = []
        user_id = self._safe_event_value(event, "get_sender_id")
        user_name = self._safe_event_value(event, "get_sender_name")

        for entry in entries:
            if len(selected) >= max_count:
                break
            is_persona = entry.template == CHARACTER_TEMPLATE
            sections = persona_sections if is_persona else lore_sections
            wrapper = PERSONA_WRAPPER if is_persona else LORE_WRAPPER
            # The opening/closing tags are only paid for by the first entry that
            # actually needs that block.
            overhead = 0 if sections else len(wrapper[0]) + len(wrapper[1])
            rendered = render_content(
                entry.content,
                standard_values(
                    user_id=user_id,
                    user_name=user_name,
                    entry_name=entry.name,
                ),
            )
            section = f"\n\n### {entry.name}\n{rendered}"
            if used + overhead + len(section) > max_chars:
                logger.debug(
                    "[worldtree] skipped oversized entry %s for this request", entry.name
                )
                continue
            sections.append(section)
            used += overhead + len(section)
            selected.append(entry)

        if not selected:
            return "", "", []
        persona = (
            PERSONA_WRAPPER[0] + "".join(persona_sections) + PERSONA_WRAPPER[1]
            if persona_sections
            else ""
        )
        lore = (
            LORE_WRAPPER[0] + "".join(lore_sections) + LORE_WRAPPER[1]
            if lore_sections
            else ""
        )
        return persona, lore, selected

    # ------------------------------------------------------------------
    # Chat commands: all mutations have the world-tree namespace and admin
    # permission. The richer workflow intentionally lives in the plugin Page.
    # ------------------------------------------------------------------

    @worldtree.command("帮助", alias={"help"})
    async def command_help(self, event: AstrMessageEvent):
        """显示世界树的简要命令帮助。"""

        yield event.plain_result(
            "世界树\n"
            "完整字段编辑：在插件详情页打开「世界树管理台」。\n"
            "聊天命令可用于检索、快速添加、删除、开关和会话控制。\n\n"
            "管理员命令：\n"
            "- 世界树 查找 <关键词>\n"
            "- 世界树 查看 <名称>\n"
            "- 世界树 添加 <单词名称> <内容>\n"
            "- 世界树 删除 <名称>\n"
            "- 世界树 全局开关 <名称> <开|关>\n"
            "- 世界树 固定 <名称>（仅当前会话）\n"
            "- 世界树 终止 [名称或模板]（立刻结束生效中的条目）\n"
            "- 世界树 屏蔽 <名称>（仅当前会话）\n"
            "- 世界树 会话状态 / 清理"
        )

    @worldtree.command("查找", alias={"搜索"})
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def command_find(self, event: AstrMessageEvent):
        """按名称、内容、标签、文件夹或关键词检索条目。"""

        query = self._command_tail(event)
        if not query:
            yield event.plain_result("用法：世界树 查找 <关键词>")
            return
        result = self.library.paged_entries(query=query, page=1, page_size=20)
        entries = result["entries"]
        if not entries:
            yield event.plain_result(f"未找到包含“{query}”的条目")
            return
        lines = [f"找到 {result['pagination']['total']} 个条目（显示前 {len(entries)} 个）："]
        for entry in entries:
            status = "启用" if entry["enabled"] else "禁用"
            tags = f" · #{' #'.join(entry['tags'])}" if entry["tags"] else ""
            folder = f"[{entry['folder']}] " if entry["folder"] else ""
            lines.append(
                f"- {folder}{entry['name']}（{status}，优先级 {entry['priority']}）{tags}"
            )
        yield event.plain_result("\n".join(lines))

    @worldtree.command("查看")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def command_view(self, event: AstrMessageEvent):
        """查看一个条目的完整设置和内容。"""

        name = self._command_tail(event)
        if not name:
            yield event.plain_result("用法：世界树 查看 <名称>")
            return
        entry = self.library.get_by_name(name)
        if entry is None:
            yield event.plain_result(f"未找到条目“{name}”")
            return
        yield event.plain_result(self._format_entry(entry))

    @worldtree.command("添加")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def command_add(self, event: AstrMessageEvent):
        """快速添加条目；名称不含空格时最方便，复杂编辑请使用管理台。"""

        tail = self._command_tail(event)
        name, separator, content = tail.partition(" ")
        if not name or not separator or not content.strip():
            yield event.plain_result("用法：世界树 添加 <单词名称> <内容>")
            return
        try:
            async with self._mutation_lock:
                entry = self.library.create(
                    {"name": name, "content": content.strip(), "keywords": [name]}
                )
        except EntryValidationError as exc:
            yield event.plain_result(f"添加失败：{exc}")
            return
        yield event.plain_result(f"已添加条目“{entry.name}”。默认触发词为名称。")

    @worldtree.command("删除")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def command_delete(self, event: AstrMessageEvent):
        """删除指定全局条目。"""

        name = self._command_tail(event)
        if not name:
            yield event.plain_result("用法：世界树 删除 <名称>")
            return
        entry = self.library.get_by_name(name)
        if entry is None:
            yield event.plain_result(f"未找到条目“{name}”")
            return
        try:
            async with self._mutation_lock:
                self.library.delete(entry.id)
        except EntryValidationError as exc:
            yield event.plain_result(f"删除失败：{exc}")
            return
        yield event.plain_result(f"已删除条目“{entry.name}”。")

    @worldtree.command("全局开关", alias={"开关"})
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def command_toggle(self, event: AstrMessageEvent):
        """开启或关闭全局条目；不会误改其适用范围。"""

        tail = self._command_tail(event)
        name, separator, value = tail.rpartition(" ")
        if not name or not separator or value not in {"开", "关", "on", "off"}:
            yield event.plain_result("用法：世界树 全局开关 <名称> <开|关>")
            return
        entry = self.library.get_by_name(name)
        if entry is None:
            yield event.plain_result(f"未找到条目“{name}”")
            return
        enabled = value in {"开", "on"}
        try:
            async with self._mutation_lock:
                self.library.update(entry.id, {"enabled": enabled})
        except EntryValidationError as exc:
            yield event.plain_result(f"更新失败：{exc}")
            return
        yield event.plain_result(f"条目“{entry.name}”已{'开启' if enabled else '关闭'}。")

    @worldtree.command("固定")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def command_pin(self, event: AstrMessageEvent):
        """立即让一个条目在当前会话生效，不改变全局配置或 scope。"""

        name = self._command_tail(event)
        entry = self.library.get_by_name(name) if name else None
        if entry is None:
            yield event.plain_result("用法：世界树 固定 <名称>（名称必须存在）")
            return
        if self.sessions.pin(
            self._activation_context(event),
            entry,
            allow_same_priority=self._bool_config("allow_same_priority", True),
            exclusive_templates=self._exclusive_templates(),
        ):
            yield event.plain_result(f"已在当前会话固定“{entry.name}”。")
        else:
            yield event.plain_result("无法固定该条目：它可能已关闭或不允许在当前会话使用。")

    @worldtree.command("终止", alias={"停止", "熄灭"})
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def command_stop(self, event: AstrMessageEvent):
        """立刻结束当前会话中生效的条目，可按名称或模板缩小范围。"""

        ctx = self._activation_context(event)
        active, _ = self.sessions.status(ctx.session_id)
        if not active:
            yield event.plain_result("当前会话没有生效中的条目。")
            return

        target = self._command_tail(event)
        if not target:
            doomed = list(active)
            label = "全部条目"
        else:
            entry = self.library.get_by_name(target)
            if entry is not None:
                doomed = [item for item in active if item.id == entry.id]
                label = f"“{entry.name}”"
            else:
                template_key = self._resolve_template_alias(target)
                if template_key is None:
                    yield event.plain_result(
                        "用法：世界树 终止 [名称或模板]\n"
                        "「世界树 终止」结束当前会话的全部条目；"
                        "「世界树 终止 常规」只结束常规条目。"
                    )
                    return
                doomed = [item for item in active if item.template == template_key]
                label = self._template_label(template_key)

        if not doomed:
            yield event.plain_result(f"{label}当前没有生效中的条目。")
            return

        removed = set(
            self.sessions.deactivate(ctx.session_id, [item.id for item in doomed])
        )
        stopped = [item for item in doomed if item.id in removed]
        names = "、".join(item.name for item in stopped) or label
        # A catch-all trigger re-activates on the very next message, so say so
        # instead of letting the admin think the command failed.
        catch_all = [item.name for item in stopped if "re:.*" in item.keywords]
        tail = ""
        if catch_all:
            tail = (
                "\n注意：" + "、".join(catch_all) + " 是全量触发的条目，"
                "下一条消息会立刻重新生效；要彻底挡住请用「世界树 屏蔽 <名称>」。"
            )
        yield event.plain_result(f"已在当前会话终止 {len(stopped)} 个条目：{names}。{tail}")

    @worldtree.command("屏蔽")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def command_block(self, event: AstrMessageEvent):
        """在当前会话临时屏蔽一个条目，不改变全局配置。"""

        name = self._command_tail(event)
        entry = self.library.get_by_name(name) if name else None
        if entry is None:
            yield event.plain_result("用法：世界树 屏蔽 <名称>（名称必须存在）")
            return
        self.sessions.block(event.unified_msg_origin, [entry.id])
        yield event.plain_result(f"当前会话已屏蔽“{entry.name}”。重载插件后会恢复。")

    @worldtree.command("解除屏蔽")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def command_unblock(self, event: AstrMessageEvent):
        """取消当前会话对一个条目的临时屏蔽。"""

        name = self._command_tail(event)
        entry = self.library.get_by_name(name) if name else None
        if entry is None:
            yield event.plain_result("用法：世界树 解除屏蔽 <名称>（名称必须存在）")
            return
        changed = self.sessions.unblock(event.unified_msg_origin, [entry.id])
        yield event.plain_result(
            f"已取消屏蔽“{entry.name}”。" if changed else f"“{entry.name}”当前未被屏蔽。"
        )

    @worldtree.command("会话状态")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def command_session_status(self, event: AstrMessageEvent):
        """查看当前会话中已激活和临时屏蔽的条目。"""

        active, blocked_ids = self.sessions.status(event.unified_msg_origin)
        names = {entry.id: entry.name for entry in self.library.entries}
        lines = ["当前会话状态："]
        if active:
            lines.append("生效中：")
            for entry in active:
                seconds = "永久" if entry.remaining_seconds is None else f"剩余 {entry.remaining_seconds} 秒"
                uses = "不限" if entry.remaining_times is None else f"剩余 {entry.remaining_times} 次"
                marks = [seconds, uses]
                if entry.model:
                    marks.append(f"模型 {entry.model}")
                if entry.provider:
                    marks.append(f"提供商 {entry.provider}")
                lines.append(
                    f"- {entry.name}［{self._template_label(entry.template)}］"
                    f"（{'，'.join(marks)}）"
                )
        else:
            lines.append("生效中：无")
        if blocked_ids:
            lines.append("已屏蔽：" + "、".join(names.get(item, item) for item in blocked_ids))
        yield event.plain_result("\n".join(lines))

    @worldtree.command("清理")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def command_session_clear(self, event: AstrMessageEvent):
        """清理当前会话已激活的条目；“全部”也会清除临时屏蔽。"""

        include_blocks = self._command_tail(event) == "全部"
        self.sessions.clear(event.unified_msg_origin, include_blocks=include_blocks)
        yield event.plain_result(
            "已清理当前会话的激活条目和临时屏蔽。"
            if include_blocks
            else "已清理当前会话的激活条目。"
        )

    # ------------------------------------------------------------------
    # Plugin Page APIs
    # ------------------------------------------------------------------

    async def web_entries(self):
        """Return a paginated server-side search result for the management Page."""

        try:
            return json_response(
                self.library.paged_entries(
                    page=request.query.get("page", 1, type=int),
                    page_size=request.query.get("page_size", 30, type=int),
                    query=request.query.get("q", ""),
                    status=request.query.get("status", "all"),
                    folder=request.query.get("folder", ""),
                    tag=request.query.get("tag", ""),
                    template=request.query.get("template", ""),
                    sort=request.query.get("sort", "priority"),
                )
            )
        except EntryValidationError as exc:
            return error_response(str(exc))

    async def web_entry(self, entry_id: str):
        entry = self.library.get(entry_id)
        if entry is None:
            return error_response("找不到该条目", status_code=404)
        return json_response(
            {
                "revision": self.library.revision,
                "entry": entry.to_dict(),
                "templates": self.library.templates(),
            }
        )

    async def web_create_entry(self):
        payload = await self._json_payload()
        if isinstance(payload, Exception):
            return error_response(str(payload))
        try:
            entry_payload = payload.get("entry", payload)
            if not isinstance(entry_payload, dict):
                raise EntryValidationError("entry 必须是对象")
            async with self._mutation_lock:
                entry = self.library.create(
                    entry_payload,
                    expected_revision=self._payload_revision(payload),
                )
            return json_response({"revision": self.library.revision, "entry": entry.to_dict()})
        except RevisionConflict as exc:
            return error_response(str(exc), status_code=409)
        except EntryValidationError as exc:
            return error_response(str(exc))

    async def web_save_entry(self, entry_id: str):
        payload = await self._json_payload()
        if isinstance(payload, Exception):
            return error_response(str(payload))
        try:
            entry_payload = payload.get("entry", payload)
            if not isinstance(entry_payload, dict):
                raise EntryValidationError("entry 必须是对象")
            async with self._mutation_lock:
                entry = self.library.update(
                    entry_id,
                    entry_payload,
                    expected_revision=self._payload_revision(payload),
                )
            return json_response({"revision": self.library.revision, "entry": entry.to_dict()})
        except RevisionConflict as exc:
            return error_response(str(exc), status_code=409)
        except EntryValidationError as exc:
            return error_response(str(exc), status_code=404 if "找不到" in str(exc) else 400)

    async def web_toggle_entry(self, entry_id: str):
        payload = await self._json_payload()
        if isinstance(payload, Exception):
            return error_response(str(payload))
        enabled = payload.get("enabled")
        if not isinstance(enabled, bool):
            return error_response("enabled 必须是布尔值")
        try:
            async with self._mutation_lock:
                entry = self.library.update(
                    entry_id,
                    {"enabled": enabled},
                    expected_revision=self._payload_revision(payload),
                )
            return json_response({"revision": self.library.revision, "entry": entry.summary()})
        except RevisionConflict as exc:
            return error_response(str(exc), status_code=409)
        except EntryValidationError as exc:
            return error_response(str(exc), status_code=404 if "找不到" in str(exc) else 400)

    async def web_duplicate_entry(self, entry_id: str):
        payload = await self._json_payload()
        if isinstance(payload, Exception):
            return error_response(str(payload))
        try:
            async with self._mutation_lock:
                entry = self.library.duplicate(
                    entry_id, expected_revision=self._payload_revision(payload)
                )
            return json_response({"revision": self.library.revision, "entry": entry.to_dict()})
        except RevisionConflict as exc:
            return error_response(str(exc), status_code=409)
        except EntryValidationError as exc:
            return error_response(str(exc), status_code=404 if "找不到" in str(exc) else 400)

    async def web_delete_entry(self, entry_id: str):
        payload = await self._json_payload()
        if isinstance(payload, Exception):
            return error_response(str(payload))
        try:
            async with self._mutation_lock:
                deleted = self.library.delete(
                    entry_id, expected_revision=self._payload_revision(payload)
                )
            return json_response(
                {"revision": self.library.revision, "deleted": deleted.summary()}
            )
        except RevisionConflict as exc:
            return error_response(str(exc), status_code=409)
        except EntryValidationError as exc:
            return error_response(str(exc), status_code=404 if "找不到" in str(exc) else 400)

    async def web_bulk_entries(self):
        payload = await self._json_payload()
        if isinstance(payload, Exception):
            return error_response(str(payload))
        entry_ids = payload.get("entry_ids")
        if not isinstance(entry_ids, list):
            return error_response("entry_ids 必须是列表")
        try:
            async with self._mutation_lock:
                result = self.library.bulk(
                    entry_ids,
                    action=str(payload.get("action", "")),
                    value=payload.get("value"),
                    expected_revision=self._payload_revision(payload),
                )
            return json_response(result)
        except RevisionConflict as exc:
            return error_response(str(exc), status_code=409)
        except EntryValidationError as exc:
            return error_response(str(exc))

    async def web_import_entries(self, strategy: str, revision: str):
        try:
            expected_revision = int(revision)
        except ValueError:
            return error_response("无效的条目库版本")
        files = await request.files()
        upload = files.get("file")
        if not isinstance(upload, PluginUploadFile):
            return error_response("请选择要导入的文件")
        filename = upload.filename or "import.yaml"
        if upload.content_length is not None and upload.content_length > MAX_IMPORT_BYTES:
            return error_response("导入文件不能超过 2 MiB")
        try:
            data = await upload.read(MAX_IMPORT_BYTES + 1)
            entries = load_entries_from_bytes(data, filename)
            async with self._mutation_lock:
                report = self.library.import_entries(
                    entries,
                    strategy=strategy,
                    expected_revision=expected_revision,
                )
            return json_response({"revision": self.library.revision, "report": report.as_dict()})
        except RevisionConflict as exc:
            return error_response(str(exc), status_code=409)
        except EntryValidationError as exc:
            return error_response(str(exc))
        except Exception:  # noqa: BLE001 - untrusted uploads must return a safe response
            logger.exception("[worldtree] import failed")
            return error_response("导入失败：文件内容无法处理")
        finally:
            await upload.close()

    async def web_export_entries(self):
        export_format = request.query.get("format", "yaml").casefold()
        # An empty "ids" query means "export the whole library"; a non-empty one
        # narrows the backup down to the entries selected in the Page.
        wanted_ids = [item for item in request.query.get("ids", "").split(",") if item.strip()]
        entries = self.library.entries
        if wanted_ids:
            selected = set(wanted_ids)
            entries = [entry for entry in entries if entry.id in selected]
            if not entries:
                return error_response("所选条目已不存在，请刷新后重试", status_code=404)
        try:
            data = dump_entries(entries, export_format)
        except EntryValidationError as exc:
            return error_response(str(exc))
        suffix = "json" if export_format == "json" else "yaml"
        export_dir = self._ensure_export_dir()
        self._prune_export_files(export_dir)
        path = export_dir / f"worldtree-lore-{int(time.time())}-{uuid4().hex[:8]}.{suffix}"
        path.write_bytes(data)
        content_type = "application/json" if suffix == "json" else "application/x-yaml"
        return file_response(path, filename=path.name, content_type=content_type)

    async def web_providers(self):
        """List provider IDs so the Page can offer a picker instead of free text.

        Provider metadata is read from the running AstrBot instance only; no
        remote model listing is performed, so this stays fast and offline-safe.
        """

        providers: list[dict[str, str]] = []
        try:
            candidates = self.context.get_all_providers()
        except Exception:  # noqa: BLE001 - provider registries are third-party code
            logger.exception("[worldtree] failed to list providers")
            candidates = []
        for provider in candidates or []:
            try:
                meta = provider.meta()
            except Exception:  # noqa: BLE001 - one broken provider must not hide the rest
                logger.debug("[worldtree] provider metadata unavailable", exc_info=True)
                continue
            provider_id = str(getattr(meta, "id", "") or "")
            if not provider_id:
                continue
            providers.append(
                {
                    "id": provider_id,
                    "model": str(getattr(meta, "model", "") or ""),
                    "type": str(getattr(meta, "type", "") or ""),
                }
            )
        providers.sort(key=lambda item: item["id"].casefold())
        return json_response({"providers": providers})

    async def web_diagnostics(self):
        return json_response(
            {
                "revision": self.library.revision,
                "stats": self.library.stats(),
                "invalid_cron_entries": self.scheduler.invalid_entries,
                "injection_target": self._config.get(
                    "injection_target", "extra_user_content"
                ),
            }
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _reconcile_sessions(self) -> None:
        self.sessions.reconcile(self.library.entries)

    @staticmethod
    def _safe_event_value(event: AstrMessageEvent, method_name: str) -> str:
        try:
            value = getattr(event, method_name)()
        except Exception:  # noqa: BLE001 - adapter implementations may raise arbitrary errors
            return ""
        return "" if value is None else str(value)

    def _activation_context(self, event: AstrMessageEvent) -> ActivationContext:
        try:
            is_admin = bool(event.is_admin())
        except Exception:  # noqa: BLE001 - adapter implementations may raise arbitrary errors
            is_admin = False
        return ActivationContext.from_values(
            user_id=self._safe_event_value(event, "get_sender_id"),
            group_id=self._safe_event_value(event, "get_group_id"),
            session_id=getattr(event, "unified_msg_origin", ""),
            is_admin=is_admin,
        )

    def _exclusive_templates(self) -> frozenset[str]:
        """Templates where only one entry may hold a session at a time."""

        if self._bool_config("single_character_entry", True):
            return frozenset({CHARACTER_TEMPLATE})
        return frozenset()

    def _resolve_active(
        self, event: AstrMessageEvent, ctx: ActivationContext
    ) -> list[WorldTreeEntry]:
        """Run keyword/cron activation for this turn and return what is live."""

        candidates = self.library.activation_candidates(ctx, event.message_str or "")
        self.sessions.activate(
            ctx,
            candidates,
            allow_same_priority=self._bool_config("allow_same_priority", True),
            exclusive_templates=self._exclusive_templates(),
        )
        idle_minutes = self._int_config(
            "session_idle_minutes", 1_440, minimum=5, maximum=43_200
        )
        self.sessions.prune_idle(max_idle_seconds=idle_minutes * 60)
        return self.sessions.active_for(ctx)

    def _mark_turn_resolved(self, event: AstrMessageEvent) -> None:
        try:
            event.set_extra(TURN_RESOLVED_EXTRA, True)
        except Exception:  # noqa: BLE001 - adapter implementations may raise
            logger.debug("[worldtree] could not mark this turn as resolved")

    @staticmethod
    def _safe_extra(event: AstrMessageEvent, key: str) -> Any:
        try:
            return event.get_extra(key)
        except Exception:  # noqa: BLE001 - adapter implementations may raise
            return None

    def _apply_model_override(
        self, req: ProviderRequest, entries: list[WorldTreeEntry]
    ) -> None:
        """Let the highest-priority active entry pick the model for this turn.

        Only the model name is overridden here; the provider itself is chosen in
        the waiting hook because AstrBot resolves it earlier in the pipeline.
        """

        if not self._bool_config("enable_model_switch", True):
            return
        model = next((entry.model for entry in entries if entry.model), "")
        if not model:
            return
        req.model = model
        logger.debug("[worldtree] switched model to %s for this turn", model)

    @staticmethod
    def _write_payload(req: ProviderRequest, payload: str, target: str) -> None:
        """Attach one rendered block through the configured delivery channel."""

        if target == "system_prompt":
            system_prompt = str(req.system_prompt or "").rstrip()
            req.system_prompt = f"{system_prompt}\n\n{payload}".strip()
            return
        # On AstrBot >=4.24.2 this remains provider-facing for the current turn
        # but is not written into conversation history.
        req.extra_user_content_parts.append(TextPart(text=payload).mark_as_temp())

    def _filter_llm_entry_tool(
        self,
        req: ProviderRequest,
        ctx: ActivationContext,
    ) -> None:
        """Hide the mutation tool unless both config and caller permit it."""

        if self._bool_config("enable_llm_entry_tool", False) and ctx.is_admin:
            return
        tool_set = getattr(req, "func_tool", None)
        if tool_set is not None and hasattr(tool_set, "remove_tool"):
            tool_set.remove_tool(LLM_ENTRY_TOOL_NAME)

    @staticmethod
    def _split_tool_keywords(value: Any) -> list[str]:
        source = str(value or "").replace("，", ",").replace("\r", "\n")
        result: list[str] = []
        seen: set[str] = set()
        for line in source.split("\n"):
            for part in line.split(","):
                keyword = part.strip()
                if not keyword or keyword in seen:
                    continue
                seen.add(keyword)
                result.append(keyword)
        return result

    @staticmethod
    def _resolve_template_alias(value: str) -> str | None:
        """Map a template key, its label, or a short alias to a template key."""

        text = value.strip().casefold()
        if not text:
            return None
        extra_aliases = {
            "角色卡": CHARACTER_TEMPLATE,
            "人设": CHARACTER_TEMPLATE,
            "人格": CHARACTER_TEMPLATE,
        }
        if text in extra_aliases:
            return extra_aliases[text]
        for key, template in ENTRY_TEMPLATES.items():
            aliases = {key.casefold(), template.label.casefold()}
            if template.label.endswith("条目"):
                aliases.add(template.label[:-2].casefold())
            if text in aliases:
                return key
        return None

    @staticmethod
    def _command_tail(event: AstrMessageEvent) -> str:
        parts = (event.message_str or "").strip().split(maxsplit=2)
        return parts[2].strip() if len(parts) >= 3 else ""

    @staticmethod
    def _template_label(template: str) -> str:
        found = ENTRY_TEMPLATES.get(template)
        return found.label if found else (template or "常规条目")

    @staticmethod
    def _format_entry(entry: WorldTreeEntry) -> str:
        scope = "不限" if not entry.scope else "、".join(entry.scope)
        keywords = "、".join(entry.keywords) if entry.keywords else "无（仅 Cron 可触发）"
        preview = entry.content
        if len(preview) > 3_500:
            preview = preview[:3_500] + "\n…（内容较长，请在管理台查看和编辑）"
        return (
            f"【{entry.name}】{'启用' if entry.enabled else '禁用'}\n"
            f"模板：{WorldTreeLorePlugin._template_label(entry.template)}\n"
            f"优先级：{entry.priority}｜文件夹：{entry.folder or '未分类'}\n"
            f"标签：{'、'.join(entry.tags) or '无'}\n"
            f"关键词：{keywords}\n"
            f"范围：{scope}\n"
            f"Cron：{entry.cron or '无'}｜时长：{entry.duration or '永久'} 秒｜次数：{entry.times or '不限'}\n"
            f"概率：{entry.probability:.0%}\n"
            f"模型覆盖：{entry.model or '默认'}｜提供商覆盖：{entry.provider or '默认'}\n\n"
            f"内容：\n{preview}"
        )

    async def _json_payload(self) -> dict[str, Any] | Exception:
        payload = await request.json(default=None)
        if not isinstance(payload, dict):
            return EntryValidationError("请求体必须是 JSON 对象")
        return payload

    @staticmethod
    def _payload_revision(payload: dict[str, Any]) -> int | None:
        value = payload.get("revision")
        if value is None:
            return None
        if isinstance(value, bool):
            raise EntryValidationError("revision 必须是整数")
        try:
            return int(value)
        except (TypeError, ValueError) as exc:
            raise EntryValidationError("revision 必须是整数") from exc

    def _int_config(self, key: str, default: int, *, minimum: int, maximum: int) -> int:
        try:
            value = int(self._config.get(key, default))
        except (TypeError, ValueError):
            return default
        return min(maximum, max(minimum, value))

    def _bool_config(self, key: str, default: bool) -> bool:
        value = self._config.get(key, default)
        return value if isinstance(value, bool) else default

    def _ensure_export_dir(self) -> Path:
        if self._export_dir is None:
            self._data_dir = StarTools.get_data_dir(PLUGIN_NAME)
            self._export_dir = self._data_dir / "exports"
            self._export_dir.mkdir(parents=True, exist_ok=True)
        return self._export_dir

    @staticmethod
    def _prune_export_files(export_dir: Path) -> None:
        """Keep generated downloads bounded without touching user imports."""

        try:
            files = sorted(
                (
                    path
                    for path in export_dir.iterdir()
                    if path.is_file()
                    and path.name.startswith("worldtree-lore-")
                    and path.suffix.casefold() in {".json", ".yaml", ".yml"}
                ),
                key=lambda path: path.stat().st_mtime,
                reverse=True,
            )
            for stale in files[MAX_EXPORT_FILES - 1 :]:
                stale.unlink(missing_ok=True)
        except OSError as exc:
            logger.warning("[worldtree] unable to prune old exports: %s", exc)
