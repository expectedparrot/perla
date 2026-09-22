import copy

import pytest
from pydantic import ValidationError

from perla import lookback, reporting
from perla.models import LookbackInput
from perla.store import PerlaError


def forecast(pid, probability, population="team_moves", basis=None):
    return {
        "id": pid,
        "probability": probability,
        "population": population,
        "basis": basis,
        "due_date": "2027-12-31",
        "source_provenance": {"origin": "simulation"},
        "registration_provenance": {"origin": "human"},
    }


def event(eid, ids, outcome=True, status="resolved"):
    return {
        "id": eid,
        "statement": "A defined event occurs before the deadline",
        "prediction_ids": ids,
        "status": status,
        "outcome": outcome,
        "as_of": "2028-01-01",
        "occurred_on": "2027-12-01" if outcome else None,
        "rationale": "Scripted observation for tests",
        "sources": [{"reference": "fixture:reality", "description": "Fictional observation"}],
    }


def wrap(*events):
    return {
        e["id"]: {"content": e, "provenance": {"origin": "human"}, "history": []} for e in events
    }


def test_brier_bins_and_missingness_have_known_answers():
    registry = [
        forecast(f"p{i:03d}", p) for i, p in enumerate([0, 0.1, 0.8, 1, None, 0.6, 0.5, 0.4], 1)
    ]
    events = wrap(
        event("zero", ["p001"], False),
        event("tenth", ["p002"], True),
        event("eighth", ["p003"], True),
        event("one", ["p004"], False),
        event("no_probability", ["p005"], True),
        event("pending", ["p006"], None, "unresolved"),
        event("unknowable", ["p007"], None, "unresolvable"),
    )
    result = lookback.compute(registry, events, "2028-01-01")
    stats = result["summary"]
    assert stats["registered"] == 8
    assert stats["resolved"] == 5
    assert stats["scored"] == 4
    assert stats["resolved_without_probability"] == 1
    assert stats["unrecorded"] == stats["unresolved"] == stats["unresolvable"] == 1
    assert stats["brier_score"] == pytest.approx((0 + 0.81 + 0.04 + 1) / 4)
    bins = stats["calibration_bins"]
    assert [b["count"] for b in bins] == [1, 1, 0, 0, 0, 0, 0, 0, 1, 1]
    assert bins[1]["mean_probability"] == 0.1
    assert bins[1]["observed_frequency"] == 1
    assert bins[2]["observed_frequency"] is None
    assert bins[-1]["mean_probability"] == 1
    assert all(r["brier_score"] is None for r in result["predictions"][4:])
    assert result["by_population"]["human_rulings"]["brier_score"] is None
    rendered = reporting.render_calibration(result)
    assert "<svg" in rendered and "n=1" in rendered


def test_matched_comparisons_use_same_events_and_event_weights():
    registry = [
        forecast("p001", 0.9),
        forecast("p002", 0.7),
        forecast("p003", 0.1),
        forecast("p004", 0.8, "agent_rulings", "judgment"),
        forecast("p005", 0.6, "agent_rulings", "testimony"),
        forecast("p006", 0.95, "human_rulings", "testimony"),
        forecast("p007", 0.5, "human_rulings", "judgment"),
    ]
    events = wrap(
        event("shared", ["p001", "p002", "p004", "p006"]),
        event("second", ["p003", "p005"], False),
        event("unmatched", ["p007"], False),
    )
    result = lookback.compute(registry, events, "2028-01-01")
    pair = result["matched_comparisons"][0]
    assert pair["event_ids"] == ["second", "shared"]
    assert pair["scores"]["team_moves"]["event_weighted_brier_score"] == pytest.approx(
        (0.05 + 0.01) / 2
    )
    assert pair["scores"]["agent_rulings"]["event_weighted_brier_score"] == pytest.approx(
        (0.04 + 0.36) / 2
    )
    assert result["matched_comparisons"][1]["event_ids"] == ["shared"]
    assert result["by_basis"]["testimony"]["scored"] == 2
    assert result["by_population_and_basis"]["agent_rulings"]["testimony"]["scored"] == 1
    assert result["predictions"][3]["registration_provenance"]["origin"] == "human"
    assert result["predictions"][3]["population"] == "agent_rulings"


@pytest.mark.parametrize(
    "change",
    [
        "boolean",
        "missing_source",
        "bad_date",
        "false_with_occurrence",
        "resolved_null",
        "unresolved_bool",
        "duplicate_id",
        "future_occurrence",
        "injected_probability",
    ],
)
def test_invalid_observation_schema(change):
    observed = event("event", ["p001"])
    if change == "boolean":
        observed["outcome"] = 1
    elif change == "missing_source":
        observed["sources"] = []
    elif change == "bad_date":
        observed["as_of"] = "2028-02-30"
    elif change == "false_with_occurrence":
        observed["outcome"] = False
    elif change == "resolved_null":
        observed.update(outcome=None, occurred_on=None)
    elif change == "unresolved_bool":
        observed["status"] = "unresolved"
    elif change == "duplicate_id":
        observed["prediction_ids"] *= 2
    elif change == "future_occurrence":
        observed["occurred_on"] = "2029-01-01"
    else:
        observed["probability"] = 0.9
    with pytest.raises(ValidationError):
        LookbackInput.model_validate({"events": [observed]})


@pytest.mark.parametrize(
    "change,code",
    [
        ("unknown", "E_PREDICTIONS"),
        ("overlap", "E_LOOKBACK"),
        ("early_negative", "E_LOOKBACK_DATE"),
        ("late_positive", "E_LOOKBACK_DATE"),
        ("score_cutoff", "E_LOOKBACK_DATE"),
    ],
)
def test_mapping_and_time_guards(change, code):
    registry = [forecast("p001", 0.8)]
    observed = event("one", ["p001"])
    events = wrap(observed)
    if change == "unknown":
        observed["prediction_ids"] = ["p999"]
    elif change == "overlap":
        events.update(wrap(event("two", ["p001"])))
    elif change == "early_negative":
        observed.update(outcome=False, occurred_on=None, as_of="2027-12-01")
    elif change == "late_positive":
        observed["occurred_on"] = "2028-01-01"
    with pytest.raises(PerlaError) as exc:
        lookback.compute(
            registry, events, "2027-12-31" if change == "score_cutoff" else "2028-01-01"
        )
    assert exc.value.code == code


def test_scoring_does_not_mutate_frozen_registry_or_invent_probabilities():
    registry = [forecast("p001", None)]
    original = copy.deepcopy(registry)
    result = lookback.compute(registry, wrap(event("one", ["p001"])), "2028-01-01")
    assert registry == original
    assert result["summary"]["brier_score"] is None
    assert {w["code"] for w in result["warnings"]} == {
        "W_NO_PROBABILITY_SCORES",
        "W_NO_MATCHED_EVENTS",
    }


def test_lookback_requires_finalized_game(ready):
    with ready.transaction(), pytest.raises(PerlaError) as exc:
        lookback.open_lookback(ready, "reviewer", "Review outcomes")
    assert exc.value.code == "E_ENDGAME_REQUIRED"
    assert ready.read("lookback.json") is None
