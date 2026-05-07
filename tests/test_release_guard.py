import unittest

from ai_me.release_guard import validate_release_guard


class ReleaseGuardTest(unittest.TestCase):
    def test_release_guard_passes_when_version_and_changelog_are_updated(self) -> None:
        result = validate_release_guard(
            changed_files=[
                "src/ai_me/version.py",
                "frontend/package.json",
                "CHANGELOG.md",
                "src/ai_me/web/app.py",
            ],
            version_source='APP_VERSION = "0.3"\nAPP_RELEASE_DATE = "2026-05-07"\n',
            changelog_text=(
                "# CHANGELOG\n\n"
                "## Unreleased\n\n"
                "- pending\n\n"
                "## 2026-05-07\n\n"
                "- Версия или commit: `0.3`\n"
                "- Release text\n"
            ),
            frontend_package_text='{"version":"0.3.0"}',
        )
        self.assertTrue(result.ok)
        self.assertEqual(result.errors, [])

    def test_release_guard_fails_without_version_and_changelog_updates(self) -> None:
        result = validate_release_guard(
            changed_files=[
                "src/ai_me/web/app.py",
                "src/ai_me/telegram.py",
            ],
            version_source='APP_VERSION = "0.2"\nAPP_RELEASE_DATE = "2026-05-07"\n',
            changelog_text=(
                "# CHANGELOG\n\n"
                "## Unreleased\n\n"
                "- pending\n\n"
                "## 2026-05-07\n\n"
                "- Версия или commit: `0.2`\n"
                "- Release text\n"
            ),
            frontend_package_text='{"version":"0.2.0"}',
        )
        self.assertFalse(result.ok)
        self.assertGreaterEqual(len(result.errors), 3)

    def test_release_guard_fails_when_frontend_version_does_not_match_backend(self) -> None:
        result = validate_release_guard(
            changed_files=[
                "src/ai_me/version.py",
                "frontend/package.json",
                "CHANGELOG.md",
                "frontend/src/App.tsx",
            ],
            version_source='APP_VERSION = "0.3"\nAPP_RELEASE_DATE = "2026-05-07"\n',
            changelog_text=(
                "# CHANGELOG\n\n"
                "## Unreleased\n\n"
                "- pending\n\n"
                "## 2026-05-07\n\n"
                "- Версия или commit: `0.3`\n"
                "- Release text\n"
            ),
            frontend_package_text='{"version":"0.2.0"}',
        )
        self.assertFalse(result.ok)
        self.assertIn(
            "Версия в frontend/package.json не совпадает с APP_VERSION по major.minor.",
            result.errors,
        )
