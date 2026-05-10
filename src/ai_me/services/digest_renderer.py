from __future__ import annotations

import math
from datetime import date
from io import BytesIO
from typing import List, Literal, Optional, Sequence, Tuple

from PIL import Image, ImageDraw, ImageFont, ImageOps

from ai_me.domain.digest import DailyFoodDigest, WeeklyFoodDigest
from ai_me.domain.food import MealMedia


RGBColor = Tuple[int, int, int]
OverlayPosition = Literal["top_left", "bottom_right"]
DEFAULT_DAILY_OVERLAY_COLOR: RGBColor = (28, 28, 28)
DEFAULT_WEEKLY_FRAME_COLOR: RGBColor = (177, 204, 195)
DEFAULT_WEEKLY_OVERLAY_COLOR: RGBColor = (54, 94, 82)
DEFAULT_CANVAS_FRAME_WIDTH = 3
RUSSIAN_MONTH_NAMES = (
    "",
    "января",
    "февраля",
    "марта",
    "апреля",
    "мая",
    "июня",
    "июля",
    "августа",
    "сентября",
    "октября",
    "ноября",
    "декабря",
)


class DigestImageRenderer:
    def __init__(
        self,
        tile_size: int = 420,
        gap: int = 18,
        background_color: RGBColor = (245, 240, 232),
        frame_color: RGBColor = (214, 205, 189),
    ) -> None:
        self.tile_size = tile_size
        self.gap = gap
        self.background_color = background_color
        self.frame_color = frame_color

    def render_daily_mosaic(self, digest: DailyFoodDigest) -> Optional[bytes]:
        media_items = [meal.media_items[0] for meal in digest.meals if meal.media_items]
        return self._render_media_grid(
            media_items,
            overlay_text=self._format_digest_date(digest.digest_date),
            overlay_fill_color=DEFAULT_DAILY_OVERLAY_COLOR,
            overlay_position="bottom_right",
            overlay_font_scale=1.3,
            overlay_corner_radius=4,
        )

    def render_weekly_mosaic(self, digest: WeeklyFoodDigest) -> Optional[bytes]:
        media_items = [
            highlight.meal.media_items[0]
            for highlight in digest.highlights
            if highlight.meal is not None and highlight.meal.media_items
        ]
        return self._render_media_grid(
            media_items,
            frame_color=DEFAULT_WEEKLY_FRAME_COLOR,
            overlay_text=self._format_week_range(digest.week_start, digest.week_end),
            overlay_fill_color=DEFAULT_WEEKLY_OVERLAY_COLOR,
            overlay_position="top_left",
            overlay_font_scale=1.3,
            overlay_corner_radius=4,
        )

    def _render_media_grid(
        self,
        media_items: Sequence[MealMedia],
        frame_color: Optional[RGBColor] = None,
        overlay_text: Optional[str] = None,
        overlay_fill_color: RGBColor = DEFAULT_DAILY_OVERLAY_COLOR,
        overlay_position: OverlayPosition = "bottom_right",
        overlay_font_scale: float = 1.0,
        overlay_corner_radius: Optional[int] = None,
    ) -> Optional[bytes]:
        tiles = self._build_tiles(media_items, frame_color=frame_color or self.frame_color)
        if not tiles:
            return None

        columns = self._choose_columns(len(tiles))
        rows = math.ceil(len(tiles) / columns)
        canvas_width = columns * self.tile_size + (columns + 1) * self.gap
        canvas_height = rows * self.tile_size + (rows + 1) * self.gap
        canvas = Image.new("RGB", (canvas_width, canvas_height), self.background_color)

        for index, tile in enumerate(tiles):
            row = index // columns
            col = index % columns
            x = self.gap + col * (self.tile_size + self.gap)
            y = self.gap + row * (self.tile_size + self.gap)
            canvas.paste(tile, (x, y))

        if overlay_text:
            canvas = self._apply_overlay_text(
                canvas,
                overlay_text,
                fill_color=overlay_fill_color,
                position=overlay_position,
                font_scale=overlay_font_scale,
                corner_radius=overlay_corner_radius,
            )

        canvas = self._apply_canvas_frame(canvas, frame_color=frame_color or self.frame_color)

        buffer = BytesIO()
        canvas.save(buffer, format="JPEG", quality=90, optimize=True)
        return buffer.getvalue()

    def _build_tiles(self, media_items: Sequence[MealMedia], frame_color: RGBColor) -> List[Image.Image]:
        tiles: List[Image.Image] = []
        for media in media_items:
            tile = self._meal_media_to_tile(media, frame_color=frame_color)
            if tile is not None:
                tiles.append(tile)
        return tiles

    def _meal_media_to_tile(self, media: MealMedia, frame_color: RGBColor) -> Optional[Image.Image]:
        if not media.image_bytes:
            return None
        try:
            with Image.open(BytesIO(media.image_bytes)) as raw_image:
                fitted = ImageOps.fit(
                    raw_image.convert("RGB"),
                    (self.tile_size, self.tile_size),
                    method=Image.Resampling.LANCZOS,
                )
        except Exception:
            return None

        framed = Image.new("RGB", (self.tile_size, self.tile_size), frame_color)
        inset = max(10, self.tile_size // 40)
        framed.paste(fitted.resize((self.tile_size - inset * 2, self.tile_size - inset * 2)), (inset, inset))
        return framed

    def _apply_overlay_text(
        self,
        canvas: Image.Image,
        text: str,
        fill_color: RGBColor,
        position: OverlayPosition,
        font_scale: float = 1.0,
        corner_radius: Optional[int] = None,
    ) -> Image.Image:
        outer_padding = max(self.gap, self.tile_size // 28)
        box_padding = max(8, self.tile_size // 26)
        base_font_size = max(18, self.tile_size // 9)
        font = self._load_overlay_font(max(18, int(round(base_font_size * font_scale))))
        overlay = Image.new("RGBA", canvas.size, (255, 255, 255, 0))
        draw = ImageDraw.Draw(overlay)
        left, top, right, bottom = draw.textbbox((0, 0), text, font=font)
        text_width = right - left
        text_height = bottom - top
        box_width = text_width + box_padding * 2
        box_height = text_height + box_padding * 2
        if position == "top_left":
            x0 = outer_padding
            y0 = outer_padding
        else:
            x0 = canvas.width - box_width - outer_padding
            y0 = canvas.height - box_height - outer_padding
        x1 = x0 + box_width
        y1 = y0 + box_height
        radius = corner_radius if corner_radius is not None else max(10, box_padding)

        draw.rounded_rectangle((x0, y0, x1, y1), radius=radius, fill=(*fill_color, 185))
        draw.text((x0 + box_padding, y0 + box_padding - top), text, font=font, fill=(255, 255, 255, 255))
        return Image.alpha_composite(canvas.convert("RGBA"), overlay).convert("RGB")

    def _apply_canvas_frame(self, canvas: Image.Image, frame_color: RGBColor) -> Image.Image:
        overlay = Image.new("RGBA", canvas.size, (255, 255, 255, 0))
        draw = ImageDraw.Draw(overlay)
        inset = max(1, self.gap // 3)
        radius = max(8, self.gap // 2)
        draw.rounded_rectangle(
            (inset, inset, canvas.width - inset - 1, canvas.height - inset - 1),
            radius=radius,
            outline=(*frame_color, 255),
            width=DEFAULT_CANVAS_FRAME_WIDTH,
        )
        return Image.alpha_composite(canvas.convert("RGBA"), overlay).convert("RGB")

    @staticmethod
    def _format_digest_date(digest_date: date) -> str:
        return digest_date.strftime("%d/%m/%y")

    @staticmethod
    def _format_week_range(week_start: date, week_end: date) -> str:
        if week_start.year == week_end.year and week_start.month == week_end.month:
            return f"{week_start:%d}-{week_end:%d} {RUSSIAN_MONTH_NAMES[week_end.month]} '{week_end:%y}"
        if week_start.year == week_end.year:
            return (
                f"{week_start:%d} {RUSSIAN_MONTH_NAMES[week_start.month]} - "
                f"{week_end:%d} {RUSSIAN_MONTH_NAMES[week_end.month]} '{week_end:%y}"
            )
        return (
            f"{week_start:%d} {RUSSIAN_MONTH_NAMES[week_start.month]} '{week_start:%y} - "
            f"{week_end:%d} {RUSSIAN_MONTH_NAMES[week_end.month]} '{week_end:%y}"
        )

    @staticmethod
    def _load_overlay_font(font_size: int) -> ImageFont.ImageFont | ImageFont.FreeTypeFont:
        try:
            return ImageFont.truetype("DejaVuSans-Bold.ttf", font_size)
        except OSError:
            return ImageFont.load_default()

    @staticmethod
    def _choose_columns(items_count: int) -> int:
        if items_count <= 1:
            return 1
        if items_count <= 4:
            return 2
        return 3
