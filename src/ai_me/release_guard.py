from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, List, Sequence


VERSION_FILE = "src/ai_me/version.py"
CHANGELOG_FILE = "CHANGELOG.md"
FRONTEND_PACKAGE_FILE = "frontend/package.json"


@dataclass(frozen=True)
class ReleaseGuardResult:
    ok: bool
    errors: List[str]


def detect_release_relevant_changes(changed_files: Sequence[str]) -> bool:
    ignored_prefixes = (".git", "__pycache__")
    ignored_exact = {CHANGELOG_FILE, VERSION_FILE, FRONTEND_PACKAGE_FILE}
    for file_path in changed_files:
        normalized = file_path.strip()
        if not normalized or normalized in ignored_exact:
            continue
        if normalized.startswith(ignored_prefixes):
            continue
        return True
    return False


def extract_current_version(version_source: str) -> str:
    match = re.search(r'^APP_VERSION = "([^"]+)"$', version_source, re.MULTILINE)
    if not match:
        raise ValueError("Не удалось найти APP_VERSION в src/ai_me/version.py")
    return match.group(1)


def extract_major_minor(version: str) -> str:
    parts = version.split(".")
    if len(parts) < 2:
        raise ValueError("Версия должна содержать хотя бы major.minor.")
    return ".".join(parts[:2])


def extract_latest_release_block(changelog_text: str) -> str:
    matches = list(re.finditer(r"^## \d{4}-\d{2}-\d{2}$", changelog_text, re.MULTILINE))
    if not matches:
        raise ValueError("В CHANGELOG.md нет ни одного релизного блока.")
    start = matches[0].start()
    end = matches[1].start() if len(matches) > 1 else len(changelog_text)
    return changelog_text[start:end].strip()


def validate_release_guard(
    *,
    changed_files: Sequence[str],
    version_source: str,
    changelog_text: str,
    frontend_package_text: str,
) -> ReleaseGuardResult:
    errors: List[str] = []
    current_version = extract_current_version(version_source)
    latest_release_block = extract_latest_release_block(changelog_text)
    frontend_version = extract_frontend_version(frontend_package_text)

    if not detect_release_relevant_changes(changed_files):
        return ReleaseGuardResult(ok=True, errors=[])

    changed_set = set(changed_files)
    if VERSION_FILE not in changed_set:
        errors.append("Не обновлен src/ai_me/version.py.")
    if FRONTEND_PACKAGE_FILE not in changed_set:
        errors.append("Не обновлен frontend/package.json.")
    if CHANGELOG_FILE not in changed_set:
        errors.append("Не обновлен CHANGELOG.md.")

    version_line = f"- Версия или commit: `{current_version}`"
    if version_line not in latest_release_block:
        errors.append("Верхний релизный блок CHANGELOG.md не содержит текущую версию.")
    if extract_major_minor(frontend_version) != extract_major_minor(current_version):
        errors.append("Версия в frontend/package.json не совпадает с APP_VERSION по major.minor.")

    return ReleaseGuardResult(ok=not errors, errors=errors)


def format_release_guard_errors(errors: Iterable[str]) -> str:
    lines = ["Release guard failed:"]
    for error in errors:
        lines.append(f"- {error}")
    return "\n".join(lines)


def extract_frontend_version(package_text: str) -> str:
    import json

    payload = json.loads(package_text)
    version = str(payload.get("version", "")).strip()
    if not version:
        raise ValueError("В frontend/package.json отсутствует version.")
    if not re.match(r"^\d+\.\d+\.\d+$", version):
        raise ValueError("В frontend/package.json version должна быть в формате X.Y.Z.")
    return version
