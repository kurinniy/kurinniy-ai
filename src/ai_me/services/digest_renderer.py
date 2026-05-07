from __future__ import annotations

import math
from io import BytesIO
from typing import Iterable, List, Optional, Sequence, Tuple

from PIL import Image, ImageOps

from ai_me.domain.digest import DailyFoodDigest, WeeklyFoodDigest
from ai_me.domain.food import MealMedia


RGBColor = Tuple[int, int, int]


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
        return self._render_media_grid(media_items)

    def render_weekly_mosaic(self, digest: WeeklyFoodDigest) -> Optional[bytes]:
        media_items = [
            highlight.meal.media_items[0]
            for highlight in digest.highlights
            if highlight.meal is not None and highlight.meal.media_items
        ]
        return self._render_media_grid(media_items)

    def _render_media_grid(self, media_items: Sequence[MealMedia]) -> Optional[bytes]:
        tiles = self._build_tiles(media_items)
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

        buffer = BytesIO()
        canvas.save(buffer, format="JPEG", quality=90, optimize=True)
        return buffer.getvalue()

    def _build_tiles(self, media_items: Sequence[MealMedia]) -> List[Image.Image]:
        tiles: List[Image.Image] = []
        for media in media_items:
            tile = self._meal_media_to_tile(media)
            if tile is not None:
                tiles.append(tile)
        return tiles

    def _meal_media_to_tile(self, media: MealMedia) -> Optional[Image.Image]:
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

        framed = Image.new("RGB", (self.tile_size, self.tile_size), self.frame_color)
        inset = max(10, self.tile_size // 40)
        framed.paste(fitted.resize((self.tile_size - inset * 2, self.tile_size - inset * 2)), (inset, inset))
        return framed

    @staticmethod
    def _choose_columns(items_count: int) -> int:
        if items_count <= 1:
            return 1
        if items_count <= 4:
            return 2
        return 3
