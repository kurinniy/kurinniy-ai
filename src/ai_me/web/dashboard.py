from datetime import date, timedelta
from typing import Dict, List

from ai_me.domain.decision_log import DecisionStatus
from ai_me.domain.health import DailyHealthSummary, StepProgressInsight
from ai_me.domain.user import AppUser
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
    step_progress = service.build_step_progress_insight(app_user.user_id, target_date - timedelta(days=1))

    return {
        "user": {
            "user_id": app_user.user_id,
            "telegram_user_id": app_user.telegram_user_id,
            "username": app_user.username,
            "first_name": app_user.first_name,
            "is_admin": app_user.is_admin,
            "status": app_user.status.value,
        },
        "version": {
            "app_version": APP_VERSION,
            "release_date": APP_RELEASE_DATE,
        },
        "summary": _serialize_summary(summary, meals, step_progress),
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


def _serialize_summary(
    summary: DailyHealthSummary,
    meals: List,
    step_progress: StepProgressInsight,
) -> Dict[str, object]:
    return {
        "target_date": summary.target_date.isoformat(),
        "meals_count": summary.meals_count,
        "calories": summary.calories,
        "protein_g": summary.protein_g,
        "fat_g": summary.fat_g,
        "carbs_g": summary.carbs_g,
        "water_ml": summary.water_ml,
        "sleep_hours": summary.sleep_hours,
        "steps": summary.steps,
        "activity_minutes": summary.activity_minutes,
        "latest_weight_kg": summary.latest_weight_kg,
        "goals": {
            "water_ml": summary.goals.water_ml,
            "protein_g": summary.goals.protein_g,
            "sleep_hours": summary.goals.sleep_hours,
            "steps": summary.goals.steps,
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
        "step_progress": {
            "reference_date": step_progress.reference_date.isoformat(),
            "steps": step_progress.steps,
            "target_steps": step_progress.target_steps,
            "average_steps_30d": step_progress.average_steps_30d,
            "days_with_data_30d": step_progress.days_with_data_30d,
            "comment": step_progress.comment,
        },
    }
