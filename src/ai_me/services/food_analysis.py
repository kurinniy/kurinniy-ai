import base64
import json
from dataclasses import dataclass
from typing import List, Optional, Protocol
from urllib import request

from ai_me.domain.food import FoodItemEstimate


@dataclass(frozen=True)
class MealAnalysis:
    title: str
    summary: str
    calories: int
    protein_g: float
    fat_g: float
    carbs_g: float
    confidence: float
    items: List[FoodItemEstimate]


class FoodPhotoAnalyzer(Protocol):
    def analyze_photo(
        self,
        image_bytes: bytes,
        mime_type: str,
        caption: str = "",
    ) -> MealAnalysis:
        ...


class OpenAIFoodPhotoAnalyzer:
    def __init__(self, api_key: str, model: str) -> None:
        self.api_key = api_key
        self.model = model

    def analyze_photo(
        self,
        image_bytes: bytes,
        mime_type: str,
        caption: str = "",
    ) -> MealAnalysis:
        encoded = base64.b64encode(image_bytes).decode("ascii")
        input_text = (
            "Analyze this food photo. Return strict JSON with keys: "
            "title, summary, calories, protein_g, fat_g, carbs_g, confidence, items. "
            "Each item must include title, portion_text, calories, protein_g, fat_g, carbs_g. "
            "Estimate conservatively. If unclear, lower confidence. "
            "Caption context: %s" % (caption or "none")
        )
        payload = {
            "model": self.model,
            "input": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": input_text,
                        },
                        {
                            "type": "input_image",
                            "image_url": "data:%s;base64,%s" % (mime_type, encoded),
                        },
                    ],
                }
            ],
        }
        req = request.Request(
            "https://api.openai.com/v1/responses",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": "Bearer %s" % self.api_key,
                "Content-Type": "application/json",
            },
        )
        with request.urlopen(req, timeout=60) as response:
            body = json.loads(response.read().decode("utf-8"))

        output_text = self._extract_output_text(body)
        data = json.loads(output_text)
        return MealAnalysis(
            title=data["title"],
            summary=data["summary"],
            calories=int(data["calories"]),
            protein_g=float(data["protein_g"]),
            fat_g=float(data["fat_g"]),
            carbs_g=float(data["carbs_g"]),
            confidence=float(data["confidence"]),
            items=[
                FoodItemEstimate(
                    title=item["title"],
                    portion_text=item["portion_text"],
                    calories=int(item["calories"]),
                    protein_g=float(item["protein_g"]),
                    fat_g=float(item["fat_g"]),
                    carbs_g=float(item["carbs_g"]),
                )
                for item in data.get("items", [])
            ],
        )

    @staticmethod
    def _extract_output_text(body: dict) -> str:
        for item in body.get("output", []):
            for content in item.get("content", []):
                if content.get("type") == "output_text":
                    return content["text"]
        raise ValueError("OpenAI response did not contain output_text")


class DisabledFoodPhotoAnalyzer:
    def analyze_photo(
        self,
        image_bytes: bytes,
        mime_type: str,
        caption: str = "",
    ) -> MealAnalysis:
        raise RuntimeError(
            "Food photo analysis is not configured. Set OPENAI_API_KEY and OPENAI_MODEL."
        )

