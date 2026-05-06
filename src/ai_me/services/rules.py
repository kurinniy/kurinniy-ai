from datetime import datetime
from typing import List
from uuid import uuid4

from ai_me.domain.decision_log import DecisionKind, DecisionLogEntry
from ai_me.domain.health import DailyHealthSummary


class HealthDecisionEngine:
    def evaluate(
        self,
        summary: DailyHealthSummary,
        now: datetime,
        agent: str = "health",
    ) -> List[DecisionLogEntry]:
        decisions = []
        if now.date() != summary.target_date:
            return decisions

        if now.hour >= 16 and summary.water_ml < summary.goals.water_ml * 0.7:
            missing = summary.goals.water_ml - summary.water_ml
            decisions.append(
                self._build_decision(
                    summary=summary,
                    now=now,
                    agent=agent,
                    kind=DecisionKind.ALERT,
                    decision_key="water_gap",
                    title="Water intake is behind target",
                    rationale=(
                        "Daily water intake is below 70% of the target by late afternoon. "
                        "A small catch-up plan is safer than trying to recover everything at night."
                    ),
                    payload={
                        "current_water_ml": str(summary.water_ml),
                        "target_water_ml": str(summary.goals.water_ml),
                        "missing_water_ml": str(missing),
                    },
                )
            )

        if now.hour >= 14 and summary.protein_g < summary.goals.protein_g * 0.6:
            missing = round(summary.goals.protein_g - summary.protein_g, 2)
            decisions.append(
                self._build_decision(
                    summary=summary,
                    now=now,
                    agent=agent,
                    kind=DecisionKind.RECOMMENDATION,
                    decision_key="protein_gap",
                    title="Protein target is likely to be missed",
                    rationale=(
                        "Protein intake is still below 60% of the daily goal after lunch. "
                        "A protein-focused meal or snack would reduce the gap."
                    ),
                    payload={
                        "current_protein_g": str(summary.protein_g),
                        "target_protein_g": str(summary.goals.protein_g),
                        "missing_protein_g": str(missing),
                    },
                )
            )

        if summary.sleep_hours and summary.sleep_hours < max(6.0, summary.goals.sleep_hours - 1):
            if summary.activity_minutes > 0 or now.hour <= 12:
                decisions.append(
                    self._build_decision(
                        summary=summary,
                        now=now,
                        agent=agent,
                        kind=DecisionKind.RECOMMENDATION,
                        decision_key="sleep_recovery",
                        title="Recovery day adjustment is recommended",
                        rationale=(
                            "Sleep was meaningfully below target. Training intensity and planning load "
                            "should be reduced today to avoid compounding fatigue."
                        ),
                        payload={
                            "sleep_hours": str(summary.sleep_hours),
                            "target_sleep_hours": str(summary.goals.sleep_hours),
                        },
                    )
                )

        return decisions

    def _build_decision(
        self,
        summary: DailyHealthSummary,
        now: datetime,
        agent: str,
        kind: DecisionKind,
        decision_key: str,
        title: str,
        rationale: str,
        payload: dict,
    ) -> DecisionLogEntry:
        return DecisionLogEntry(
            decision_id=str(uuid4()),
            decision_key="%s:%s:%s" % (agent, summary.target_date.isoformat(), decision_key),
            created_at=now,
            agent=agent,
            kind=kind,
            title=title,
            rationale=rationale,
            context_date=summary.target_date,
            payload=payload,
        )
