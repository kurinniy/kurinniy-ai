#!/usr/bin/env python3
from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import date as date_cls
from pathlib import Path
from typing import List

from ai_me.release_guard import (
    CHANGELOG_FILE,
    FRONTEND_PACKAGE_FILE,
    VERSION_FILE,
    format_release_guard_errors,
    validate_release_guard,
)
from ai_me.release_tools import (
    build_release_tag,
    promote_unreleased_changelog,
    update_frontend_package,
    update_version_source,
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
    parser = argparse.ArgumentParser(description="Релиз stage -> main с version tag")
    parser.add_argument("version", help="Версия релиза: X.Y или X.Y.Z")
    parser.add_argument("--release-date", default=date_cls.today().isoformat())
    parser.add_argument("--stage-branch", default="stage")
    parser.add_argument("--main-branch", default="main")
    parser.add_argument("--push", action="store_true")
    args = parser.parse_args()

    ensure_clean_worktree()
    current_branch = git("branch", "--show-current").strip()
    repo_root = Path(__file__).resolve().parents[1]

    try:
        git("switch", args.main_branch)
        git("merge", "--ff-only", args.stage_branch)

        version_path = repo_root / VERSION_FILE
        package_path = repo_root / FRONTEND_PACKAGE_FILE
        changelog_path = repo_root / CHANGELOG_FILE

        version_source = update_version_source(version_path.read_text(encoding="utf-8"), args.version, args.release_date)
        frontend_package = update_frontend_package(package_path.read_text(encoding="utf-8"), args.version)
        changelog_text = promote_unreleased_changelog(changelog_path.read_text(encoding="utf-8"), args.version, args.release_date)

        version_path.write_text(version_source, encoding="utf-8")
        package_path.write_text(frontend_package, encoding="utf-8")
        changelog_path.write_text(changelog_text, encoding="utf-8")

        changed_files = release_changed_files(base_branch=f"origin/{args.main_branch}")
        result = validate_release_guard(
            changed_files=changed_files,
            version_source=version_source,
            changelog_text=changelog_text,
            frontend_package_text=frontend_package,
        )
        if not result.ok:
            print(format_release_guard_errors(result.errors), file=sys.stderr)
            return 1

        git("add", VERSION_FILE, FRONTEND_PACKAGE_FILE, CHANGELOG_FILE)
        git("commit", "-m", f"Обновить версию до {args.version}")
        tag_name = build_release_tag(args.version)
        git("tag", "-a", tag_name, "-m", f"Релиз {args.version}")

        if args.push:
            git("push", "origin", args.main_branch)
            git("push", "origin", tag_name)

        print(f"Release prepared on {args.main_branch}: version={args.version} tag={tag_name}")
        return 0
    finally:
        if current_branch and current_branch != args.main_branch:
            git("switch", current_branch)


def ensure_clean_worktree() -> None:
    status_lines = [
        line for line in git("status", "--porcelain", "--untracked-files=no").splitlines() if line.strip()
    ]
    if status_lines:
        raise SystemExit("Рабочее дерево должно быть чистым перед релизом.")


def release_changed_files(*, base_branch: str) -> List[str]:
    changed = [
        line.strip()
        for line in git("diff", "--name-only", f"{base_branch}..HEAD").splitlines()
        if line.strip()
    ]
    for required in (VERSION_FILE, FRONTEND_PACKAGE_FILE, CHANGELOG_FILE):
        if required not in changed:
            changed.append(required)
    return changed


if __name__ == "__main__":
    raise SystemExit(main())
