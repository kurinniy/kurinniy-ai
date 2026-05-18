import base64
import json
from datetime import date, timedelta
from typing import Dict, List, Optional

from ai_me.domain.decision_log import DecisionStatus
from ai_me.domain.food import MealDraftStatus, MealMedia, MealPhotoDraft
from ai_me.domain.health import DailyHealthSummary, MealEntry
from ai_me.domain.user import AppUser, UserGoal, UserSex
from ai_me.services.health_service import HealthService
from ai_me.version import APP_RELEASE_DATE, APP_VERSION


def build_dashboard_payload(
    *,
    service: HealthService,
    app_user: AppUser,
    target_date: date,
) -> Dict[str, object]:
    service.evaluate_day(app_user.user_id, target_date)
    summary = service.get_daily_summary(app_user.user_id, target_date)
    meals = service.list_meals(app_user.user_id, target_date)
    decisions = service.list_decisions(app_user.user_id, status=DecisionStatus.OPEN, target_date=target_date)

    return {
        "user": {
            "user_id": app_user.user_id,
            "telegram_user_id": app_user.telegram_user_id,
            "username": app_user.username,
            "first_name": app_user.first_name,
            "is_admin": app_user.has_admin_access,
            "is_admin_account": app_user.is_admin,
            "admin_mode_enabled": app_user.admin_mode_enabled,
            "status": app_user.status.value,
        },
        "version": {
            "app_version": APP_VERSION,
            "release_date": APP_RELEASE_DATE,
        },
        "summary": _serialize_summary(summary, meals),
        "history": _serialize_meal_history(service=service, user_id=app_user.user_id),
        "recognitions": _serialize_recognition_history(service=service, user_id=app_user.user_id),
        "analytics": _serialize_analytics(service=service, user_id=app_user.user_id, target_date=target_date),
        "profile": _serialize_profile(app_user),
        "decisions": [
            {
                "decision_id": decision.decision_id,
                "kind": decision.kind.value,
                "title": decision.title,
                "rationale": decision.rationale,
                "status": decision.status.value,
                "context_date": decision.context_date.isoformat(),
            }
            for decision in decisions
        ],
    }


def build_meal_entry_detail_payload(
    *,
    service: HealthService,
    user_id: int,
    entry_id: str,
) -> Dict[str, object]:
    meal = service.get_meal_entry(user_id, entry_id)
    media = service.get_primary_meal_media_for_entry(user_id, entry_id)
    return _serialize_meal_detail(meal, media)


def build_recognition_detail_payload(
    *,
    service: HealthService,
    user_id: int,
    draft_id: str,
) -> Dict[str, object]:
    draft = service.get_meal_draft_any_status(user_id, draft_id)
    media = service.get_primary_meal_media_for_draft(user_id, draft_id)
    return _serialize_recognition_detail(draft, media)


def _serialize_summary(
    summary: DailyHealthSummary,
    meals: List[MealEntry],
) -> Dict[str, object]:
    return {
        "target_date": summary.target_date.isoformat(),
        "meals_count": summary.meals_count,
        "calories": summary.calories,
        "protein_g": summary.protein_g,
        "fat_g": summary.fat_g,
        "carbs_g": summary.carbs_g,
        "water_ml": summary.water_ml,
        "goals": {
            "water_ml": summary.goals.water_ml,
            "protein_g": summary.goals.protein_g,
        },
        "meals": [
            {
                "entry_id": meal.entry_id,
                "occurred_at": meal.occurred_at.isoformat(),
                "title": meal.title,
                "calories": meal.calories,
                "protein_g": meal.protein_g,
                "fat_g": meal.fat_g,
                "carbs_g": meal.carbs_g,
            }
            for meal in meals
        ],
    }


def build_profile_payload(app_user: AppUser) -> Dict[str, object]:
    return _serialize_profile(app_user)


def _serialize_meal_history(*, service: HealthService, user_id: int) -> Dict[str, object]:
    recent_meals = service.list_recent_meals(user_id, limit=40, lookback_days=365)
    days_by_date: Dict[str, List[Dict[str, object]]] = {}
    for meal in recent_meals:
        day_key = meal.occurred_at.date().isoformat()
        days_by_date.setdefault(day_key, []).append(
            {
                "entry_id": meal.entry_id,
                "occurred_at": meal.occurred_at.isoformat(),
                "created_at": (meal.created_at or meal.occurred_at).isoformat(),
                "title": meal.title,
                "calories": meal.calories,
                "status": "saved",
            }
        )
    days = [
        {
            "date": day_key,
            "entries": sorted(entries, key=lambda item: str(item["occurred_at"]), reverse=True),
        }
        for day_key, entries in sorted(days_by_date.items(), key=lambda item: item[0], reverse=True)
    ]
    return {
        "days": days,
        "has_more": len(recent_meals) == 40,
    }


def _serialize_recognition_history(*, service: HealthService, user_id: int) -> Dict[str, object]:
    drafts = service.list_recent_food_draft_history(user_id, limit=40)
    return {
        "items": [_serialize_recognition_list_item(draft) for draft in drafts],
        "has_more": len(drafts) == 40,
    }


def _serialize_analytics(*, service: HealthService, user_id: int, target_date: date) -> Dict[str, object]:
    points = []
    logging_days = 0
    days_with_calories = 0
    total_calories = 0
    total_protein = 0.0
    total_water_ml = 0
    longest_streak = 0
    running_streak = 0

    start_date = target_date - timedelta(days=13)
    cursor = start_date
    while cursor <= target_date:
        summary = service.get_daily_summary(user_id, cursor)
        has_logging = summary.meals_count > 0
        if has_logging:
            logging_days += 1
            days_with_calories += 1
            total_calories += summary.calories
            total_protein += summary.protein_g
            total_water_ml += summary.water_ml
            running_streak += 1
            longest_streak = max(longest_streak, running_streak)
        else:
            running_streak = 0
        points.append(
            {
                "date": cursor.isoformat(),
                "calories": summary.calories,
                "protein_g": round(summary.protein_g, 1),
                "water_ml": summary.water_ml,
                "meals_count": summary.meals_count,
                "has_logging": has_logging,
            }
        )
        cursor += timedelta(days=1)

    current_streak = 0
    for point in reversed(points):
        if not point["has_logging"]:
            break
        current_streak += 1

    return {
        "window_days": len(points),
        "points": points,
        "logging_days": logging_days,
        "logging_frequency_pct": round((logging_days / max(len(points), 1)) * 100, 1),
        "current_streak": current_streak,
        "longest_streak": longest_streak,
        "average_calories": round(total_calories / days_with_calories, 1) if days_with_calories else 0.0,
        "average_protein_g": round(total_protein / days_with_calories, 1) if days_with_calories else 0.0,
        "average_water_ml": round(total_water_ml / days_with_calories) if days_with_calories else 0,
    }


def _serialize_profile(app_user: AppUser) -> Dict[str, object]:
    return {
        "about": {
            "sex": app_user.sex.value if app_user.sex is not None else None,
            "sex_label": _sex_label(app_user.sex),
            "age_years": app_user.age_years,
            "height_cm": app_user.height_cm,
            "profile_weight_kg": app_user.profile_weight_kg,
            "goal": app_user.goal.value if app_user.goal is not None else None,
            "goal_label": _goal_label(app_user.goal),
        },
        "goals": {
            "target_water_ml": app_user.target_water_ml,
            "target_protein_g": app_user.target_protein_g,
            "target_calories_min": app_user.target_calories_min,
            "target_calories_max": app_user.target_calories_max,
        },
        "reminders": {
            "enabled": app_user.reminders_enabled,
            "meal_logging": app_user.reminder_meal_logging,
            "water": app_user.reminder_water,
            "evening_summary": app_user.reminder_evening_summary,
        },
    }


def _serialize_recognition_list_item(draft: MealPhotoDraft) -> Dict[str, object]:
    return {
        "draft_id": draft.draft_id,
        "created_at": draft.created_at.isoformat(),
        "occurred_at": draft.occurred_at.isoformat(),
        "title": draft.title,
        "summary": draft.summary,
        "calories": draft.calories,
        "status": draft.status.value,
        "status_label": _draft_status_label(draft.status),
        "is_water_only": draft.is_water_only,
    }


def _serialize_meal_detail(meal: MealEntry, media: Optional[MealMedia]) -> Dict[str, object]:
    return {
        "entry_id": meal.entry_id,
        "occurred_at": meal.occurred_at.isoformat(),
        "created_at": (meal.created_at or meal.occurred_at).isoformat(),
        "title": meal.title,
        "summary": _extract_meal_summary(meal),
        "calories": meal.calories,
        "protein_g": meal.protein_g,
        "fat_g": meal.fat_g,
        "carbs_g": meal.carbs_g,
        "water_ml": meal.water_ml,
        "status": "saved",
        "status_label": "Сохранено",
        "photo_data_url": _serialize_media_data_url(media),
    }


def _serialize_recognition_detail(draft: MealPhotoDraft, media: Optional[MealMedia]) -> Dict[str, object]:
    return {
        "draft_id": draft.draft_id,
        "created_at": draft.created_at.isoformat(),
        "occurred_at": draft.occurred_at.isoformat(),
        "title": draft.title,
        "summary": draft.summary,
        "calories": draft.calories,
        "protein_g": draft.protein_g,
        "fat_g": draft.fat_g,
        "carbs_g": draft.carbs_g,
        "water_ml": draft.water_ml,
        "confidence": draft.confidence,
        "status": draft.status.value,
        "status_label": _draft_status_label(draft.status),
        "is_water_only": draft.is_water_only,
        "photo_data_url": _serialize_media_data_url(media),
    }


def _serialize_media_data_url(media: Optional[MealMedia]) -> Optional[str]:
    if media is None or not media.image_bytes:
        return None
    encoded = base64.b64encode(media.image_bytes).decode("ascii")
    mime_type = media.mime_type or "image/jpeg"
    return "data:%s;base64,%s" % (mime_type, encoded)


def _extract_meal_summary(meal: MealEntry) -> str:
    if not meal.notes:
        return ""
    try:
        payload = json.loads(meal.notes)
    except (TypeError, ValueError):
        return ""
    summary = payload.get("summary")
    return str(summary).strip() if summary else ""


def _draft_status_label(status: MealDraftStatus) -> str:
    if status == MealDraftStatus.PENDING:
        return "Ожидает решения"
    if status == MealDraftStatus.CONFIRMED:
        return "Сохранено"
    return "Отклонено"


def _sex_label(sex: Optional[UserSex]) -> str:
    if sex == UserSex.MALE:
        return "Мужчина"
    if sex == UserSex.FEMALE:
        return "Женщина"
    return "Не указан"


def _goal_label(goal: Optional[UserGoal]) -> str:
    if goal == UserGoal.MAINTENANCE:
        return "Поддержание"
    if goal == UserGoal.WEIGHT_LOSS:
        return "Похудение"
    if goal == UserGoal.MASS_GAIN:
        return "Набор массы"
    return "Не указана"
