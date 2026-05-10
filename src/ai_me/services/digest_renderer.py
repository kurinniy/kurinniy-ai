from __future__ import annotations

import math
from datetime import date, timedelta
from io import BytesIO
from pathlib import Path
from typing import List, Literal, Optional, Sequence, Tuple

from PIL import Image, ImageDraw, ImageFont, ImageOps

from ai_me.domain.digest import DailyFoodDigest, WeeklyFoodDigest
from ai_me.domain.food import MealMedia


RGBColor = Tuple[int, int, int]
OverlayPosition = Literal["top_left", "bottom_left", "bottom_right"]
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
FONT_CANDIDATE_PATHS = (
    "DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
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
            overlay_position="bottom_left",
            overlay_font_scale=3.25,
            overlay_corner_radius=4,
            overlay_max_width_ratio=0.18,
        )

    def render_weekly_mosaic(self, digest: WeeklyFoodDigest) -> Optional[bytes]:
        tiles = self._build_weekly_tiles(digest, frame_color=DEFAULT_WEEKLY_FRAME_COLOR)
        return self._render_tile_grid(
            tiles,
            columns=3,
            rows=3,
            frame_color=DEFAULT_WEEKLY_FRAME_COLOR,
            overlay_text=self._format_week_range(digest.week_start, digest.week_end),
            overlay_fill_color=DEFAULT_WEEKLY_OVERLAY_COLOR,
            overlay_position="top_left",
            overlay_font_scale=3.25,
            overlay_corner_radius=4,
            overlay_max_width_ratio=0.18,
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
        overlay_max_width_ratio: float = 1.0,
    ) -> Optional[bytes]:
        tiles = self._build_tiles(media_items, frame_color=frame_color or self.frame_color)
        if not tiles:
            return None

        columns = self._choose_columns(len(tiles))
        rows = math.ceil(len(tiles) / columns)
        return self._render_tile_grid(
            tiles,
            columns=columns,
            rows=rows,
            frame_color=frame_color or self.frame_color,
            overlay_text=overlay_text,
            overlay_fill_color=overlay_fill_color,
            overlay_position=overlay_position,
            overlay_font_scale=overlay_font_scale,
            overlay_corner_radius=overlay_corner_radius,
            overlay_max_width_ratio=overlay_max_width_ratio,
        )

    def _render_tile_grid(
        self,
        tiles: Sequence[Optional[Image.Image]],
        columns: int,
        rows: int,
        frame_color: RGBColor,
        overlay_text: Optional[str],
        overlay_fill_color: RGBColor,
        overlay_position: OverlayPosition,
        overlay_font_scale: float,
        overlay_corner_radius: Optional[int],
        overlay_max_width_ratio: float,
    ) -> Optional[bytes]:
        if not tiles:
            return None

        canvas_width = columns * self.tile_size + (columns + 1) * self.gap
        canvas_height = rows * self.tile_size + (rows + 1) * self.gap
        canvas = Image.new("RGB", (canvas_width, canvas_height), self.background_color)

        for index, tile in enumerate(tiles):
            if tile is None:
                continue
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
                max_width_ratio=overlay_max_width_ratio,
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

    def _build_weekly_tiles(self, digest: WeeklyFoodDigest, frame_color: RGBColor) -> List[Optional[Image.Image]]:
        highlight_by_date = {
            highlight.digest_date: highlight
            for highlight in digest.highlights
        }
        tiles: List[Optional[Image.Image]] = []

        for day_offset in range(6):
            target_date = digest.week_start + timedelta(days=day_offset)
            tiles.append(self._weekly_tile_from_highlight(highlight_by_date.get(target_date), frame_color=frame_color))

        tiles.append(None)

        sunday_date = digest.week_start + timedelta(days=6)
        tiles.append(self._weekly_tile_from_highlight(highlight_by_date.get(sunday_date), frame_color=frame_color))

        tiles.append(None)
        return tiles

    def _weekly_tile_from_highlight(self, highlight, frame_color: RGBColor) -> Image.Image:
        if highlight is not None and highlight.meal is not None and highlight.meal.media_items:
            media = highlight.meal.media_items[0]
            tile = self._meal_media_to_tile(media, frame_color=frame_color)
            if tile is not None:
                return tile
        return self._build_placeholder_tile(frame_color=frame_color)

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

    def _build_placeholder_tile(self, frame_color: RGBColor) -> Image.Image:
        tile = Image.new("RGB", (self.tile_size, self.tile_size), frame_color)
        inset = max(10, self.tile_size // 40)
        inner_size = self.tile_size - inset * 2
        inner = Image.new("RGBA", (inner_size, inner_size), (252, 252, 250, 255))
        overlay = Image.new("RGBA", (inner_size, inner_size), (255, 255, 255, 0))

        icon_size = max(32, int(round(inner_size * 0.6)))
        color = (0, 0, 0, 128)
        knife = self._build_placeholder_knife(icon_size=icon_size, color=color)
        fork = self._build_placeholder_fork(icon_size=icon_size, color=color)

        knife = knife.rotate(-44, resample=Image.Resampling.BICUBIC, expand=True)
        fork = fork.rotate(44, resample=Image.Resampling.BICUBIC, expand=True)

        knife_x = (inner_size - knife.width) // 2 - max(2, icon_size // 28)
        knife_y = (inner_size - knife.height) // 2 - max(2, icon_size // 40)
        fork_x = (inner_size - fork.width) // 2 + max(2, icon_size // 34)
        fork_y = (inner_size - fork.height) // 2 + max(2, icon_size // 48)
        overlay.alpha_composite(knife, (knife_x, knife_y))
        overlay.alpha_composite(fork, (fork_x, fork_y))

        inner = Image.alpha_composite(inner, overlay)
        tile.paste(inner.convert("RGB"), (inset, inset))
        return tile

    @staticmethod
    def _build_placeholder_knife(icon_size: int, color: Tuple[int, int, int, int]) -> Image.Image:
        scale = 4
        width = max(30, int(round(icon_size * 0.34)))
        height = max(76, int(round(icon_size * 0.9)))
        image = Image.new("RGBA", (width * scale, height * scale), (255, 255, 255, 0))
        draw = ImageDraw.Draw(image)

        def sx(value: float) -> float:
            return value * width * scale

        def sy(value: float) -> float:
            return value * height * scale

        handle_left = sx(0.39)
        handle_right = sx(0.61)
        handle_top = sy(0.40)
        handle_bottom = sy(0.98)
        draw.rounded_rectangle(
            (handle_left, handle_top, handle_right, handle_bottom),
            radius=max(10, int(round((handle_right - handle_left) * 0.48))),
            fill=color,
        )

        shoulder_y = sy(0.35)
        draw.rounded_rectangle(
            (sx(0.34), sy(0.31), sx(0.66), sy(0.45)),
            radius=max(8, int(round(width * scale * 0.08))),
            fill=color,
        )
        blade_points = [
            (sx(0.50), sy(0.02)),
            (sx(0.82), sy(0.23)),
            (sx(0.67), shoulder_y),
            (sx(0.33), shoulder_y),
            (sx(0.18), sy(0.23)),
        ]
        draw.polygon(blade_points, fill=color)

        return image.resize((width, height), resample=Image.Resampling.LANCZOS)

    @staticmethod
    def _build_placeholder_fork(icon_size: int, color: Tuple[int, int, int, int]) -> Image.Image:
        scale = 4
        width = max(32, int(round(icon_size * 0.36)))
        height = max(76, int(round(icon_size * 0.9)))
        image = Image.new("RGBA", (width * scale, height * scale), (255, 255, 255, 0))
        draw = ImageDraw.Draw(image)

        def sx(value: float) -> float:
            return value * width * scale

        def sy(value: float) -> float:
            return value * height * scale

        handle_left = sx(0.39)
        handle_right = sx(0.61)
        handle_top = sy(0.42)
        handle_bottom = sy(0.98)
        draw.rounded_rectangle(
            (handle_left, handle_top, handle_right, handle_bottom),
            radius=max(10, int(round((handle_right - handle_left) * 0.48))),
            fill=color,
        )

        neck_left = sx(0.36)
        neck_right = sx(0.64)
        neck_top = sy(0.28)
        neck_bottom = sy(0.49)
        draw.rounded_rectangle(
            (neck_left, neck_top, neck_right, neck_bottom),
            radius=max(8, int(round(width * scale * 0.07))),
            fill=color,
        )

        tine_width = sx(0.12) - sx(0.0)
        tine_gap = sx(0.05) - sx(0.0)
        tine_top = sy(0.02)
        tine_bottom = sy(0.28)
        first_tine_left = sx(0.15)
        for tine_index in range(4):
            tine_left = first_tine_left + tine_index * (tine_width + tine_gap)
            draw.rounded_rectangle(
                (tine_left, tine_top, tine_left + tine_width, tine_bottom),
                radius=max(6, int(round(tine_width * 0.45))),
                fill=color,
            )

        draw.rounded_rectangle(
            (sx(0.12), sy(0.22), sx(0.88), sy(0.36)),
            radius=max(8, int(round(width * scale * 0.08))),
            fill=color,
        )

        return image.resize((width, height), resample=Image.Resampling.LANCZOS)

    def _apply_overlay_text(
        self,
        canvas: Image.Image,
        text: str,
        fill_color: RGBColor,
        position: OverlayPosition,
        font_scale: float = 1.0,
        corner_radius: Optional[int] = None,
        max_width_ratio: float = 1.0,
    ) -> Image.Image:
        outer_padding = max(self.gap // 2, self.tile_size // 36)
        horizontal_padding = max(12, self.tile_size // 28)
        vertical_padding = max(8, self.tile_size // 36)
        base_font_size = max(22, self.tile_size // 6)
        overlay = Image.new("RGBA", canvas.size, (255, 255, 255, 0))
        draw = ImageDraw.Draw(overlay)
        max_box_width = min(
            canvas.width - outer_padding * 2,
            max(1, int(canvas.width * max_width_ratio)),
        )
        font = self._fit_overlay_font(
            draw=draw,
            text=text,
            desired_size=max(18, int(round(base_font_size * font_scale))),
            max_text_width=max(1, max_box_width - horizontal_padding * 2),
        )
        left, top, right, bottom = draw.textbbox((0, 0), text, font=font)
        text_width = right - left
        text_height = bottom - top
        box_width = text_width + horizontal_padding * 2
        box_height = text_height + vertical_padding * 2
        if position == "top_left":
            x0 = outer_padding
            y0 = outer_padding
        elif position == "bottom_left":
            x0 = outer_padding
            y0 = canvas.height - box_height - outer_padding
        else:
            x0 = canvas.width - box_width - outer_padding
            y0 = canvas.height - box_height - outer_padding
        x1 = x0 + box_width
        y1 = y0 + box_height
        radius = corner_radius if corner_radius is not None else max(4, vertical_padding)

        draw.rounded_rectangle((x0, y0, x1, y1), radius=radius, fill=(*fill_color, 185))
        draw.text(
            (x0 + horizontal_padding, y0 + vertical_padding - top),
            text,
            font=font,
            fill=(255, 255, 255, 255),
        )
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
        for candidate in FONT_CANDIDATE_PATHS:
            try:
                if "/" in candidate and not Path(candidate).exists():
                    continue
                return ImageFont.truetype(candidate, font_size)
            except OSError:
                continue
        return ImageFont.load_default()

    @classmethod
    def _fit_overlay_font(
        cls,
        draw: ImageDraw.ImageDraw,
        text: str,
        desired_size: int,
        max_text_width: int,
    ) -> ImageFont.ImageFont | ImageFont.FreeTypeFont:
        for font_size in range(desired_size, 17, -2):
            font = cls._load_overlay_font(font_size)
            left, _, right, _ = draw.textbbox((0, 0), text, font=font)
            if (right - left) <= max_text_width:
                return font
        return cls._load_overlay_font(18)

    @staticmethod
    def _choose_columns(items_count: int) -> int:
        if items_count <= 1:
            return 1
        if items_count <= 4:
            return 2
        return 3
