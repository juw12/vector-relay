"""The list of versions cannot fall behind the version that ships.

Run from the repo:  python -m unittest discover -s tests -v
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import vector_relay as relay  # noqa: E402


class VersionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.releases = json.loads((REPO / "docs" / "versions.json").read_text(encoding="utf-8"))["releases"]

    def test_newest_entry_is_the_version_that_ships(self) -> None:
        page = (REPO / "docs" / "app.js").read_text(encoding="utf-8")
        on_page = re.search(r'APP_VERSION = "([0-9.]+)"', page).group(1)
        self.assertEqual(self.releases[0]["version"], relay.VERSION)
        self.assertEqual(on_page, relay.VERSION)

    def test_every_entry_is_complete_and_newest_first(self) -> None:
        numbers = [tuple(int(part) for part in entry["version"].split(".")) for entry in self.releases]
        self.assertEqual(numbers, sorted(numbers, reverse=True))
        self.assertEqual(len(numbers), len(set(numbers)))
        for entry in self.releases:
            self.assertRegex(entry["date"], r"^\d{4}-\d{2}-\d{2}$")
            self.assertTrue(entry["title"].strip())
            self.assertTrue(entry["changes"])

    def test_changelog_and_readme_are_made_from_the_list(self) -> None:
        made = subprocess.run([sys.executable, str(REPO / "scripts" / "make_changelog.py"), "--check"],
                              capture_output=True, text=True)
        self.assertEqual(made.returncode, 0, made.stdout + made.stderr)

    def test_the_pc_serves_the_list_in_local_mode(self) -> None:
        self.assertEqual(relay.PAGE_FILES.get("versions.json"), "versions.json")
        self.assertIn(".json", relay.PAGE_TYPES)


if __name__ == "__main__":
    unittest.main()
