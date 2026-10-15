"""Deterministic, interpretable risk scoring with explicit missing-data handling.

The weights and thresholds are provisional engineering choices, not calibrated
Cyclone Michaung coefficients. All inputs must already be normalized to [0, 1].
When inputs are missing, observed weights are renormalized and the assessment is
marked partial; no observations produce an unavailable score.
"""

from __future__ import annotations

from collections.abc import Mapping

from app.schemas.contracts import (
    RISK_INDICATORS,
    RiskAssessment,
    RiskConfig,
    RiskFactor,
    RiskIndicators,
)


def assess_risk(
    target_id: str,
    indicators: Mapping[str, float | None],
    config: RiskConfig | None = None,
) -> RiskAssessment:
    """Score one target using configured weights, preserving missing indicators."""
    if not target_id.strip():
        raise ValueError("target_id cannot be blank")
    selected_config = config or RiskConfig()
    parsed = RiskIndicators.model_validate(indicators)
    values = parsed.model_dump()
    missing = [name for name in RISK_INDICATORS if values[name] is None]
    available = [
        name
        for name in RISK_INDICATORS
        if values[name] is not None and selected_config.weights[name] > 0
    ]

    if not available:
        return RiskAssessment(
            target_id=target_id,
            risk_score=None,
            level="UNAVAILABLE",
            contributing_factors={},
            missing_indicators=missing,
            explanation=(
                "No normalized indicators with positive configured weight were available; "
                "no risk score was calculated."
            ),
            complete=False,
        )

    available_weight = sum(selected_config.weights[name] for name in available)

    factors: dict[str, RiskFactor] = {}
    score = 0.0
    for name in available:
        value = values[name]
        assert value is not None
        configured_weight = selected_config.weights[name]
        effective_weight = configured_weight / available_weight
        contribution = value * effective_weight
        score += contribution
        factors[name] = RiskFactor(
            value=value,
            configured_weight=configured_weight,
            effective_weight=effective_weight,
            weighted_contribution=contribution,
        )

    if score < selected_config.low_threshold:
        level = "LOW"
    elif score < selected_config.moderate_threshold:
        level = "MODERATE"
    elif score < selected_config.high_threshold:
        level = "HIGH"
    else:
        level = "CRITICAL"

    is_complete = not missing
    completeness = "complete" if is_complete else "partial"
    missing_text = "none" if is_complete else ", ".join(missing)
    explanation = (
        f"{completeness.title()} assessment from {len(available)} normalized indicators; "
        f"available weights were renormalized. Missing indicators: {missing_text}. "
        "Weights and category thresholds are provisional and require evaluation."
    )
    return RiskAssessment(
        target_id=target_id,
        risk_score=score,
        level=level,
        contributing_factors=factors,
        missing_indicators=missing,
        explanation=explanation,
        complete=is_complete,
    )
