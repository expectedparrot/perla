"""Deterministic report artifacts derived from a finalized, audited game."""

import html
import json
from pathlib import Path

from . import endgame, lookback, workflow
from .store import atomic_json, atomic_text
from .workflow import require


def uncertainties(context):
    result = []
    for reference, record in context["rulings"].items():
        branches = record["content"]["branches"]
        if branches:
            result.append(
                {
                    "source": reference,
                    "question": branches["uncertainty"],
                    "hypotheses": branches["options"],
                    "played": branches["played"],
                    "evidence": record["content"]["chain"],
                    "provenance": record["provenance"],
                }
            )
    return result


def bundle(
    store,
    audience="Strategy decision-makers",
    emphasis="Competitive collisions, invalidated assumptions, and follow-up actions",
):
    require(
        bool(audience.strip()) and bool(emphasis.strip()),
        "E_INPUT",
        "The writing brief needs an audience and emphasis.",
    )
    workflow.validate(store)
    meta = workflow.project(store)
    final = endgame.checked_record(store, meta, "endgame")
    require(
        final is not None,
        "E_UNEVALUATED_CRITERIA",
        "Finalize endgame before exporting a report.",
        "Run perla endgame; resolve its criterion or ownership gates first.",
    )
    context = endgame.game_context(store, meta)
    assessment = endgame.checked_record(store, meta, "endgame_assessment")
    evaluations = {e["criterion_id"]: e for e in assessment["answer"]["evaluations"]}
    lookback_record = lookback.checked(store, meta)
    return {
        "schema_version": "1.0",
        "artifact_type": "perla.report_context",
        "project_id": meta["id"],
        "finalized_at": final["finalized_at"],
        "context_hash": final["context_hash"],
        **context,
        "criteria": [
            {**criterion, "evaluation": evaluations[criterion["id"]]}
            for criterion in context["criteria"]
        ],
        "insights": store.read("insights.json"),
        "predictions": store.read("predictions.json"),
        "uncertainties": uncertainties(context),
        "health": final["health"],
        "waivers": final["waivers"],
        "evaluation_provenance": assessment["provenance"],
        "evaluation_history": assessment.get("history", []),
        "lookback": lookback.show_data(lookback_record, store.read("predictions.json"))
        if lookback_record
        else None,
        "provenance_index": {
            ref: record["provenance"]
            for ref, record in {**context["moves"], **context["rulings"]}.items()
        },
        "writing_brief": {
            "audience": audience.strip(),
            "emphasis": emphasis.strip(),
            "caveats": [
                "Game outcomes are adjudicated findings, not observations of future reality.",
                "Keep simulation, human input, and individual review provenance distinct.",
                "Insight novelty is a heuristic comparison, not proof of emergence.",
                *[w["message"] for w in final["health"]["warnings"]],
            ],
        },
    }


def output_path(store, output, filename):
    path = Path(output).resolve() if output else store.path(f"reports/{filename}")
    resolved = path.resolve()
    state = store.state.resolve()
    if resolved.is_relative_to(state):
        relative = resolved.relative_to(state)
        require(
            bool(relative.parts) and relative.parts[0] == "reports",
            "E_OUTPUT",
            "Derived exports may not overwrite canonical .perla state.",
        )
    return path


def export_context(
    store,
    output=None,
    audience="Strategy decision-makers",
    emphasis="Competitive collisions, invalidated assumptions, and follow-up actions",
):
    data = bundle(store, audience, emphasis)
    path = output_path(store, output, "context.json")
    atomic_json(path, data)
    return {
        "path": str(path),
        "context_hash": data["context_hash"],
        "artifact_type": data["artifact_type"],
    }, data["health"]["warnings"]


def export_uncertainties(store, output=None):
    data = bundle(store)
    result = {
        "schema_version": "1.0",
        "artifact_type": "perla.uncertainties",
        "project_id": data["project_id"],
        "context_hash": data["context_hash"],
        "uncertainties": data["uncertainties"],
        "criterion_gaps": [
            c for c in data["criteria"] if c["evaluation"]["verdict"] == "unevaluated"
        ],
        "waivers": data["waivers"],
    }
    path = output_path(store, output, "uncertainties.json")
    atomic_json(path, result)
    return {
        "path": str(path),
        "uncertainty_count": len(result["uncertainties"]),
        "criterion_gap_count": len(result["criterion_gaps"]),
    }, data["health"]["warnings"]


def render_calibration(result):
    """Show observed frequencies against mean forecasts; empty bins stay absent."""

    def escape(value):
        return html.escape(str(value), quote=True)

    parts = [
        "<p>As of " + escape(result["as_of"]) + ". Lower Brier scores are better.</p>",
        "<table><tr><th>Population</th><th>Registered</th><th>Resolved</th><th>Scored</th><th>Brier score</th></tr>",
    ]
    for population, stats in result["by_population"].items():
        parts.append(
            "<tr>"
            + "".join(
                "<td>" + escape(value) + "</td>"
                for value in (
                    population,
                    stats["registered"],
                    stats["resolved"],
                    stats["scored"],
                    f"{stats['brier_score']:.4f}"
                    if stats["brier_score"] is not None
                    else "Unavailable",
                )
            )
            + "</tr>"
        )
    parts.append(
        "</table><p>Plots show occupied probability bins. Points use mean registered probability and observed frequency; the diagonal indicates perfect calibration. Counts are forecasts, not independent events.</p>"
    )
    plot_groups = {
        **{"Population: " + key: value for key, value in result["by_population"].items()},
        **{"Basis: " + key: value for key, value in result["by_basis"].items()},
    }
    for population, stats in plot_groups.items():
        occupied = [b for b in stats["calibration_bins"] if b["count"]]
        if not occupied:
            continue
        label = escape(population)
        parts.append(
            "<h3>"
            + label
            + "</h3><svg viewBox='0 0 320 290' width='320' role='img' aria-label='Calibration for "
            + label
            + "'><title>"
            + label
            + " calibration</title><path d='M40 20 V240 H260 M40 240 L260 20' fill='none' stroke='#777'/><text x='40' y='260'>0</text><text x='255' y='260'>1</text><text x='22' y='25'>1</text><text x='80' y='282'>Mean probability</text><text transform='translate(14 210) rotate(-90)'>Observed frequency</text>"
        )
        points = " ".join(
            f"{40 + b['mean_probability'] * 220:.3f},{240 - b['observed_frequency'] * 220:.3f}"
            for b in occupied
        )
        parts.append("<polyline points='" + points + "' fill='none' stroke='#145a91'/>")
        for b in occupied:
            parts.append(
                f"<circle cx='{40 + b['mean_probability'] * 220:.3f}' cy='{240 - b['observed_frequency'] * 220:.3f}' r='4' fill='#145a91'><title>n={b['count']}, probability={b['mean_probability']:.3f}, frequency={b['observed_frequency']:.3f}</title></circle>"
            )
        parts.append("</svg>")
    return "\n".join(parts)


def render_html(data):
    def escape(value):
        return html.escape(str(value), quote=True)

    def pretty(value):
        return (
            "<pre>"
            + escape(json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False))
            + "</pre>"
        )

    def listing(values):
        return "<ul>" + "".join("<li>" + escape(v) + "</li>" for v in values) + "</ul>"

    parts = [
        "<!doctype html><html lang='en'><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>",
        "<title>Perla game report</title><style>body{font:16px/1.6 system-ui,sans-serif;max-width:1050px;margin:2rem auto;padding:0 1rem;color:#202c36;background:#f7f8fa}h1,h2,h3{line-height:1.2}article,section{background:white;padding:1.2rem;margin:1rem 0;border:1px solid #dce2e8;border-radius:6px}pre{white-space:pre-wrap;overflow-wrap:anywhere;font-size:13px}.badge{background:#e5edf5;padding:.2rem .5rem;border-radius:3px}td,th{text-align:left;vertical-align:top;padding:.5rem;border-bottom:1px solid #ddd}table{width:100%;border-collapse:collapse}a{color:#145a91}</style><body>",
        "<h1>Competitive wargame report</h1><p>" + escape(data["decision"]["statement"]) + "</p>",
        "<p>Finalized "
        + escape(data["finalized_at"])
        + " · Project "
        + escape(data["project_id"])
        + "</p>",
        "<section><h2>Interpretation and limitations</h2>"
        + listing(data["writing_brief"]["caveats"])
        + "</section>",
        "<section><h2>Criterion verdicts</h2><p>Evaluation origin: "
        + escape(data["evaluation_provenance"]["origin"])
        + " · Evaluator: "
        + escape(data["evaluation_provenance"]["model"])
        + "</p>",
    ]
    for criterion in data["criteria"]:
        evaluation = criterion["evaluation"]
        links = " ".join(
            '<a href="#' + escape(ref) + '">' + escape(ref) + "</a>"
            for ref in evaluation["ruling_refs"] + evaluation["board_refs"]
        )
        parts.append(
            "<article><h3>"
            + escape(criterion["id"])
            + " — "
            + escape(evaluation["verdict"])
            + "</h3><p>"
            + escape(criterion["condition"])
            + "</p><p>"
            + escape(evaluation["rationale"])
            + "</p><p>"
            + links
            + "</p></article>"
        )
    parts.append("</section><section><h2>Board evolution</h2>")
    for ref, board in data["boards"].items():
        parts.append(
            '<article id="'
            + escape(ref)
            + '"><h3>'
            + escape(ref)
            + "</h3>"
            + pretty(board)
            + "</article>"
        )
    parts.append("</section><section><h2>Final rulings</h2>")
    for ref, record in data["rulings"].items():
        ruling = record["content"]
        parts.append(
            '<article id="'
            + escape(ref)
            + '"><h3>'
            + escape(ref)
            + '</h3><span class="badge">'
            + escape(ruling["basis"])
            + "</span><p>"
            + escape(ruling["outcome"]["description"])
            + "</p>"
        )
        parts.append(
            listing(
                link["link"] + ": " + link["claim"] + " (" + link["basis"] + ")"
                for link in ruling["chain"]
            )
        )
        parts.append(
            "<details><summary>Evidence, resolution, and provenance</summary>"
            + pretty(record)
            + "</details></article>"
        )
    parts.append("</section><section><h2>Insights and ownership</h2>")
    for insight in data["insights"]:
        parts.append(
            "<article><p>"
            + escape(insight["text"])
            + "</p><p>"
            + escape(insight["novelty"])
            + " · Owner: "
            + escape(insight["owner"] or "Waived")
            + " · Due: "
            + escape(insight["due_date"] or "Waived")
            + "</p></article>"
        )
    parts.append(
        "</section><section><h2>Prediction registry</h2><table><tr><th>Source</th><th>Prediction</th><th>Resolve by</th><th>Resolution criteria</th><th>Origin / probability</th></tr>"
    )
    for prediction in data["predictions"]:
        parts.append(
            "<tr>"
            + "".join(
                "<td>" + escape(prediction[key]) + "</td>"
                for key in ("source", "statement", "due_date", "resolution_criteria")
            )
            + "<td>"
            + escape(prediction["source_provenance"]["origin"])
            + " / "
            + escape(
                prediction["probability"]
                if prediction["probability"] is not None
                else "Not elicited"
            )
            + "</td></tr>"
        )
    parts.append("</table></section>")
    if data.get("lookback"):
        observed = data["lookback"]
        parts.extend(
            [
                "<section><h2>Lookback: observed outcomes</h2>",
                pretty({"events": observed["events"], "score_stale": observed["score_stale"]}),
            ]
        )
        latest = observed["latest_score"]
        if latest:
            parts.append(
                "<h3>"
                + (
                    "Historical score — new evidence requires rescoring"
                    if observed["score_stale"]
                    else "Calibration scores"
                )
                + "</h3>"
            )
            parts.append(render_calibration(latest["result"]))
            parts.append(
                "<details><summary>Scores by basis, matched events, and scoring method</summary>"
                + pretty(
                    {
                        k: v
                        for k, v in latest["result"].items()
                        if k not in {"predictions", "by_population"}
                    }
                )
                + "</details>"
            )
        parts.append("</section>")
    parts.extend(
        [
            "<section><h2>Health checks and escalation debt</h2>",
            pretty(data["health"]),
            "</section><section><h2>Approval waivers and accepted gaps</h2>",
            pretty({"delegations": data["delegations"], "endgame_waivers": data["waivers"]}),
            "</section><section><h2>Uncertainties</h2>",
            pretty(data["uncertainties"]),
            "</section><section><h2>Evidence and audit trail</h2><details><summary>Decision, dossiers, and testimony</summary>",
            pretty(
                {
                    "decision": data["decision"],
                    "dossiers": data["dossiers"],
                    "testimony": data["testimony"],
                }
            ),
            "</details><details><summary>Original rulings and round reviews</summary>",
            pretty(
                {"initial_rulings": data["initial_rulings"], "round_reviews": data["round_reviews"]}
            ),
            "</details><details><summary>Evaluation provenance and revision history</summary>",
            pretty(
                {"provenance": data["evaluation_provenance"], "history": data["evaluation_history"]}
            ),
            "</details>",
            "</section></body></html>",
        ]
    )
    return "\n".join(parts)


def export_html(store, output=None):
    data = bundle(store)
    path = output_path(store, output, "report.html")
    atomic_text(path, render_html(data))
    return {"path": str(path), "context_hash": data["context_hash"]}, data["health"]["warnings"]


def export_lookback(store, output=None):
    data = lookback.show(store)
    require(data["latest_score"] is not None, "E_LOOKBACK_SCORE", "Run perla lookback score first.")
    require(
        not data["score_stale"],
        "E_LOOKBACK_SCORE",
        "Observations changed; rescore before exporting lookback.",
    )
    meta = workflow.project(store)
    result = {
        "schema_version": "1.0",
        "artifact_type": "perla.lookback_score",
        "project_id": meta["id"],
        **data["latest_score"],
    }
    path = output_path(store, output, "lookback.json")
    atomic_json(path, result)
    return {"path": str(path), "score_id": result["id"]}, result["result"]["warnings"]
