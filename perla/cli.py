"""One JSON envelope on stdout, including argument errors and help."""

import argparse
import io
import json
import sys
from contextlib import redirect_stdout
from pathlib import Path

from pydantic import ValidationError

from . import (
    __version__,
    actor_bundle,
    adjudication,
    design,
    dossiers,
    endgame,
    handoff,
    lookback,
    reporting,
    rounds,
    selection,
    workflow,
)
from .store import PerlaError, Store, read_json


class HelpRequested(Exception):
    def __init__(self, message):
        self.message = message


class Parser(argparse.ArgumentParser):
    def __init__(self, *args, **kwargs):
        kwargs.setdefault("allow_abbrev", False)
        super().__init__(*args, **kwargs)

    def error(self, message):
        raise PerlaError("E_ARGUMENT", message, "Run perla --help for usage.")

    def print_help(self, file=None):
        raise HelpRequested(self.format_help())


def parser():
    root = Parser(
        prog="perla",
        description="Competitive wargaming: isolated moves and evidence-checked adjudication.",
    )
    root.add_argument(
        "--project",
        type=Path,
        default=None,
        help="Project directory (overrides PERLA_PROJECT and the directory's selected project)",
    )
    root.add_argument("--human", action="store_true", help="Render the envelope using Rich")
    root.add_argument("--version", action="store_true")
    sub = root.add_subparsers(dest="command_name")

    def command(parent, name, path, help_text):
        child = parent.add_parser(name, help=help_text, description=help_text)
        child.set_defaults(command=path)
        return child

    init = command(sub, "init", ["init"], "Initialize from a command-built design or JSON.")
    init.add_argument("--input", type=Path, help="Use this file instead of the local game design")
    init.add_argument("--delegate", action="append", choices=["dossiers", "rulings"], default=[])
    game = command(
        sub, "game", ["game"], "Build the experiment before initialization."
    ).add_subparsers(required=True)
    decision = command(
        game, "decision", ["game", "decision"], "Define the proposed decision."
    ).add_subparsers(required=True)
    decision_set = command(
        decision, "set", ["game", "decision", "set"], "Create or revise the draft decision."
    )
    for field in ("statement", "offer", "segment", "pricing", "launch-period", "scope"):
        decision_set.add_argument(f"--{field}", required=True)
    decision_set.add_argument("--moves", required=True, type=int)
    decision_set.add_argument("--months-per-move", required=True, type=int)
    criteria = command(
        game, "criterion", ["game", "criterion"], "Define observable decision criteria."
    ).add_subparsers(required=True)
    criterion_add = command(
        criteria, "add", ["game", "criterion", "add"], "Add a criterion to the draft."
    )
    criterion_add.add_argument("id")
    for field in ("condition", "observable", "threshold"):
        criterion_add.add_argument(f"--{field}", required=True)
    criterion_add.add_argument("--deadline-move", required=True, type=int)
    criterion_add.add_argument("--replace", action="store_true")
    board = command(
        game, "board", ["game", "board"], "Describe the shared starting situation."
    ).add_subparsers(required=True)
    board_set = command(
        board, "set", ["game", "board", "set"], "Set a typed field; dots address nested objects."
    )
    board_set.add_argument("field")
    value = board_set.add_mutually_exclusive_group(required=True)
    value.add_argument("--text")
    value.add_argument("--number")
    value.add_argument("--unknown", action="store_true")
    value.add_argument("--empty-list", action="store_true")
    board_append = command(
        board,
        "append",
        ["game", "board", "append"],
        "Append a text entry to a list, skipping duplicates.",
    )
    board_append.add_argument("field")
    board_append.add_argument("text")
    command(game, "show", ["game", "show"], "Inspect the draft and initialization requirements.")
    projects = command(
        sub, "project", ["project"], "Select a project for this directory and its children."
    ).add_subparsers(required=True)
    project_set = command(
        projects,
        "set",
        ["project", "set"],
        "Remember an initialized project in the current directory.",
    )
    project_set.add_argument("path", type=Path)
    command(
        projects, "show", ["project", "show"], "Show the effective project and how it was selected."
    )
    command(
        projects, "unset", ["project", "unset"], "Remove the selection stored in this directory."
    )
    command(
        sub,
        "agent-start",
        ["agent-start"],
        "Method, phase, approval checkpoints, and next actions.",
    )
    command(sub, "status", ["status"], "Current workflow state without revealing pending moves.")

    actors = command(sub, "actor", ["actor"], "Declare actors and playing modes.").add_subparsers(
        required=True
    )
    add = command(actors, "add", ["actor", "add"], "Add an actor before move generation.")
    add.add_argument("id")
    add.add_argument("--name", required=True)
    add.add_argument("--role", choices=["home", "competitor", "wildcard"], required=True)
    add.add_argument("--mode", choices=["agent", "human"], default="agent")
    load = command(
        actors,
        "load",
        ["actor", "load"],
        "Load a cast and its complete dossiers from a case manifest.",
    )
    load.add_argument("--input", required=True, type=Path)
    mode = command(
        actors,
        "set-mode",
        ["actor", "set-mode"],
        "Set an actor's playing mode before move generation.",
    )
    mode.add_argument("id")
    mode.add_argument("mode", choices=["agent", "human"])

    dossiers = command(
        sub, "dossier", ["dossier"], "Inspect or edit sourced dossiers."
    ).add_subparsers(required=True)
    show = command(
        dossiers, "show", ["dossier", "show"], "Show an actor's dossier for facilitator review."
    )
    show.add_argument("actor")
    edit = command(
        dossiers,
        "edit",
        ["dossier", "edit"],
        "Supply a sourced dossier, recording author and rationale.",
    )
    edit.add_argument("actor")
    edit.add_argument("--input", required=True, type=Path)
    edit.add_argument("--by", required=True)
    edit.add_argument("--note", required=True)
    fact = command(
        dossiers, "fact", ["dossier", "fact"], "Add a sourced fact directly to an actor's dossier."
    )
    fact.add_argument("actor")
    fact.add_argument("id")
    fact.add_argument("claim")
    fact.add_argument("--source", required=True, action="append")
    fact.add_argument(
        "--replace", action="store_true", help="Replace an existing fact with this id"
    )
    for name in ("incentive", "constraint", "capability", "red-line"):
        entry = command(
            dossiers, name, ["dossier", name], f"Add a {name} directly to an actor's dossier."
        )
        entry.add_argument("actor")
        entry.add_argument("text")

    jobs = command(
        sub, "job", ["job"], "Generate portable, model-free EDSL packages."
    ).add_subparsers(required=True)
    generate = command(
        jobs, "generate", ["job", "generate"], "Export isolated actor jobs; never run models."
    )
    generate.add_argument("kind", choices=["dossiers", "moves", "rebuttals", "novelty", "endgame"])
    generate.add_argument("--actor")
    generate.add_argument("--move", type=int, default=0)
    generate.add_argument("--facts", type=Path)
    generate.add_argument(
        "--output", type=Path, help="Export named Jobs files and a batch receipt to a directory"
    )
    generate.add_argument(
        "--mode",
        choices=["agent", "human"],
        help="Evaluation jobs only; actor modes come from state",
    )

    ingest = command(
        sub, "ingest", ["ingest"], "Ingest one answer with job lineage and provenance."
    )
    ingest.add_argument(
        "kind",
        choices=["dossiers", "moves", "rulings", "rebuttals", "resolutions", "novelty", "endgame"],
    )
    ingest.add_argument("--job")
    ingest.add_argument("--source", type=Path)
    ingest.add_argument(
        "--from", dest="batch", type=Path, help="Import the Results in a named export directory"
    )
    ingest.add_argument(
        "--human-input", action="store_true", help="Read a participant-attributed JSON submission"
    )

    control = command(
        sub, "adjudicate", ["adjudicate"], "Reveal committed moves and inspect pending rulings."
    ).add_subparsers(required=True)
    opening = command(
        control,
        "open",
        ["adjudicate", "open"],
        "Export the control job after every expected move is committed.",
    )
    opening.add_argument("--move", required=True, type=int)
    opening.add_argument("--mode", choices=["agent", "human"], default="agent")
    opening.add_argument("--output", type=Path, help="Export the control Jobs file to a directory")
    show = command(
        control,
        "show",
        ["adjudicate", "show"],
        "Show revealed actions and pending mechanism-chain rulings.",
    )
    show.add_argument("--move", required=True, type=int)
    route = command(
        control,
        "route",
        ["adjudicate", "route"],
        "Freeze escalation policy and rank ruling reviews.",
    )
    route.add_argument("--move", required=True, type=int)
    route.add_argument("--input", type=Path, help="Optional flagged ruling ids and impact scores")
    resolve = command(
        control,
        "resolve",
        ["adjudicate", "resolve"],
        "Generate control's final response to all actor rebuttals.",
    )
    resolve.add_argument("--move", required=True, type=int)
    resolve.add_argument(
        "--output", type=Path, help="Export the resolution Jobs file to a directory"
    )
    assess = command(
        control,
        "assess",
        ["adjudicate", "assess"],
        "Record an individual human assessment of a final ruling.",
    )
    assess.add_argument("--move", required=True, type=int)
    assess.add_argument("--ruling", required=True)
    assess.add_argument("--by", required=True)
    assess.add_argument("--note", required=True)
    reconcile = command(
        control,
        "reconcile",
        ["adjudicate", "reconcile"],
        "Choose among conflicting board outcomes with reasons.",
    )
    reconcile.add_argument("--move", required=True, type=int)
    reconcile.add_argument("--input", required=True, type=Path)
    reconcile.add_argument("--by", required=True)
    close = command(
        control,
        "close",
        ["adjudicate", "close"],
        "Lock approved rulings and atomically create the next public board.",
    )
    close.add_argument("--move", required=True, type=int)
    policy = command(
        control, "policy", ["adjudicate", "policy"], "Configure policy for rounds not yet routed."
    ).add_subparsers(required=True)
    policy_set = command(
        policy,
        "set",
        ["adjudicate", "policy", "set"],
        "Set human-review budget and confidence thresholds.",
    )
    policy_set.add_argument("--input", type=Path)
    policy_set.add_argument("--human-budget", type=int)
    policy_set.add_argument("--auto-confidence", type=float)
    policy_set.add_argument("--judgment-share-threshold", type=float)
    testimony = command(
        sub, "testimony", ["testimony"], "Record attributed human evidence before control opens."
    ).add_subparsers(required=True)
    add_testimony = command(
        testimony, "add", ["testimony", "add"], "Append immutable, sourced and scoped testimony."
    )
    add_testimony.add_argument("--input", required=True, type=Path)
    add_testimony.add_argument("--by", required=True)
    command(
        testimony, "list", ["testimony", "list"], "Inspect registered testimony and attribution."
    )

    flow = command(
        sub, "workflow", ["workflow"], "Approval checkpoints and invariant audits."
    ).add_subparsers(required=True)
    approve = command(
        flow,
        "approve",
        ["workflow", "approve"],
        "Record actual human approval of dossiers or final round rulings.",
    )
    approve.add_argument("checkpoint", choices=["dossiers", "rulings"])
    approve.add_argument("--move", type=int)
    approve.add_argument("--by", required=True)
    approve.add_argument("--note", required=True)
    validate = command(
        flow,
        "validate",
        ["workflow", "validate"],
        "Audit registrations, package contents, lineage, and move hashes.",
    )
    validate.add_argument("--strict", action="store_true")
    finish = command(
        sub,
        "endgame",
        ["endgame"],
        "Finalize evaluated criteria, owned insights, and the prediction registry.",
    )
    finish.add_argument("--accept-gap", action="append", default=[], metavar="CRITERION_ID")
    finish.add_argument("--waive-insight", action="append", default=[], metavar="INSIGHT_ID")
    finish.add_argument("--by", help="Human accepting gaps or waiving missing ownership")
    finish.add_argument("--note", help="Rationale for explicit waivers")
    revisions = finish.add_subparsers()
    command(
        revisions,
        "show",
        ["endgame", "show"],
        "Inspect the current assessment, provenance, and unresolved completion gates.",
    )
    revise = command(
        revisions,
        "revise",
        ["endgame", "revise"],
        "Record a human correction to an ingested assessment before finalization.",
    )
    revise.add_argument("--input", required=True, type=Path)
    revise.add_argument("--by", required=True)
    revise.add_argument("--note", required=True)
    reports = command(
        sub, "report", ["report"], "Export finalized report artifacts."
    ).add_subparsers(required=True)
    context = command(
        reports, "context", ["report", "context"], "Export the canonical JSON writing bundle."
    )
    context.add_argument("--output", type=Path)
    context.add_argument("--audience", default="Strategy decision-makers")
    context.add_argument(
        "--emphasis",
        default="Competitive collisions, invalidated assumptions, and follow-up actions",
    )
    html_report = command(
        reports, "html", ["report", "html"], "Render a deterministic standalone HTML report."
    )
    html_report.add_argument("--output", type=Path)
    lookback_report = command(
        reports,
        "lookback",
        ["report", "lookback"],
        "Export the current calibration score and evidence snapshot.",
    )
    lookback_report.add_argument("--output", type=Path)
    lookbacks = command(
        sub, "lookback", ["lookback"], "Record sourced outcomes and score frozen predictions."
    ).add_subparsers(required=True)
    lookback_open = command(
        lookbacks, "open", ["lookback", "open"], "Open an attributed lookback after endgame."
    )
    lookback_open.add_argument("--by", required=True)
    lookback_open.add_argument("--note", required=True)
    command(
        lookbacks,
        "show",
        ["lookback", "show"],
        "Show predictions, observation history, and score freshness.",
    )
    record = command(
        lookbacks,
        "record",
        ["lookback", "record"],
        "Record or revise sourced observations with attribution.",
    )
    record.add_argument("--input", required=True, type=Path)
    record.add_argument("--by", required=True)
    record.add_argument("--note", required=True)
    scoring = command(
        lookbacks,
        "score",
        ["lookback", "score"],
        "Compute Brier scores and calibration bins with an explicit date cutoff.",
    )
    scoring.add_argument("--as-of", required=True)
    exports = command(
        sub, "export", ["export"], "Export downstream evidence packages."
    ).add_subparsers(required=True)
    uncertainty = command(
        exports,
        "uncertainties",
        ["export", "uncertainties"],
        "Export declared branches as competing hypotheses and accepted criterion gaps.",
    )
    uncertainty.add_argument("--output", type=Path)
    return root


METHOD = (
    "Test a specific decision against isolated competitors. Pre-register falsifiable kill criteria, "
    "ground actors in sourced dossiers, record review approval, and commit hashed moves before reveal. "
    "Perla exports model-free EDSL jobs; the operator chooses and runs models or collects human responses. "
    "Control reveals moves only after all commitments, then proposes evidence-checked mechanism-chain rulings. "
    "Route escalations, collect one rebuttal per affected actor/ruling, resolve with reasons, and record "
    "human ruling approval before closing each round. Evaluate every criterion, record insight owners "
    "and prediction resolution criteria, then finalize endgame and export reports. At lookback, record "
    "sourced actual outcomes and score registered probabilities. Panel aggregation and asynchronous "
    "ruling reopening remain pending."
)


def dispatch(args):
    command = args.command
    warnings = []
    if command == ["project", "set"]:
        return selection.select(args.path), [], []
    if command == ["project", "unset"]:
        return selection.unset(), [], []
    # Initialization defaults to cwd, rather than implicitly targeting an existing selection.
    selected, selected_by = (
        selection.resolve(args.project)
        if command != ["init"] and command[:1] != ["game"]
        else (args.project or Path.cwd(), "argument" if args.project else "current_directory")
    )
    args.project = selected
    if command[:1] == ["game"]:
        if command == ["game", "decision", "set"]:
            data = design.set_decision(
                selected,
                {
                    key: getattr(args, key)
                    for key in (
                        "statement",
                        "offer",
                        "segment",
                        "pricing",
                        "launch_period",
                        "scope",
                        "moves",
                        "months_per_move",
                    )
                },
            )
        elif command == ["game", "criterion", "add"]:
            data = design.add_criterion(
                selected,
                {
                    key: getattr(args, key)
                    for key in (
                        "id",
                        "condition",
                        "observable",
                        "threshold",
                        "deadline_move",
                    )
                },
                args.replace,
            )
        elif command == ["game", "board", "set"]:
            entry = (
                args.text
                if args.text is not None
                else design.number(args.number)
                if args.number is not None
                else []
                if args.empty_list
                else None
            )
            data = design.board_value(selected, args.field, entry)
        elif command == ["game", "board", "append"]:
            data = design.board_value(selected, args.field, args.text, append=True)
        else:
            data = design.show(selected)
        return data, [], []
    if command == ["project", "show"]:
        store = Store.discover(selected)
        return {"project": str(store.root), "selected_by": selected_by}, [], []
    if command == ["init"]:
        data = (
            workflow.initialize(args.project, read_json(args.input), args.delegate)
            if args.input
            else design.initialize(args.project, args.delegate)
        )
        store = Store(args.project)
        with store.transaction():
            _, actions = workflow.status(store)
        return data, warnings, actions
    try:
        store = Store.discover(args.project)
    except PerlaError as exc:
        if command == ["agent-start"] and exc.code == "E_NO_PROJECT":
            return (
                {"method": METHOD, "phase": "uninitialized", "checkpoints": ["dossiers"]},
                [],
                [
                    workflow.action(
                        "facilitate",
                        "Define the decision, observable kill criteria, and public initial board; initialize from JSON.",
                    )
                ],
            )
        raise
    with store.transaction():
        if command in (["status"], ["agent-start"]):
            data, actions = workflow.status(store)
            if command == ["agent-start"]:
                data["method"] = METHOD
            return data, warnings, actions
        if command == ["actor", "add"]:
            data = workflow.add_actor(
                store, {k: getattr(args, k) for k in ("id", "name", "role", "mode")}
            )
        elif command == ["actor", "load"]:
            data = actor_bundle.load(store, args.input)
        elif command == ["actor", "set-mode"]:
            data = workflow.set_mode(store, args.id, args.mode)
        elif command == ["dossier", "edit"]:
            data = workflow.save_dossier(
                store, args.actor, read_json(args.input), args.by, args.note
            )
        elif command == ["dossier", "show"]:
            workflow.actor(store, args.actor)
            data = dossiers.show(store, args.actor)
            workflow.require(
                data is not None, "E_DOSSIER", "No dossier is recorded for this actor."
            )
        elif command == ["dossier", "fact"]:
            data = dossiers.add_fact(
                store, args.actor, args.id, args.claim, args.source, args.replace
            )
        elif command[:1] == ["dossier"] and command[1] in dossiers.FIELDS:
            data = dossiers.add_entry(store, args.actor, command[1], args.text)
        elif command == ["job", "generate"]:
            data = workflow.generate(
                store,
                args.kind,
                args.actor,
                args.move,
                read_json(args.facts) if args.facts else None,
                args.mode,
            )
        elif command == ["ingest"]:
            if args.batch:
                workflow.require(
                    not args.job and not args.source,
                    "E_ARGUMENT",
                    "Use --from or --job with --source, not both.",
                )
                data = handoff.ingest_batch(store, args.kind, args.batch, args.human_input)
            else:
                workflow.require(
                    args.job and args.source,
                    "E_ARGUMENT",
                    "Supply --from DIRECTORY or both --job and --source.",
                )
                data = workflow.ingest(store, args.kind, args.job, args.source, args.human_input)
        elif command == ["adjudicate", "open"]:
            data = adjudication.open_round(store, args.move, args.mode)
        elif command == ["adjudicate", "show"]:
            data = adjudication.show_round(store, args.move)
        elif command == ["adjudicate", "route"]:
            data = rounds.route_round(
                store, args.move, read_json(args.input) if args.input else None
            )
        elif command == ["adjudicate", "resolve"]:
            data = rounds.generate_resolution(store, args.move)
        elif command == ["adjudicate", "assess"]:
            data = rounds.assess(store, args.move, args.ruling, args.by, args.note)
        elif command == ["adjudicate", "reconcile"]:
            data = rounds.reconcile(store, args.move, read_json(args.input), args.by)
        elif command == ["adjudicate", "close"]:
            data = rounds.close_round(store, args.move)
        elif command == ["adjudicate", "policy", "set"]:
            options = {
                key: getattr(args, key)
                for key in ("human_budget", "auto_confidence", "judgment_share_threshold")
                if getattr(args, key) is not None
            }
            workflow.require(
                bool(args.input) != bool(options),
                "E_ARGUMENT",
                "Supply either --input or policy options, not both.",
            )
            workflow.require(
                args.input or len(options) == 3,
                "E_ARGUMENT",
                "Specify --human-budget, --auto-confidence and --judgment-share-threshold together.",
            )
            data = rounds.set_policy(store, read_json(args.input) if args.input else options)
        elif command == ["testimony", "add"]:
            data = adjudication.add_testimony(store, read_json(args.input), args.by)
        elif command == ["testimony", "list"]:
            data = {"testimony": adjudication.testimony_records(store, workflow.project(store))}
        elif command == ["workflow", "approve"]:
            if args.checkpoint == "rulings":
                workflow.require(
                    args.move is not None, "E_ARGUMENT", "Ruling approval requires --move."
                )
                data = rounds.approve(store, args.move, args.by, args.note)
            else:
                workflow.require(
                    args.move is None, "E_ARGUMENT", "Dossier approval does not take --move."
                )
                data = workflow.approve_dossiers(store, args.by, args.note)
        elif command == ["workflow", "validate"]:
            data, warnings = workflow.validate(store, args.strict)
        elif command == ["endgame"]:
            data = endgame.finalize(store, args.accept_gap, args.waive_insight, args.by, args.note)
            warnings = data["health"]["warnings"]
        elif command == ["endgame", "revise"]:
            data = endgame.revise(store, args.input, read_json(args.input), args.by, args.note)
        elif command == ["endgame", "show"]:
            data = endgame.show(store)
        elif command == ["report", "context"]:
            data, warnings = reporting.export_context(
                store, args.output, args.audience, args.emphasis
            )
        elif command == ["report", "html"]:
            data, warnings = reporting.export_html(store, args.output)
        elif command == ["report", "lookback"]:
            data, warnings = reporting.export_lookback(store, args.output)
        elif command == ["lookback", "open"]:
            data = lookback.open_lookback(store, args.by, args.note)
        elif command == ["lookback", "show"]:
            data = lookback.show(store)
        elif command == ["lookback", "record"]:
            data = lookback.record_outcomes(store, read_json(args.input), args.by, args.note)
        elif command == ["lookback", "score"]:
            data = lookback.score(store, args.as_of)
            warnings = data["result"]["warnings"]
        elif command == ["export", "uncertainties"]:
            data, warnings = reporting.export_uncertainties(store, args.output)
        else:
            raise PerlaError("E_ARGUMENT", "Unknown command.")
        if (
            command in (["job", "generate"], ["adjudicate", "open"], ["adjudicate", "resolve"])
            and args.output
        ):
            data["export"] = handoff.export_batch(store, data, args.output)
        _, actions = workflow.status(store)
    return data, warnings, actions


def main(argv=None):
    arguments = list(sys.argv[1:] if argv is None else argv)
    human = "--human" in arguments
    command = []
    result = {
        "schema_version": "1.0",
        "ok": True,
        "command": command,
        "warnings": [],
        "next_actions": [],
    }
    exit_code = 0
    try:
        cli = parser()
        # Dependency diagnostics must not corrupt the machine-readable stdout envelope.
        with redirect_stdout(io.StringIO()) as diagnostics:
            args = cli.parse_args(arguments)
            command = getattr(args, "command", [])
            if command and hasattr(args, "kind"):
                command = [*command, args.kind]
            result["command"] = command
            if args.version:
                result["data"] = {"version": __version__}
            elif not command:
                result["data"] = {"help": cli.format_help()}
            else:
                result["data"], result["warnings"], result["next_actions"] = dispatch(args)
        if diagnostics.getvalue():
            print(diagnostics.getvalue(), file=sys.stderr, end="")
    except HelpRequested as exc:
        result["data"] = {"help": exc.message}
    except (PerlaError, ValidationError, OSError, ValueError, KeyError, TypeError) as exc:
        exit_code = 1
        result["ok"] = False
        if isinstance(exc, PerlaError):
            result["error"] = {
                "code": exc.code,
                "message": exc.message,
                "remediation": exc.remediation,
            }
        else:
            code = (
                "E_INPUT"
                if isinstance(exc, (ValidationError, ValueError))
                else "E_STATE"
                if isinstance(exc, (KeyError, TypeError))
                else "E_IO"
            )
            result["error"] = {
                "code": code,
                "message": str(exc),
                "remediation": "Check the input, project state, and file permissions.",
            }
    if human:
        from rich.console import Console

        Console().print_json(data=result)
    else:
        print(json.dumps(result, ensure_ascii=False, allow_nan=False))
    if argv is None:
        raise SystemExit(exit_code)
    return exit_code
