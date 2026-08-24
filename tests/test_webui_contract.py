from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class WebUiContractTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
