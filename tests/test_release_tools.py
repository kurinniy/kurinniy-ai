import unittest

from ai_me.release_tools import (
    build_release_tag,
    normalize_frontend_version,
    promote_unreleased_changelog,
    update_frontend_package,
    update_version_source,
)


class ReleaseToolsTest(unittest.TestCase):
    def test_normalize_frontend_version_appends_patch_for_minor_version(self) -> None:
        self.assertEqual(normalize_frontend_version("0.13"), "0.13.0")

    def test_normalize_frontend_version_keeps_patch_version(self) -> None:
        self.assertEqual(normalize_frontend_version("0.12.2"), "0.12.2")

    def test_build_release_tag_uses_v_prefix(self) -> None:
        self.assertEqual(build_release_tag("0.13"), "v0.13")

    def test_update_version_source_replaces_version_and_date(self) -> None:
        original = 'APP_VERSION = "0.12.2"\nAPP_RELEASE_DATE = "2026-05-12"\n'
        updated = update_version_source(original, "0.13", "2026-05-13")
        self.assertIn('APP_VERSION = "0.13"', updated)
        self.assertIn('APP_RELEASE_DATE = "2026-05-13"', updated)

    def test_update_frontend_package_writes_normalized_version(self) -> None:
        updated = update_frontend_package('{"version":"0.12.2","private":true}', "0.13")
        self.assertIn('"version": "0.13.0"', updated)

    def test_promote_unreleased_changelog_moves_notes_into_release_block(self) -> None:
        changelog = (
            "# CHANGELOG\n\n"
            "## Unreleased\n\n"
            "- Добавлен новый digest layout\n"
            "- Исправлен текст comparison\n\n"
            "## 2026-05-12\n\n"
            "- Версия или commit: `0.12.2`\n"
            "- Previous release\n"
        )

        updated = promote_unreleased_changelog(changelog, "0.13", "2026-05-13")

        self.assertIn("## Unreleased\n\n- Изменения в работе, которые еще не были выложены в production.\n\n## 2026-05-13", updated)
        self.assertIn("- Версия или commit: `0.13`", updated)
        self.assertIn("- Добавлен новый digest layout", updated)
        self.assertIn("- Исправлен текст comparison", updated)


if __name__ == "__main__":
    unittest.main()
