#!/usr/bin/env python3
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from ai_me.release_guard import (
    CHANGELOG_FILE,
    FRONTEND_PACKAGE_FILE,
    VERSION_FILE,
    extract_frontend_version,
    format_release_guard_errors,
    validate_release_guard,
)


def git(*args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        check=True,
        text=True,
        capture_output=True,
    )
    return result.stdout


def main() -> int:
    parser = argparse.ArgumentParser(description="Проверка релизного merge stage -> main")
    parser.add_argument("base", nargs="?", default="origin/main")
    parser.add_argument("head", nargs="?", default="HEAD")
    args = parser.parse_args()

    changed_files = [
        line.strip()
        for line in git("diff", "--name-only", f"{args.base}..{args.head}").splitlines()
        if line.strip()
    ]

    repo_root = Path(__file__).resolve().parents[1]
    version_source = (repo_root / VERSION_FILE).read_text(encoding="utf-8")
    changelog_text = (repo_root / CHANGELOG_FILE).read_text(encoding="utf-8")
    frontend_package_text = (repo_root / FRONTEND_PACKAGE_FILE).read_text(encoding="utf-8")

    result = validate_release_guard(
        changed_files=changed_files,
        version_source=version_source,
        changelog_text=changelog_text,
        frontend_package_text=frontend_package_text,
    )
    if not result.ok:
        print(format_release_guard_errors(result.errors), file=sys.stderr)
        return 1

    extract_frontend_version(frontend_package_text)

    print("Release guard passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
