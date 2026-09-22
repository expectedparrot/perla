import json

from perla.edsl_adapter import response_format_notes


def citation_ids(notes):
    return json.loads(notes.split("): ", 1)[1].split(". Bare fact", 1)[0])


def test_control_notes_name_exact_frozen_evidence_for_rulings_and_resolutions():
    context = {
        "dossiers": {"home": {"facts": [{"id": "h5"}]}, "dutch": {"facts": [{"id": "d8"}]}},
        "testimony": [{"id": "interview"}],
    }
    for kind, payload in [("rulings", context), ("resolutions", {"control": context})]:
        notes = response_format_notes({"kind": kind, "context": payload})
        assert citation_ids(notes) == ["d.dutch.d8", "d.home.h5", "t.interview"]
        assert "exactly the same keys and values" in notes


def test_rebuttal_notes_only_expose_assigned_dossier():
    notes = response_format_notes(
        {
            "kind": "rebuttals",
            "actor_id": "home",
            "context": {
                "dossier": {"facts": [{"id": "h5"}]},
                "rulings": {"r1": {"cite": ["d.dutch.d8"]}},
            },
        }
    )
    assert citation_ids(notes) == ["d.home.h5"]
    assert response_format_notes({"kind": "moves", "context": {}}) == ""


def test_round_instruction_uses_registered_round_not_previous_board_label():
    from perla.edsl_adapter import round_instruction

    context = {"board": {"calendar_period": "February 2027", "period_ends": {"3": "2027-03-31"}}}
    for kind, payload in [("moves", context), ("resolutions", {"control": context})]:
        note = round_instruction({"kind": kind, "move": 3, "context": payload})
        assert "Current game round: 3" in note
        assert "2027-03-31" in note
        assert "February" not in note
    assert round_instruction({"kind": "dossiers", "move": 0, "context": {}}) == ""
