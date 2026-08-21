from __future__ import annotations

import unittest
from pathlib import Path


class V943SupersededContractTests(unittest.TestCase):
    def test_v943_task_visibility_contract_is_superseded_by_v944(self) -> None:
        root = Path(__file__).resolve().parents[1]
        version = (root / "VERSION").read_text(encoding="utf-8").strip()
        changelog = (root / "CHANGELOG.md").read_text(encoding="utf-8")
        self.assertGreaterEqual(tuple(int(part) for part in version.split(".")), (9, 4, 4))
        self.assertIn("Restored the public task MCP/API contract to v9.4.2 compatibility", changelog)
        self.assertIn("WebGUI-only Tasks filtering", changelog)


if __name__ == "__main__":
    unittest.main()
