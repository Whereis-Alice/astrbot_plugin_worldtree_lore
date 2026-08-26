from __future__ import annotations

import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class WebUiContractTests(unittest.TestCase):
    def test_public_chinese_name_is_worldtree(self) -> None:
        metadata = (ROOT / "metadata.yaml").read_text(encoding="utf-8")
        locale = json.loads(
            (ROOT / ".astrbot-plugin" / "i18n" / "zh-CN.json").read_text(
                encoding="utf-8"
            )
        )
        readme = (ROOT / "README.md").read_text(encoding="utf-8")

        self.assertIn("display_name: 世界树\n", metadata)
        self.assertEqual(locale["metadata"]["display_name"], "世界树")
        self.assertTrue(readme.startswith("# 世界树\n"))
        self.assertNotIn("世界树·世界书", metadata + readme)

    def test_delete_confirmation_is_safe_inside_astrbot_sandbox(self) -> None:
        script = (ROOT / "pages" / "worldtree" / "app.js").read_text(encoding="utf-8")
        markup = (ROOT / "pages" / "worldtree" / "index.html").read_text(
            encoding="utf-8"
        )

        # AstrBot Plugin Pages do not grant the iframe `allow-modals` sandbox
        # capability, so alert/confirm/prompt are silently blocked by browsers.
        self.assertNotIn("window.confirm", script)
        self.assertIn('id="confirmDialog"', markup)
        self.assertIn('id="cancelConfirmButton"', markup)
        self.assertIn('id="acceptConfirmButton"', markup)
        self.assertIn("requestConfirmation", script)

    def test_single_and_bulk_delete_use_the_shared_confirmation(self) -> None:
        script = (ROOT / "pages" / "worldtree" / "app.js").read_text(encoding="utf-8")

        self.assertIn('confirmLabel: "永久删除"', script)
        self.assertIn("confirmLabel: `删除 ${ids.length} 项`", script)
        self.assertIn('entry/${entryId}/delete', script)
        self.assertIn('bridge.apiPost("entries/bulk"', script)


    def test_page_exposes_sorting_and_template_controls(self) -> None:
        markup = (ROOT / "pages" / "worldtree" / "index.html").read_text(
            encoding="utf-8"
        )
        script = (ROOT / "pages" / "worldtree" / "app.js").read_text(encoding="utf-8")
        styles = (ROOT / "pages" / "worldtree" / "style.css").read_text(
            encoding="utf-8"
        )

        # Ordering and template filtering are backend concerns, so the page must
        # ship the controls and forward both as query parameters.
        self.assertIn('id="sortSelect"', markup)
        self.assertIn('id="templateFilter"', markup)
        for mode in (
            "priority",
            "priority_desc",
            "template",
            "name",
            "folder",
            "updated",
            "enabled",
        ):
            self.assertIn(f'value="{mode}"', markup)
        self.assertIn("sort: DEFAULT_SORT,", script)
        self.assertIn("state.filters.sort = ", script)
        self.assertIn("state.filters.template = ", script)
        self.assertIn('bridge.apiGet("entries", state.filters)', script)

        # Each card advertises its template both as text and as a styling hook.
        self.assertIn("template-pill", script)
        self.assertIn("dataset.template", script)
        self.assertIn('[data-template="schedule"]', styles)

    def test_page_supports_duplicate_and_selective_export(self) -> None:
        markup = (ROOT / "pages" / "worldtree" / "index.html").read_text(
            encoding="utf-8"
        )
        script = (ROOT / "pages" / "worldtree" / "app.js").read_text(encoding="utf-8")
        main = (ROOT / "main.py").read_text(encoding="utf-8")

        self.assertIn('id="duplicateEntryButton"', markup)
        self.assertIn('id="exportSelectedButton"', markup)
        self.assertIn("entry/${entryId}/duplicate", script)
        self.assertIn("params.ids = ids.join", script)
        self.assertIn("/entry/<entry_id>/duplicate", main)
        self.assertIn("def web_duplicate_entry", main)

    def test_tree_layout_and_grouping_are_present(self) -> None:
        markup = (ROOT / "pages" / "worldtree" / "index.html").read_text(
            encoding="utf-8"
        )
        script = (ROOT / "pages" / "worldtree" / "app.js").read_text(encoding="utf-8")
        styles = (ROOT / "pages" / "worldtree" / "style.css").read_text(
            encoding="utf-8"
        )

        self.assertIn('id="tree"', markup)
        self.assertIn('id="collapseAllButton"', markup)
        self.assertIn('id="densityButton"', markup)
        self.assertIn("branch-head", script)
        self.assertIn(".tree-trunk", styles)
        self.assertIn(".branch-body", styles)

    def test_theme_choice_is_independent_of_astrbot(self) -> None:
        markup = (ROOT / "pages" / "worldtree" / "index.html").read_text(
            encoding="utf-8"
        )
        script = (ROOT / "pages" / "worldtree" / "app.js").read_text(encoding="utf-8")
        styles = (ROOT / "pages" / "worldtree" / "style.css").read_text(
            encoding="utf-8"
        )

        # The host theme is only a fallback: "auto" follows AstrBot, the other
        # three modes are stored locally and win over whatever the host reports.
        self.assertIn('id="themeSwitch"', markup)
        for mode in ("auto", "light", "dark", "nightglow"):
            self.assertIn(f'data-theme-mode="{mode}"', markup)
        self.assertIn('readPref("theme", "auto")', script)
        self.assertIn('writePref("theme", mode)', script)
        self.assertIn("state.hostIsDark", script)
        self.assertIn("function applyTheme()", script)

        # A theme picked on a previous visit must be on the root element before
        # the first paint, otherwise dark users get a white flash.
        self.assertIn('localStorage.getItem("worldtree.theme")', markup)

        self.assertIn('[data-theme="nightglow"] {', styles)
        self.assertIn(".theme-switch {", styles)
        self.assertIn("@keyframes glimmer", styles)

    def test_dark_overrides_also_cover_the_nightglow_theme(self) -> None:
        styles = (ROOT / "pages" / "worldtree" / "style.css").read_text(
            encoding="utf-8"
        )

        # Nightglow reuses the dark palette semantics, so every dark descendant
        # rule is written as :is(dark, nightglow). Only the two variable blocks
        # may target a single theme on their own.
        shared = styles.count(':is([data-theme="dark"], [data-theme="nightglow"])')
        self.assertGreater(shared, 40)
        self.assertEqual(styles.count('[data-theme="dark"]'), shared + 1)


if __name__ == "__main__":
    unittest.main()
