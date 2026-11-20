"""Make CHANGELOG.md and the Versions part of the README from docs/versions.json.

    python scripts/make_changelog.py           write both
    python scripts/make_changelog.py --check   exit 1 if either is behind the list
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
VERSIONS = REPO / "docs" / "versions.json"
CHANGELOG = REPO / "CHANGELOG.md"
README = REPO / "README.md"
START, END = "<!-- versions:start -->", "<!-- versions:end -->"
PULLS = "https://github.com/juw12/vector-relay/pull/"


def releases() -> list:
    return json.loads(VERSIONS.read_text(encoding="utf-8"))["releases"]


def changelog_text(entries: list) -> str:
    lines = [
        "# Changelog",
        "",
        "Made from `docs/versions.json` by `scripts/make_changelog.py`. Do not edit by hand.",
        "The phone page shows the same list under **Version history**.",
        "",
    ]
    for entry in entries:
        pull = f" ([#{entry['pr']}]({PULLS}{entry['pr']}))" if entry.get("pr") else ""
        lines += [f"## {entry['version']} - {entry['date']}: {entry['title']}{pull}", ""]
        lines += [f"- {change}" for change in entry["changes"]]
        lines.append("")
    return "\n".join(lines)


def readme_block(entries: list) -> str:
    lines = [START]
    for entry in entries:
        lines.append(f"- **{entry['version']}** ({entry['date']}) {entry['title']}. " + " ".join(entry["changes"]))
    lines.append(END)
    return "\n".join(lines)


def readme_text(entries: list) -> str:
    text = README.read_text(encoding="utf-8")
    start, end = text.index(START), text.index(END) + len(END)
    return text[:start] + readme_block(entries) + text[end:]


def main() -> int:
    entries = releases()
    wanted = {CHANGELOG: changelog_text(entries), README: readme_text(entries)}
    if "--check" in sys.argv[1:]:
        behind = [path.name for path, text in wanted.items()
                  if not path.exists() or path.read_text(encoding="utf-8") != text]
        if behind:
            print("behind docs/versions.json: " + ", ".join(behind) + ". Run python scripts/make_changelog.py")
            return 1
        print("CHANGELOG.md and README.md match docs/versions.json")
        return 0
    for path, text in wanted.items():
        path.write_bytes(text.encode("utf-8"))
    print(f"wrote {CHANGELOG.name} and the Versions part of {README.name}: {len(entries)} versions")
    return 0


if __name__ == "__main__":
    sys.exit(main())
