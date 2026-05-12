from __future__ import annotations

import json
import re
from typing import List


UNRELEASED_TITLE = "## Unreleased"
UNRELEASED_PLACEHOLDER = "- Изменения в работе, которые еще не были выложены в production."


def normalize_frontend_version(app_version: str) -> str:
    parts = app_version.split(".")
    if len(parts) == 2:
        return f"{app_version}.0"
    if len(parts) == 3:
        return app_version
    raise ValueError("APP_VERSION должна быть в формате X.Y или X.Y.Z.")


def build_release_tag(app_version: str) -> str:
    return f"v{app_version}"


def update_version_source(version_source: str, app_version: str, release_date: str) -> str:
    updated = re.sub(r'^APP_VERSION = "[^"]+"$', f'APP_VERSION = "{app_version}"', version_source, flags=re.MULTILINE)
    updated = re.sub(r'^APP_RELEASE_DATE = "[^"]+"$', f'APP_RELEASE_DATE = "{release_date}"', updated, flags=re.MULTILINE)
    return updated


def update_frontend_package(package_text: str, app_version: str) -> str:
    payload = json.loads(package_text)
    payload["version"] = normalize_frontend_version(app_version)
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


def promote_unreleased_changelog(changelog_text: str, app_version: str, release_date: str) -> str:
    unreleased_match = re.search(r"^## Unreleased\s*$", changelog_text, re.MULTILINE)
    if unreleased_match is None:
        raise ValueError("В CHANGELOG.md отсутствует секция Unreleased.")

    section_start = unreleased_match.start()
    content_start = unreleased_match.end()
    next_section_match = re.search(r"^## \S.*$", changelog_text[content_start:], re.MULTILINE)
    section_end = content_start + next_section_match.start() if next_section_match else len(changelog_text)

    before = changelog_text[:section_start]
    unreleased_body = changelog_text[content_start:section_end]
    after = changelog_text[section_end:]

    release_lines = _extract_release_lines(unreleased_body)
    if not release_lines:
        raise ValueError("В секции Unreleased нет записей для релиза.")

    new_unreleased = f"{UNRELEASED_TITLE}\n\n{UNRELEASED_PLACEHOLDER}\n\n"
    release_block = "\n".join(
        [
            f"## {release_date}",
            "",
            f"- Версия или commit: `{app_version}`",
            *release_lines,
            "",
        ]
    )
    return f"{before}{new_unreleased}{release_block}{after.lstrip()}"


def _extract_release_lines(unreleased_body: str) -> List[str]:
    lines = [line.rstrip() for line in unreleased_body.strip().splitlines()]
    release_lines: List[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped == UNRELEASED_PLACEHOLDER:
            continue
        if stripped.startswith("- "):
            release_lines.append(stripped)
    return release_lines
