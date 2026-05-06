import base64
import json
from dataclasses import dataclass
from typing import List, Protocol
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
            "Return title, summary, item titles, and portion_text in Russian. "
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
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "meal_analysis",
                    "strict": True,
                    "schema": self._response_schema(),
                }
            },
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

        data = self._extract_output_json(body)
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
    def _extract_output_json(body: dict) -> dict:
        for item in body.get("output", []):
            for content in item.get("content", []):
                if content.get("type") == "output_text":
                    return OpenAIFoodPhotoAnalyzer._parse_json_text(content["text"])
                if content.get("type") == "refusal":
                    raise ValueError("OpenAI отказался анализировать это изображение")
        raise ValueError("OpenAI не вернул структурированный результат")

    @staticmethod
    def _parse_json_text(text: str) -> dict:
        cleaned = text.strip()
        if cleaned.startswith("```"):
            lines = cleaned.splitlines()
            if lines and lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].startswith("```"):
                lines = lines[:-1]
            cleaned = "\n".join(lines).strip()

        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            start = cleaned.find("{")
            end = cleaned.rfind("}")
            if start != -1 and end != -1 and end > start:
                return json.loads(cleaned[start : end + 1])
            raise

    @staticmethod
    def _response_schema() -> dict:
        item_schema = {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "title": {"type": "string"},
                "portion_text": {"type": "string"},
                "calories": {"type": "integer"},
                "protein_g": {"type": "number"},
                "fat_g": {"type": "number"},
                "carbs_g": {"type": "number"},
            },
            "required": [
                "title",
                "portion_text",
                "calories",
                "protein_g",
                "fat_g",
                "carbs_g",
            ],
        }
        return {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "title": {"type": "string"},
                "summary": {"type": "string"},
                "calories": {"type": "integer"},
                "protein_g": {"type": "number"},
                "fat_g": {"type": "number"},
                "carbs_g": {"type": "number"},
                "confidence": {"type": "number"},
                "items": {
                    "type": "array",
                    "items": item_schema,
                },
            },
            "required": [
                "title",
                "summary",
                "calories",
                "protein_g",
                "fat_g",
                "carbs_g",
                "confidence",
                "items",
            ],
        }


class DisabledFoodPhotoAnalyzer:
    def analyze_photo(
        self,
        image_bytes: bytes,
        mime_type: str,
        caption: str = "",
    ) -> MealAnalysis:
        raise RuntimeError(
            "Анализ фото еды не настроен. Задайте OPENAI_API_KEY и OPENAI_MODEL."
        )
