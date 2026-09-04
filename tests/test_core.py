from __future__ import annotations

import json
import unittest

from worldtree.files import dump_entries, load_entries_from_bytes
from worldtree.library import RevisionConflict, WorldTreeLibrary
from worldtree.models import (
    ActivationContext,
    EntryValidationError,
    WorldTreeEntry,
)
from worldtree.rendering import render_content, standard_values
from worldtree.scheduler import _normalise_weekday_field
from worldtree.sessions import WorldTreeSessionStore


class FakeConfig(dict):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.save_calls = 0

    def save_config(self) -> None:
        self.save_calls += 1


def make_entry(**overrides) -> WorldTreeEntry:
    payload = {
        "name": "雾港档案",
        "content": "雾港被盐雾与钟声包围。",
        "keywords": ["雾港"],
    }
    payload.update(overrides)
    return WorldTreeEntry.from_dict(payload)


class EntryModelTests(unittest.TestCase):
    def test_modern_keywords_are_literal_unless_prefixed(self) -> None:
        literal = make_entry(name="字面量", keywords=["a.b"])
        self.assertTrue(literal.matches_text("请查找 a.b"))
        self.assertFalse(literal.matches_text("请查找 axb"))

        explicit_regex = make_entry(name="显式正则", keywords=["re:a.b"])
        self.assertTrue(explicit_regex.matches_text("请查找 axb"))

        legacy_regex = make_entry(
            name="旧格式正则",
            keywords=["a.b"],
            keyword_mode="legacy_regex",
        )
        self.assertTrue(legacy_regex.matches_text("请查找 axb"))

    def test_invalid_regex_is_rejected_during_validation(self) -> None:
        with self.assertRaises(EntryValidationError):
            make_entry(name="坏正则", keywords=["re:(未闭合"])

    def test_placeholder_renderer_only_replaces_known_names(self) -> None:
        values = standard_values(user_id="42", user_name="Alice", entry_name="规则")
        rendered = render_content(
            "{user} / {user_id} / {entry_name} / {unknown}",
            values,
        )
        self.assertIn("Alice(42) / 42 / 规则", rendered)
        self.assertTrue(rendered.endswith("{unknown}"))


class SessionStoreTests(unittest.TestCase):
    def test_repeated_match_does_not_reset_lifetime_or_use_count(self) -> None:
        definition = make_entry(duration=600, times=3)
        ctx = ActivationContext(session_id="session-a")
        store = WorldTreeSessionStore()

        self.assertTrue(definition.try_activate(ctx, "雾港"))
        self.assertEqual(store.activate(ctx, [definition], allow_same_priority=True), [definition.id])
        active = store.active_for(ctx)[0]
        active.consume()
        activated_at = active._activated_at

        self.assertTrue(definition.try_activate(ctx, "再次提到雾港"))
        self.assertEqual(store.activate(ctx, [definition], allow_same_priority=True), [])
        active_again = store.active_for(ctx)[0]
        self.assertEqual(active_again._activated_at, activated_at)
        self.assertEqual(active_again.remaining_times, 2)

    def test_blocked_session_does_not_consume_global_cron_signal(self) -> None:
        definition = make_entry(keywords=[], cron="* * * * *")
        blocked_ctx = ActivationContext(session_id="blocked")
        allowed_ctx = ActivationContext(session_id="allowed")
        later_ctx = ActivationContext(session_id="later")
        store = WorldTreeSessionStore()
        store.block(blocked_ctx.session_id, [definition.id])
        definition.mark_cron_fired()

        self.assertTrue(definition.try_activate(blocked_ctx, ""))
        self.assertEqual(store.activate(blocked_ctx, [definition], allow_same_priority=True), [])
        self.assertTrue(definition.try_activate(allowed_ctx, ""))
        self.assertEqual(store.activate(allowed_ctx, [definition], allow_same_priority=True), [definition.id])
        self.assertFalse(definition.try_activate(later_ctx, ""))

    def test_reconcile_removes_deleted_runtime_and_stale_blocks(self) -> None:
        definition = make_entry()
        ctx = ActivationContext(session_id="session-b")
        store = WorldTreeSessionStore()
        store.block(ctx.session_id, [definition.id])
        store.reconcile([])
        active, blocked = store.status(ctx.session_id)
        self.assertEqual(active, [])
        self.assertEqual(blocked, set())


class LibraryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = FakeConfig(
            entry_storage=[],
            library_revision=0,
            data_version=2,
        )
        self.library = WorldTreeLibrary(self.config)
        self.library.load()

    def test_search_facets_bulk_change_and_revision_conflict(self) -> None:
        first = self.library.create(
            {
                "name": "雾港档案",
                "content": "海港城市设定",
                "keywords": ["雾港"],
                "folder": "地点",
                "tags": ["主线"],
            }
        )
        self.library.create(
            {
                "name": "白猫",
                "content": "旧码头的随机彩蛋",
                "keywords": ["白猫"],
                "folder": "彩蛋",
                "enabled": False,
            }
        )

        result = self.library.paged_entries(query="海港 主线", folder="地点")
        self.assertEqual([item["name"] for item in result["entries"]], ["雾港档案"])
        self.assertEqual(set(result["facets"]["folders"]), {"彩蛋", "地点"})

        stale_revision = self.library.revision - 1
        with self.assertRaises(RevisionConflict):
            self.library.update(
                first.id,
                {"content": "不应覆盖"},
                expected_revision=stale_revision,
            )

        changed = self.library.bulk(
            [first.id],
            action="add_tag",
            value="城市",
            expected_revision=self.library.revision,
        )
        self.assertEqual(changed["changed"], [first.id])
        self.assertIn("城市", self.library.get(first.id).tags)
        self.assertGreaterEqual(self.config.save_calls, 3)

    def test_sort_modes_cover_every_page_ordering_option(self) -> None:
        # Ordering happens on the backend so a large library never has to be
        # shipped to the browser only to be re-sorted there.
        trunk = self.library.create(
            {"name": "b 主干", "content": "x", "keywords": ["b"], "priority": 40, "folder": "地点", "template": "common"}
        )
        core = self.library.create(
            {"name": "a 树心", "content": "x", "keywords": ["a"], "priority": 5, "folder": "", "template": "resident"}
        )
        leaf = self.library.create(
            {
                "name": "c 新叶",
                "content": "x",
                "keywords": [],
                "priority": 120,
                "folder": "日程",
                "template": "schedule",
                "cron": "0 9 * * 1-5",
                "enabled": False,
            }
        )

        # All three are created within the same second, so give them distinct
        # timestamps to make the "recently updated" ordering observable.
        core.updated_at = 1_000
        trunk.updated_at = 2_000
        leaf.updated_at = 3_000

        def names(mode: str) -> list[str]:
            return [item["name"] for item in self.library.paged_entries(sort=mode)["entries"]]

        self.assertEqual(names("priority"), ["a 树心", "b 主干", "c 新叶"])
        self.assertEqual(names("priority_desc"), ["c 新叶", "b 主干", "a 树心"])
        self.assertEqual(names("name"), ["a 树心", "b 主干", "c 新叶"])
        # common precedes resident precedes schedule in template declaration order.
        self.assertEqual(names("template"), ["b 主干", "a 树心", "c 新叶"])
        # Unfiled entries sort last instead of first despite their empty folder.
        self.assertEqual(names("folder")[-1], "a 树心")
        self.assertEqual(names("enabled")[-1], "c 新叶")
        self.assertEqual(names("updated")[0], "c 新叶")
        self.assertEqual(self.library.paged_entries(sort="name")["pagination"]["sort"], "name")

    def test_unknown_sort_mode_is_rejected_with_a_readable_message(self) -> None:
        with self.assertRaises(EntryValidationError) as caught:
            self.library.paged_entries(sort="随便排")
        self.assertIn("排序", str(caught.exception))

    def test_status_and_template_filters_narrow_the_listing(self) -> None:
        self.library.create(
            {"name": "群规", "content": "x", "keywords": [], "scope": ["group:1"], "template": "group"}
        )
        self.library.create(
            {"name": "晨会", "content": "x", "keywords": [], "cron": "0 9 * * *", "template": "schedule"}
        )
        self.library.create({"name": "散条目", "content": "x", "keywords": ["k"], "template": "common"})

        scoped = self.library.paged_entries(status="scoped")
        self.assertEqual([item["name"] for item in scoped["entries"]], ["群规"])
        scheduled = self.library.paged_entries(status="scheduled")
        self.assertEqual([item["name"] for item in scheduled["entries"]], ["晨会"])
        by_template = self.library.paged_entries(template="common")
        self.assertEqual([item["name"] for item in by_template["entries"]], ["散条目"])
        self.assertEqual(self.library.stats()["scoped"], 1)

        with self.assertRaises(EntryValidationError):
            self.library.paged_entries(template="不存在的模板")

    def test_duplicate_creates_an_independent_copy_with_a_unique_name(self) -> None:
        source = self.library.create(
            {"name": "雾港档案", "content": "原内容", "keywords": ["雾港"], "tags": ["主线"]}
        )
        copy = self.library.duplicate(source.id, expected_revision=self.library.revision)

        self.assertEqual(copy.name, "雾港档案 副本")
        self.assertNotEqual(copy.id, source.id)
        self.assertEqual(copy.content, source.content)
        self.assertEqual(self.library.stats()["total"], 2)

        again = self.library.duplicate(source.id, expected_revision=self.library.revision)
        self.assertNotEqual(again.name, copy.name)

        with self.assertRaises(RevisionConflict):
            self.library.duplicate(source.id, expected_revision=self.library.revision - 1)

    def test_bulk_modernise_keywords_only_touches_legacy_entries(self) -> None:
        legacy = self.library.create(
            {
                "name": "旧常驻",
                "content": "常驻内容",
                "keywords": [".*"],
                "keyword_mode": "legacy_regex",
            }
        )
        modern = self.library.create(
            {"name": "雾港档案", "content": "海港设定", "keywords": ["雾港"]}
        )
        result = self.library.bulk(
            [legacy.id, modern.id],
            action="modernise_keywords",
            expected_revision=self.library.revision,
        )
        self.assertEqual(result["changed"], [legacy.id])
        rewritten = self.library.get(legacy.id)
        assert rewritten is not None
        self.assertEqual(rewritten.keyword_mode, "modern")
        self.assertEqual(rewritten.keywords, ["re:.*"])
        self.assertTrue(rewritten.matches_text("任何一句话"))
        untouched = self.library.get(modern.id)
        assert untouched is not None
        self.assertEqual(untouched.keywords, ["雾港"])

    def test_stored_legacy_entries_are_folded_onto_one_convention_once(self) -> None:
        config = FakeConfig(
            entry_storage=[
                {
                    "id": "legacyentry01",
                    "name": "旧常驻",
                    "content": "常驻内容",
                    "keywords": [".*", "re:foo"],
                    "keyword_mode": "legacy_regex",
                }
            ],
            library_revision=4,
            data_version=2,
        )
        library = WorldTreeLibrary(config)
        report = library.load()
        entry = library.list_entries()[0]
        self.assertEqual(entry.keyword_mode, "modern")
        self.assertEqual(entry.keywords, ["re:.*", "re:re:foo"])
        self.assertTrue(entry.matches_text("任何一句话"))
        self.assertEqual(config["data_version"], 3)
        self.assertTrue(any("re:" in message for message in report["messages"]))

        # the rewrite is a one-off migration, so a legacy entry chosen on purpose
        # afterwards must survive the next load untouched
        config["entry_storage"].append(
            {
                "id": "legacyentry02",
                "name": "手选旧模式",
                "content": "内容",
                "keywords": ["a.b"],
                "keyword_mode": "legacy_regex",
            }
        )
        reloaded = WorldTreeLibrary(config)
        reloaded.load()
        kept = reloaded.get("legacyentry02")
        assert kept is not None
        self.assertEqual(kept.keyword_mode, "legacy_regex")
        self.assertEqual(kept.keywords, ["a.b"])

    def test_import_rename_strategy_keeps_both_entries(self) -> None:
        self.library.create({"name": "同名", "content": "原内容", "keywords": ["原"]})
        report = self.library.import_entries(
            [{"name": "同名", "content": "导入内容", "keywords": ["新"]}],
            strategy="rename",
            expected_revision=self.library.revision,
        )
        self.assertEqual(report.added, 1)
        self.assertEqual(report.renamed, 1)
        self.assertEqual(
            [entry.name for entry in self.library.entries],
            ["同名", "同名 (2)"],
        )


class InterchangeAndSchedulerTests(unittest.TestCase):
    def test_upstream_import_rewrites_regex_keywords_without_changing_matches(self) -> None:
        source = {
            "entries": [
                {
                    "__template_key": "common",
                    "name": "旧世界书",
                    "content": "兼容内容",
                    "keywords": ["a.b", "re:foo"],
                }
            ]
        }
        rows = load_entries_from_bytes(
            json.dumps(source, ensure_ascii=False).encode("utf-8"),
            "upstream.json",
        )
        entry = WorldTreeEntry.from_dict(rows[0])
        self.assertEqual(entry.keyword_mode, "modern")
        self.assertEqual(entry.keywords, ["re:a.b", "re:re:foo"])
        # upstream compiled every keyword as a regex, so both must still behave
        # exactly as they did before the rewrite
        self.assertTrue(entry.matches_text("axb"))
        self.assertTrue(entry.matches_text("提到 re:foo 的消息"))

    def test_keywords_too_long_to_prefix_stay_on_the_compatibility_mode(self) -> None:
        source = {
            "entries": [
                {
                    "template": "common",
                    "name": "超长正则",
                    "content": "兼容内容",
                    "keywords": ["x" * 255],
                }
            ]
        }
        rows = load_entries_from_bytes(
            json.dumps(source, ensure_ascii=False).encode("utf-8"),
            "upstream.json",
        )
        self.assertEqual(rows[0]["keyword_mode"], "legacy_regex")
        self.assertEqual(rows[0]["keywords"], ["x" * 255])

    def test_common_worldbook_fields_and_round_trip_export(self) -> None:
        source = {
            "world_info": {
                "7": {
                    "comment": "雾港",
                    "key": ["盐雾", "钟声"],
                    "content": "港口背景",
                    "order": 12,
                    "disable": False,
                }
            }
        }
        rows = load_entries_from_bytes(
            json.dumps(source, ensure_ascii=False).encode("utf-8"),
            "common.json",
        )
        entry = WorldTreeEntry.from_dict(rows[0])
        self.assertEqual(entry.name, "雾港")
        self.assertEqual(entry.priority, 12)
        self.assertEqual(entry.keywords, ["盐雾", "钟声"])

        exported = json.loads(dump_entries([entry], "json"))
        self.assertEqual(exported["format"], "worldtree-lore/v1")
        self.assertEqual(exported["entries"][0]["name"], "雾港")

    def test_standard_crontab_weekdays_are_converted_for_apscheduler(self) -> None:
        self.assertEqual(_normalise_weekday_field("0,1-5,7"), "6,0-4,6")
        self.assertEqual(_normalise_weekday_field("mon-fri/2"), "mon-fri/2")


class CharacterEntryTests(unittest.TestCase):
    """Covers the character template and its per-entry model override."""

    def setUp(self) -> None:
        self.config = FakeConfig(entry_storage=[], library_revision=0, data_version=3)
        self.library = WorldTreeLibrary(self.config)
        self.library.load()

    def test_overrides_survive_the_full_round_trip(self) -> None:
        entry = make_entry(
            template="character",
            model=" deepseek-chat ",
            provider=" local/deepseek ",
        )
        self.assertEqual(entry.model, "deepseek-chat")
        self.assertEqual(entry.provider, "local/deepseek")
        restored = WorldTreeEntry.from_dict(entry.to_dict())
        self.assertEqual(restored.model, "deepseek-chat")
        self.assertEqual(restored.summary()["provider"], "local/deepseek")

    def test_overlong_override_is_rejected(self) -> None:
        with self.assertRaises(EntryValidationError):
            make_entry(model="m" * 201)

    def test_library_update_persists_overrides(self) -> None:
        created = self.library.create(
            {
                "name": "此小鬼",
                "content": "角色卡正文",
                "keywords": ["小鬼"],
                "template": "character",
            }
        )
        self.assertEqual(created.model, "")
        self.library.update(
            created.id,
            {"model": "deepseek-chat", "provider": "local/deepseek"},
            expected_revision=self.library.revision,
        )
        stored = self.library.get(created.id)
        self.assertEqual(stored.model, "deepseek-chat")
        self.assertEqual(stored.provider, "local/deepseek")

    def test_character_template_sorts_before_the_general_ones(self) -> None:
        self.library.create(
            {"name": "常规", "content": "x", "keywords": ["k"], "template": "common"}
        )
        self.library.create(
            {"name": "角色", "content": "x", "keywords": ["r"], "template": "character"}
        )
        names = [item["name"] for item in self.library.paged_entries(sort="template")["entries"]]
        self.assertEqual(names[0], "角色")

    def test_only_one_character_entry_stays_active_per_session(self) -> None:
        alice = make_entry(name="爱丽丝", template="character", keywords=["爱丽丝"], priority=5)
        bob = make_entry(name="鲍勃", template="character", keywords=["鲍勃"], priority=5)
        lore = make_entry(name="雾港档案", template="common", keywords=["雾港"], priority=50)
        ctx = ActivationContext(session_id="room-1")
        store = WorldTreeSessionStore()
        exclusive = frozenset({"character"})

        self.assertTrue(alice.try_activate(ctx, "爱丽丝在吗"))
        store.activate(ctx, [alice], allow_same_priority=True, exclusive_templates=exclusive)
        self.assertTrue(lore.try_activate(ctx, "说说雾港"))
        store.activate(ctx, [lore], allow_same_priority=True, exclusive_templates=exclusive)
        self.assertTrue(bob.try_activate(ctx, "鲍勃你好"))
        store.activate(ctx, [bob], allow_same_priority=True, exclusive_templates=exclusive)

        names = [entry.name for entry in store.active_for(ctx)]
        self.assertIn("鲍勃", names)
        # Two personas in one reply would fight over voice, so the newer one wins.
        self.assertNotIn("爱丽丝", names)
        # Ordinary lore has nothing to do with persona swapping and stays put.
        self.assertIn("雾港档案", names)

    def test_deactivate_ends_now_but_keeps_the_entry_triggerable(self) -> None:
        definition = make_entry(duration=600, times=0)
        ctx = ActivationContext(session_id="room-2")
        store = WorldTreeSessionStore()

        self.assertTrue(definition.try_activate(ctx, "雾港"))
        store.activate(ctx, [definition], allow_same_priority=True)
        self.assertEqual(store.deactivate(ctx.session_id, [definition.id]), [definition.id])
        self.assertEqual(store.active_for(ctx), [])
        # Unlike a block, nothing is left behind that would stop a re-trigger.
        _, blocked = store.status(ctx.session_id)
        self.assertEqual(blocked, set())
        self.assertTrue(definition.try_activate(ctx, "雾港"))
        self.assertEqual(
            store.activate(ctx, [definition], allow_same_priority=True),
            [definition.id],
        )


if __name__ == "__main__":
    unittest.main()
