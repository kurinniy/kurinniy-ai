import base64
import unittest
from io import BytesIO
from datetime import date, datetime

from PIL import Image, ImageChops

from ai_me.domain.digest import DailyFoodDigest, DigestMealSnapshot, WeeklyDigestHighlight, WeeklyFoodDigest
from ai_me.domain.food import MealMedia
from ai_me.services.digest_renderer import DigestImageRenderer


VALID_PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO2WZgAAAABJRU5ErkJggg=="
)


class DigestImageRendererTest(unittest.TestCase):
    def setUp(self) -> None:
        self.renderer = DigestImageRenderer(tile_size=120, gap=8)
        self.media = MealMedia(
            media_id="media-1",
            user_id=1,
            draft_id="draft-1",
            meal_entry_id="meal-1",
            occurred_at=datetime(2026, 5, 6, 12, 0),
            created_at=datetime(2026, 5, 6, 12, 0),
            mime_type="image/png",
            telegram_file_id="file-1",
            telegram_unique_id="u-1",
            byte_size=len(VALID_PNG_BYTES),
            sha256="abc",
            image_bytes=VALID_PNG_BYTES,
        )

    def test_render_daily_mosaic_returns_jpeg_bytes(self) -> None:
        digest = DailyFoodDigest(
            user_id=1,
            digest_date=date(2026, 5, 6),
            meals=[
                DigestMealSnapshot(
                    meal_entry_id="meal-1",
                    occurred_at=datetime(2026, 5, 6, 12, 0),
                    title="Курица с рисом",
                    calories=620,
                    protein_g=38,
                    fat_g=18,
                    carbs_g=71,
                    media_items=[self.media],
                )
            ],
            total_calories=620,
            total_protein_g=38.0,
            total_fat_g=18.0,
            total_carbs_g=71.0,
            trend_windows=[],
            commentary="Комментарий",
        )

        image_bytes = self.renderer.render_daily_mosaic(digest)

        self.assertIsNotNone(image_bytes)
        self.assertTrue(image_bytes.startswith(b"\xff\xd8\xff"))

    def test_render_daily_mosaic_adds_digest_date_overlay(self) -> None:
        digest = DailyFoodDigest(
            user_id=1,
            digest_date=date(2026, 5, 6),
            meals=[
                DigestMealSnapshot(
                    meal_entry_id="meal-1",
                    occurred_at=datetime(2026, 5, 6, 12, 0),
                    title="Курица с рисом",
                    calories=620,
                    protein_g=38,
                    fat_g=18,
                    carbs_g=71,
                    media_items=[self.media],
                )
            ],
            total_calories=620,
            total_protein_g=38.0,
            total_fat_g=18.0,
            total_carbs_g=71.0,
            trend_windows=[],
            commentary="Комментарий",
        )

        baseline_bytes = self.renderer._render_media_grid([self.media])
        image_bytes = self.renderer.render_daily_mosaic(digest)

        self.assertIsNotNone(baseline_bytes)
        self.assertIsNotNone(image_bytes)
        with Image.open(BytesIO(baseline_bytes)) as baseline_image, Image.open(BytesIO(image_bytes)) as rendered_image:
            diff = ImageChops.difference(baseline_image.convert("RGB"), rendered_image.convert("RGB"))

        self.assertIsNotNone(diff.getbbox())

    def test_format_week_range_uses_requested_russian_format(self) -> None:
        formatted = self.renderer._format_week_range(date(2026, 5, 5), date(2026, 5, 11))

        self.assertEqual(formatted, "05-11 мая '26")

    def test_render_weekly_mosaic_adds_range_overlay_and_distinct_style(self) -> None:
        digest = WeeklyFoodDigest(
            user_id=1,
            week_start=date(2026, 5, 5),
            week_end=date(2026, 5, 11),
            highlights=[
                WeeklyDigestHighlight(
                    digest_date=date(2026, 5, 6),
                    meal=DigestMealSnapshot(
                        meal_entry_id="meal-1",
                        occurred_at=datetime(2026, 5, 6, 12, 0),
                        title="Курица с рисом",
                        calories=620,
                        protein_g=38,
                        fat_g=18,
                        carbs_g=71,
                        media_items=[self.media],
                    ),
                    score=0.8,
                    reason="Лучший приём пищи",
                )
            ],
            total_meals=1,
            total_calories=620,
            commentary="Комментарий",
        )

        baseline_bytes = self.renderer._render_media_grid([self.media])
        image_bytes = self.renderer.render_weekly_mosaic(digest)

        self.assertIsNotNone(baseline_bytes)
        self.assertIsNotNone(image_bytes)
        with Image.open(BytesIO(baseline_bytes)) as baseline_image, Image.open(BytesIO(image_bytes)) as rendered_image:
            diff = ImageChops.difference(baseline_image.convert("RGB"), rendered_image.convert("RGB"))

        self.assertIsNotNone(diff.getbbox())

    def test_render_weekly_mosaic_returns_none_without_images(self) -> None:
        digest = WeeklyFoodDigest(
            user_id=1,
            week_start=date(2026, 5, 4),
            week_end=date(2026, 5, 10),
            highlights=[WeeklyDigestHighlight(digest_date=date(2026, 5, 4), meal=None, score=0.0, reason="Нет блюда")],
            total_meals=0,
            total_calories=0,
            commentary="Комментарий",
        )

        image_bytes = self.renderer.render_weekly_mosaic(digest)

        self.assertIsNone(image_bytes)
