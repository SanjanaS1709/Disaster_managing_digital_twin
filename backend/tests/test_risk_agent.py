import pytest
from pydantic import ValidationError

from app.agents.risk import assess_risk
from app.schemas.contracts import RiskConfig


@pytest.mark.parametrize(
    ("score", "level"),
    [
        (0.0, "LOW"),
        (0.249999, "LOW"),
        (0.25, "MODERATE"),
        (0.499999, "MODERATE"),
        (0.5, "HIGH"),
        (0.749999, "HIGH"),
        (0.75, "CRITICAL"),
        (1.0, "CRITICAL"),
    ],
)
def test_risk_category_boundaries(score, level):
    result = assess_risk("Z001", {"flood_severity": score})

    assert result.risk_score == pytest.approx(score)
    assert result.level == level
    assert result.complete is False


@pytest.mark.parametrize("value", [-0.01, 1.01, float("nan"), float("inf")])
def test_risk_rejects_invalid_normalized_values(value):
    with pytest.raises(ValidationError):
        assess_risk("Z001", {"flood_severity": value})


def test_risk_weights_must_sum_to_one():
    with pytest.raises(ValidationError):
        RiskConfig(weights={
            "flood_severity": 0.3,
            "rainfall_indicator": 0.2,
            "elevation_risk": 0.2,
            "infrastructure_vulnerability": 0.2,
            "population_exposure": 0.2,
        })


def test_risk_reports_unavailable_when_every_indicator_is_missing():
    result = assess_risk("R001", {})

    assert result.risk_score is None
    assert result.level == "UNAVAILABLE"
    assert result.complete is False
    assert len(result.missing_indicators) == 5
    assert result.contributing_factors == {}


def test_risk_renormalizes_available_weights_and_marks_partial():
    result = assess_risk("R001", {"flood_severity": 0.8, "rainfall_indicator": 0.4})

    assert result.risk_score == pytest.approx((0.35 * 0.8 + 0.25 * 0.4) / 0.60)
    assert result.complete is False
    assert result.missing_indicators == [
        "elevation_risk",
        "infrastructure_vulnerability",
        "population_exposure",
    ]
    assert sum(factor.effective_weight for factor in result.contributing_factors.values()) == pytest.approx(1)


def test_risk_complete_assessment_uses_all_configured_weights():
    result = assess_risk(
        "Z001",
        {
            "flood_severity": 1.0,
            "rainfall_indicator": 0.0,
            "elevation_risk": 0.0,
            "infrastructure_vulnerability": 0.0,
            "population_exposure": 0.0,
        },
    )

    assert result.risk_score == pytest.approx(0.35)
    assert result.level == "MODERATE"
    assert result.complete is True
    assert result.missing_indicators == []
    assert set(result.contributing_factors) == {
        "flood_severity",
        "rainfall_indicator",
        "elevation_risk",
        "infrastructure_vulnerability",
        "population_exposure",
    }
