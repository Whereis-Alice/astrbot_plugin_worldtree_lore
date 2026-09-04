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

    def test_editor_explains_what_each_template_preset_did(self) -> None:
        markup = (ROOT / "pages" / "worldtree" / "index.html").read_text(
            encoding="utf-8"
        )
        script = (ROOT / "pages" / "worldtree" / "app.js").read_text(encoding="utf-8")
        styles = (ROOT / "pages" / "worldtree" / "style.css").read_text(
            encoding="utf-8"
        )

        # Templates only seed fields, so the editor keeps every control visible
        # and explains the preset instead of hiding the trigger section.
        self.assertIn('id="templateHint"', markup)
        self.assertIn('id="triggerNote"', markup)
        self.assertIn('id="keywordWarning"', markup)
        self.assertIn('id="applyTemplateDefaultsButton"', markup)
        self.assertIn("const TEMPLATE_NOTES = {", script)
        for key in ("character", "common", "resident", "chance", "schedule", "group", "user"):
            self.assertIn(f"  {key}: {{", script)
        self.assertIn("DEFAULT_TRIGGER_NOTE", script)
        self.assertIn("function updateEditorGuidance()", script)
        self.assertIn("function updateKeywordWarning()", script)
        self.assertIn("REGEX_LOOKALIKE", script)
        self.assertIn('confirmLabel: "套用默认值"', script)
        self.assertIn(".field-note {", styles)
        self.assertIn("small.field-warning {", styles)

    def test_confirm_dialog_separates_deletions_from_routine_changes(self) -> None:
        markup = (ROOT / "pages" / "worldtree" / "index.html").read_text(
            encoding="utf-8"
        )
        script = (ROOT / "pages" / "worldtree" / "app.js").read_text(encoding="utf-8")
        styles = (ROOT / "pages" / "worldtree" / "style.css").read_text(
            encoding="utf-8"
        )

        # Reusing the red "permanent delete" dialog for a reversible field reset
        # trains people to dismiss it, so the tone is swapped instead.
        self.assertIn('id="confirmEyebrow"', markup)
        self.assertIn('id="confirmGlyph"', markup)
        self.assertIn('data-tone="prune"', markup)
        self.assertIn("const CONFIRM_TONES = {", script)
        self.assertIn("  prune: {", script)
        self.assertIn("  graft: {", script)
        self.assertIn('tone: "graft"', script)
        self.assertIn("refs.cancelConfirm.textContent = cancelLabel", script)
        self.assertIn('.confirm-dialog[data-tone="graft"] {', styles)

    def test_legacy_regex_entries_are_visible_and_convertible(self) -> None:
        markup = (ROOT / "pages" / "worldtree" / "index.html").read_text(
            encoding="utf-8"
        )
        script = (ROOT / "pages" / "worldtree" / "app.js").read_text(encoding="utf-8")
        styles = (ROOT / "pages" / "worldtree" / "style.css").read_text(
            encoding="utf-8"
        )

        # Two keyword conventions in one library is a trap: switching an entry's
        # mode can silently turn a regex into a literal. Legacy entries are
        # therefore labelled on the card and convertible one by one or in bulk.
        self.assertIn('id="keywordLegacyHint"', markup)
        self.assertIn('id="keywordModeNote"', markup)
        self.assertIn('id="modernKeywordsButton"', markup)
        self.assertIn("refs.keywordLegacyHint.hidden = !legacy", script)
        self.assertIn('value="modernise_keywords"', markup)
        self.assertIn("async function modernisePendingKeywords()", script)
        self.assertIn('badge("旧世界书正则", "legacy-pill")', script)
        self.assertIn(".legacy-pill {", styles)
        self.assertIn(".legacy-hint {", styles)

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


    def test_character_entries_can_override_the_answering_model(self) -> None:
        markup = (ROOT / "pages" / "worldtree" / "index.html").read_text(
            encoding="utf-8"
        )
        script = (ROOT / "pages" / "worldtree" / "app.js").read_text(encoding="utf-8")
        styles = (ROOT / "pages" / "worldtree" / "style.css").read_text(
            encoding="utf-8"
        )
        main = (ROOT / "main.py").read_text(encoding="utf-8")

        # A character card is useless if the model it needs refuses to play it,
        # so the override lives on the entry, and the picker is filled from the
        # running AstrBot instance instead of asking people to memorise IDs.
        self.assertIn('id="modelInput"', markup)
        self.assertIn('id="providerInput"', markup)
        self.assertIn('id="providerOptions"', markup)
        self.assertIn("refs.model", script)
        self.assertIn("refs.provider.value", script)
        self.assertIn('bridge.apiGet("providers")', script)
        self.assertIn('badge(`模型 ${entry.model}`, "override-pill")', script)
        self.assertIn(".override-pill {", styles)
        self.assertIn('[data-template="character"]', styles)
        self.assertIn("/providers", main)
        self.assertIn("def web_providers", main)

        # The provider is chosen in the waiting hook because AstrBot builds the
        # agent right after it; that hook must never return a truthy value, or
        # the whole LLM request would be cancelled.
        self.assertIn("async def on_waiting_llm_request", main)
        self.assertIn("SELECTED_PROVIDER_EXTRA", main)

    def test_active_entries_can_be_stopped_on_demand(self) -> None:
        main = (ROOT / "main.py").read_text(encoding="utf-8")
        sessions = (ROOT / "worldtree" / "sessions.py").read_text(encoding="utf-8")
        readme = (ROOT / "README.md").read_text(encoding="utf-8")

        # Ending an activation early is not the same as blocking it: the entry
        # may trigger again on the next message, and the command says so.
        self.assertIn('@worldtree.command("终止"', main)
        self.assertIn("def deactivate(", sessions)
        self.assertIn("世界树 终止", readme)

    def test_plugin_logo_is_a_square_png(self) -> None:
        logo = ROOT / "logo.png"
        markup = (ROOT / "pages" / "worldtree" / "index.html").read_text(
            encoding="utf-8"
        )

        # AstrBot only picks up a file literally named logo.png in the plugin
        # root, and the plugin card renders it as a 64x64 square, so anything
        # non-square would be cropped.
        self.assertTrue(logo.is_file())
        header = logo.read_bytes()[:24]
        self.assertEqual(header[:8], b"\x89PNG\r\n\x1a\n")
        self.assertEqual(header[12:16], b"IHDR")
        width = int.from_bytes(header[16:20], "big")
        height = int.from_bytes(header[20:24], "big")
        self.assertEqual(width, height)
        self.assertGreaterEqual(width, 256)

        # The vector source stays in the repo so the icon can be recoloured
        # without reverse-engineering the bitmap.
        self.assertTrue((ROOT / "assets" / "logo.svg").is_file())
        self.assertIn('href="./favicon.svg"', markup)


if __name__ == "__main__":
    unittest.main()
