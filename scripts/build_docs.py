"""Build the narrative guide from its HTML source, case files and live EDSL artifacts.

Run: .venv/bin/python scripts/build_docs.py
Requires the optional docs extra (Pygments). No network calls or inference.
"""

import html
import json
import re
import shlex
import shutil
import zipfile
from pathlib import Path

from pygments import highlight
from pygments.formatters import HtmlFormatter
from pygments.lexers import get_lexer_by_name

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
CASE = DOCS / "case/starbucks-queue"
LIVE = ROOT / "examples/starbucks-queue/dated-live"
OUT = DOCS / "run/starbucks-queue-dated"
NAMES = {
    "home": "Starbucks",
    "dutch": "Dutch Bros",
    "dunkin": "Dunkin",
    "buyer": "Morning customers",
}
FMT = HtmlFormatter(cssclass="highlight", wrapcode=True)


def read(path):
    return json.loads(path.read_text())


def esc(value):
    return html.escape(str(value))


def p(value):
    return f"<p>{esc(value)}</p>"


def code(value, language="json"):
    if not isinstance(value, str):
        value = json.dumps(value, indent=2, ensure_ascii=False)
    return highlight(value, get_lexer_by_name(language), FMT)


def detail(title, content):
    return f"<details><summary>{esc(title)}</summary>{content}</details>"


def ep_command(folder, actor):
    return f"""ep run --jobs {folder}/{actor}.jobs.ep \\
  --model_list models/{actor}.ep \\
  --background --wait --timeout 900 --task-timeout 600 \\
  --remote_inference_results_visibility private --fresh \\
  --output {folder}/{actor}.results.ep"""


def actor_commands(move, kind):
    return "\n\n".join(ep_command(f"round-{move}/{kind}", actor) for actor in NAMES)


def construction_steps():
    """The executable authoring walkthrough, derived from the recorded case inputs."""
    game = read(CASE / "game.json")

    def options(prefix, fields):
        args = shlex.split(prefix)
        for key, value in fields.items():
            args.extend(["--" + key.replace("_", "-"), str(value)])
        return shlex.join(args)

    steps = [("decision", [options("perla game decision set", game["decision"])])]
    for criterion in game["criteria"]:
        fields = {k: v for k, v in criterion.items() if k != "id"}
        steps.append(
            (criterion["id"], [options(f"perla game criterion add {criterion['id']}", fields)])
        )
    board = []

    def field(path, value):
        prefix = f"perla game board set {path}"
        if isinstance(value, dict):
            for key, entry in value.items():
                field(f"{path}.{key}", entry)
        elif isinstance(value, list):
            board.append(prefix + " --empty-list")
            board.extend(
                shlex.join(["perla", "game", "board", "append", path, entry]) for entry in value
            )
        elif value is None:
            board.append(prefix + " --unknown")
        elif isinstance(value, str):
            board.append(options(prefix, {"text": value}))
        else:
            board.append(options(prefix, {"number": value}))

    for key, value in game["board"].items():
        field(key, value)
    steps.append(("board", board))
    steps.append(
        ("initialize", ["perla game show", "perla init --delegate dossiers --delegate rulings"])
    )
    for actor in read(CASE / "players.json")["actors"]:
        actor_id = actor["id"]
        commands = [
            options(f"perla actor add {actor_id}", {"name": actor["name"], "role": actor["role"]})
        ]
        dossier = read(CASE / actor["dossier"])
        for fact in dossier["facts"]:
            args = ["perla", "dossier", "fact", actor_id, fact["id"], fact["claim"]]
            for source in fact["sources"]:
                args.extend(["--source", source])
            commands.append(shlex.join(args))
        for key, kind in [
            ("incentives", "incentive"),
            ("constraints", "constraint"),
            ("capabilities", "capability"),
            ("red_lines", "red-line"),
        ]:
            commands.extend(
                shlex.join(["perla", "dossier", kind, actor_id, entry]) for entry in dossier[key]
            )
        steps.append((actor_id, commands))
    steps.append(
        (
            "policy",
            [
                options("perla adjudicate policy set", read(CASE / "live-policy.json")),
                "perla status",
            ],
        )
    )
    return steps


def construction_code(commands):
    # Break option-heavy commands at token boundaries, keeping shell quoting intact.
    formatted = []
    for command in commands:
        tokens = shlex.split(command)
        lines = []
        for token in tokens:
            if token.startswith("--"):
                lines.append([])
            elif not lines:
                lines.append([])
            lines[-1].append(token)
        formatted.append(" \\\n  ".join(shlex.join(line) for line in lines))
    return code("\n\n".join(formatted), "bash")


def artifact(path, label, root=LIVE, output=OUT):
    relative = path.relative_to(root)
    if relative.parts[0] == ".perla":
        relative = Path("state", *relative.parts[1:])
    target = output / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(path, target)
    return f'<a href="{esc(target.relative_to(DOCS))}">{esc(label)}</a>'


def job_section(move, kind, actor):
    folder = LIVE / "execution" / f"round-{move}-{kind}-{actor}"
    if not folder.exists():
        return "", None
    result = ""
    manifest = read(folder / "manifest.json")
    result += p(f"Registered job: {manifest['id']}.")
    result += detail("Read the exact exported context", code(manifest["context"]))
    prompts = read(folder / "prompts.json")
    prompt_text = ""
    for column in prompts["data"]:
        for key in ["system_prompt", "user_prompt"]:
            if key in column:
                prompt_text += f"<h5>{key.replace('_', ' ').title()}</h5>"
                prompt_text += '<pre class="prompt">' + esc(column[key][0]) + "</pre>"
    result += detail("Read the complete rendered elicitation", prompt_text)
    links = [
        artifact(folder / "manifest.json", "manifest"),
        artifact(folder / "prompts.json", "rendered prompts"),
    ]
    package = Path(manifest["package"])
    links.append(artifact(LIVE / ".perla" / package, "EDSL Jobs package"))
    accepted = folder / "accepted.json"
    if not accepted.exists():
        result += p("No accepted live response is available for this job.")
        result += '<p class="artifacts">' + " · ".join(links) + "</p>"
        return result, None
    attempt = read(accepted)["attempt"]
    returned = read(folder / f"result-{attempt}.json")
    answer = read(folder / f"answer-{attempt}.json")[kind]
    result += p(
        f"Returned by {returned['model']['model']} through {returned['model']['inference_service']}; accepted attempt {attempt}."
    )
    actual_prompts = ""
    for key, value in returned["prompt"].items():
        actual_prompts += f"<h5>{esc(key)}</h5>"
        actual_prompts += '<pre class="prompt">' + esc(value["text"]) + "</pre>"
    result += detail("Inspect the prompts recorded in the actual Results", actual_prompts)
    for filename, label in [
        (f"results-{attempt}.ep", "original EDSL Results"),
        (f"result-{attempt}.json", "complete result and metadata"),
        (f"answer-{attempt}.json", "answer JSON"),
        ("accepted.json", "ingestion receipt"),
        (f"remote-{attempt}.json", "remote submission receipt"),
        (f"status-{attempt}.json", "completed remote status"),
    ]:
        links.append(artifact(folder / filename, label))
    execution_package = folder / f"execution-{attempt}.ep"
    if execution_package.exists():
        links.append(artifact(execution_package, "exact execution package"))
    clarification = folder / f"format-clarification-{attempt}.json"
    if clarification.exists():
        result += detail("Formatting guidance added at execution", code(read(clarification)))
        links.append(artifact(clarification, "formatting guidance"))
    result += '<p class="artifacts">' + " · ".join(links) + "</p>"
    result += detail("Read the complete returned answer", code(answer))
    for rejected in sorted(folder.glob("rejection-*.json")):
        result += detail("Rejected attempt: " + rejected.stem, code(read(rejected)))
        result += artifact(rejected, "Rejection record")
        rejected_attempt = rejected.stem.split("-")[-1]
        for filename, label in [
            (f"answer-{rejected_attempt}.json", "rejected answer"),
            (f"results-{rejected_attempt}.ep", "original rejected Results"),
        ]:
            path = folder / filename
            if path.exists():
                result += " · " + artifact(path, label)
    return result, answer


def move_transcript(move, actor, *, compact=False):
    """Keep actual model instructions and gameplay visually separate from editorial prose."""
    folder = LIVE / f"execution/round-{move}-moves-{actor}"
    attempt = read(folder / "accepted.json")["attempt"]
    returned = read(folder / f"result-{attempt}.json")
    answer = read(folder / f"answer-{attempt}.json")["moves"]
    prompt = returned["prompt"]["moves_user_prompt"]["text"]
    start = prompt.index("Play only the actor whose dossier is supplied.")
    end = prompt.index("Prefer one or two coherent actions", start)
    instruction = prompt[start:end].strip()
    month = {1: "January", 2: "February", 3: "March"}[move]
    record = f"round-{move}-{actor}-record"
    href = ("reference.html" if compact else "") + "#" + record
    heading = "h4" if compact else "h3"
    title_id = f"play-{move}-{actor}"
    result = f'<article class="model-play" aria-labelledby="{title_id}">'
    result += '<header class="play-header">'
    result += f'<p class="play-kicker">Recorded model gameplay · Round {move} · {month} 2027</p>'
    result += f'<{heading} id="{title_id}">{esc(NAMES[actor])}’s move</{heading}>'
    result += f'<p class="play-model">Played by <strong>{esc(returned["model"]["model"])}</strong> · {esc(returned["model"]["inference_service"])}</p></header>'
    result += '<div class="play-input"><p class="play-label">01 / Presented to the model</p>'
    result += '<p class="play-caption">Task instruction · verbatim excerpt</p>'
    result += '<blockquote class="play-instruction">' + p(instruction) + "</blockquote>"
    result += (
        '<p class="play-context">'
        + esc(
            f"Also supplied: the proposed decision, the public situation entering round {move} and {NAMES[actor]}’s own dossier. Other players’ uncommitted moves were withheld."
        )
        + f' <a href="{href}">Read the complete input.</a></p></div>'
    )
    result += '<div class="play-output"><p class="play-label">02 / Returned by the model</p>'
    result += '<p class="play-caption">Committed actions · verbatim</p>'
    result += (
        '<blockquote class="play-actions">'
        + "".join(p(action) for action in answer["actions"])
        + "</blockquote>"
    )
    if not compact:
        result += '<p class="play-caption">Model’s rationale · verbatim</p>'
        result += '<blockquote class="play-rationale">' + p(answer["rationale"]) + "</blockquote>"
        result += detail(
            "Model’s resource commitments and expected responses · verbatim",
            "<h4>Resource commitments</h4>"
            + "".join(p(v) for v in answer["resource_commitments"])
            + "<h4>Expected responses</h4>"
            + "".join(p(v) for v in answer["expected_responses"]),
        )
    result += '</div><p class="play-footer">'
    result += f'Simulation record · <a href="{href}">Full prompts, response and original Results</a></p></article>'
    return result


def players():
    result = ""
    titles = {
        "home": "Starbucks: protect the visit and earn back the labor",
        "dutch": "Dutch Bros: acquire a habit without clogging the lane",
        "dunkin": "Dunkin: make the breakfast basket work for franchisees",
        "buyer": "Morning customers: different occasions, different outside options",
    }
    for actor, title in titles.items():
        dossier = read(CASE / f"{actor}.json")
        result += f'<section id="player-{actor}"><h3>{esc(title)}</h3>'
        role = "home" if actor == "home" else "wildcard" if actor == "buyer" else "competitor"
        name = "Morning customers (composite)" if actor == "buyer" else NAMES[actor]
        result += code(f"perla actor add {actor} --name {shlex.quote(name)} --role {role}", "bash")
        reported, assumed = [], []
        for fact in dossier["facts"]:
            refs = [
                f'<a href="{esc(s)}">source</a>'
                for s in fact["sources"]
                if s.startswith("https://")
            ]
            result += f'<p><span class="fact-id">{esc(fact["id"])}</span> {esc(fact["claim"])}'
            if refs:
                result += " " + "; ".join(refs) + "."
            result += "</p>"
            command = (
                f"perla dossier fact {actor} {fact['id']} \\\n  {shlex.quote(fact['claim'])}"
                + "".join(f" \\\n  --source {shlex.quote(source)}" for source in fact["sources"])
            )
            (assumed if fact["claim"].startswith("Assumed") else reported).append(command)
        result += detail(
            f"Add {NAMES[actor]}’s sourced background and financial facts",
            '<div class="dossier-commands">' + code("\n\n".join(reported), "bash") + "</div>",
        )
        result += detail(
            f"Define {NAMES[actor]}’s operating assumptions and information",
            '<div class="dossier-commands">' + code("\n\n".join(assumed), "bash") + "</div>",
        )
        entries = []
        for key, heading in [
            ("incentives", "What the player wants"),
            ("constraints", "What binds"),
            ("capabilities", "What it can do"),
            ("red_lines", "What it must not assume"),
        ]:
            result += f"<h4>{heading}</h4>" + p(" ".join(dossier[key]))
            kind = {
                "incentives": "incentive",
                "constraints": "constraint",
                "capabilities": "capability",
                "red_lines": "red-line",
            }[key]
            entries.extend(
                f"perla dossier {kind} {actor} \\\n  {shlex.quote(text)}" for text in dossier[key]
            )
        result += detail(
            f"Set {NAMES[actor]}’s incentives, constraints, capabilities, and red lines",
            '<div class="dossier-commands">' + code("\n\n".join(entries), "bash") + "</div>",
        )
        result += code(f"perla dossier show {actor}", "bash")
        result += f'<p><a href="case/starbucks-queue/{actor}.json">Complete {esc(NAMES[actor])} dossier</a> · <a href="case/starbucks-queue/{actor}-facts.json">Fact packet</a></p>'
        result += detail("Inspect the exact dossier JSON", code(dossier)) + "</section>"
    return result


def round_section(move):
    month = {1: "January", 2: "February", 3: "March"}[move]
    theme = {1: "protect the queue", 2: "release unused capacity", 3: "expand the test"}[move]
    result = f'<section id="round-{move}"><h2>Round {move} · {month} 2027: {theme}</h2>'
    commentary = DOCS / "round-notes" / f"round-{move}.html"
    if commentary.exists():
        result += commentary.read_text()
    closed = LIVE / "execution" / f"round-{move}-closed.json"
    if not closed.exists():
        result += p(
            "This round has not closed. The execution record below distinguishes exported prompts from accepted model responses; no illustrative response is presented as a live return."
        )
    if move == 1:
        result += p(
            "Each player begins from the same proposed pilot and public market situation. Starbucks also sees its pilot economics and instrumentation remit. Dutch Bros sees its own local budget and shop constraints. Dunkin sees its own franchise economics. The customer player sees the occasion-specific budgets, time limits and outside options in its dossier. None receives another player’s uncommitted move."
        )
    else:
        previous = LIVE / ".perla/board" / f"move-{move - 1}.json"
        if previous.exists():
            result += p(
                "These move packages use the board produced by the previous closed round. The players receive that public situation and their original dossiers, so visible changes must be carried forward explicitly. They do not receive an unrestricted private conversation history."
            )
            result += detail(
                "Read the public board supplied to these players", code(read(previous))
            )
        else:
            result += p(
                "This round depends on the preceding round’s accepted moves, completed challenge process and closed public board. Its prompts have not yet been generated."
            )
    if move > 1:
        result += "<h3>Generate the next decisions from the updated public board</h3>"
        result += detail(
            f"Run round {move} move elicitation with Perla and ep",
            code(
                f"perla job generate moves --move {move} --output round-{move}/moves\n\n"
                + actor_commands(move, "moves")
                + f"\n\nperla ingest moves --from round-{move}/moves",
                "bash",
            ),
        )
    for actor in NAMES:
        section, answer = job_section(move, "moves", actor)
        if section:
            result += f'<section id="round-{move}-{actor}">'
            if answer:
                result += move_transcript(move, actor)
            result += f'<div id="round-{move}-{actor}-record">' + section + "</div></section>"
    result += "<h3>Adjudicate the interaction</h3>"
    result += p(
        "After every move has been ingested, open the neutral control package, execute it, ingest its rulings and freeze the review routing. These commands cannot skip missing player commitments."
    )
    result += code(
        f"""perla adjudicate open --move {move} --output round-{move}/rulings
{ep_command(f"round-{move}/rulings", "control")}
perla ingest rulings --from round-{move}/rulings
perla adjudicate show --move {move}
perla adjudicate route --move {move}""",
        "bash",
    )
    section, answer = job_section(move, "rulings", "control")
    if answer:
        for ruling in answer["rulings"]:
            result += f"<h4>Initial ruling: {esc(ruling['ruling_id'])}</h4>"
            result += p(ruling["rationale"])
            for link in ruling["chain"]:
                result += p(f"{link['link']} ({link['basis']}): {link['claim']}")
            result += p(ruling["outcome"]["description"])
    result += section
    result += "<h3>Push the ruling back to the players</h3>"
    result += p(
        "Execute each exported rebuttal job and ingest it with kind rebuttals. The actor must explicitly accept, or name the challenged causal link and cite its own dossier. These are model responses, not an independent customer panel or human review."
    )
    result += detail(
        f"Elicit and import all four round {move} rebuttals",
        code(
            f"perla job generate rebuttals --move {move} --output round-{move}/rebuttals\n\n"
            + actor_commands(move, "rebuttals")
            + f"\n\nperla ingest rebuttals --from round-{move}/rebuttals",
            "bash",
        ),
    )
    for actor in NAMES:
        section, answer = job_section(move, "rebuttals", actor)
        if section:
            result += f"<h4>{esc(NAMES[actor])} responds</h4>"
            if answer:
                for rebuttal in answer["rebuttals"]:
                    result += p(
                        f"{rebuttal['ruling_id']}: {rebuttal['stance']}. {rebuttal['argument']}"
                    )
            result += section
    result += code(
        f"""perla adjudicate resolve --move {move} --output round-{move}/resolutions
{ep_command(f"round-{move}/resolutions", "control")}
perla ingest resolutions --from round-{move}/resolutions
perla adjudicate show --move {move}
# Close only after the configured review requirements are satisfied.
perla adjudicate close --move {move}""",
        "bash",
    )
    section, answer = job_section(move, "resolutions", "control")
    if section:
        result += "<h3>Control’s final response</h3>"
        if answer:
            for resolution in answer["resolutions"]:
                result += p(
                    f"{resolution['ruling_id']}: {resolution['decision']}. {resolution['reason']}"
                )
                for response in resolution["responses"]:
                    result += p(
                        f"To {NAMES.get(response['actor_id'], response['actor_id'])}: {response['reason']}"
                    )
        result += section
    if closed.exists():
        receipt = read(closed)
        result += "<h3>The situation carried into the next round</h3>"
        result += p(
            f"Closed at {receipt['closed_at']}. Unassessed escalation items: {', '.join(receipt['escalation_debt']) or 'none'}."
        )
        history = receipt["board"].get("public_strategy_history", [])
        if isinstance(history, list):
            if history and isinstance(history[-1], dict):
                history = history[-1:]
            for entry in history:
                if isinstance(entry, dict):
                    for key, value in entry.items():
                        result += p(f"{key.replace('_', ' ').capitalize()}: {value}")
                else:
                    result += p(entry)
        else:
            result += p(history)
        result += detail("Inspect the complete closed board", code(receipt["board"]))
        result += p(
            "The actual measurement fields and any hypothetical estimates must be interpreted under the board’s evidence rule. Closure records the game state; it supplies no field observations."
        )
        result += artifact(closed, "Round closure receipt")
    result += "</section>"
    return result


def model_commands():
    models = [
        ("home", "gpt-4.1", "openai"),
        ("dutch", "gemini-2.5-flash", "google"),
        ("dunkin", "gpt-4.1-mini", "openai"),
        ("buyer", "gemini-2.5-pro", "google"),
        ("control", "gpt-4.1", "openai"),
    ]
    return "mkdir -p models\n" + "\n".join(
        f"ep models create --model {model} --service {service} \\\n  --temperature 0.5 --max-tokens 16000 --output models/{actor}.ep"
        for actor, model, service in models
    )


def stages(move):
    return [
        (
            "Commit every actor's move",
            f"perla job generate moves --move {move} --output round-{move}/moves\n"
            + actor_commands(move, "moves")
            + f"\nperla ingest moves --from round-{move}/moves\nperla status",
        ),
        (
            "Generate and route the initial rulings",
            f"perla adjudicate open --move {move} --output round-{move}/rulings\n"
            + ep_command(f"round-{move}/rulings", "control")
            + f"\nperla ingest rulings --from round-{move}/rulings\nperla adjudicate show --move {move}\nperla adjudicate route --move {move}",
        ),
        (
            "Collect the affected actors' rebuttals",
            f"perla job generate rebuttals --move {move} --output round-{move}/rebuttals\n"
            + actor_commands(move, "rebuttals")
            + f"\nperla ingest rebuttals --from round-{move}/rebuttals",
        ),
        (
            "Resolve and inspect the final outcomes",
            f"perla adjudicate resolve --move {move} --output round-{move}/resolutions\n"
            + ep_command(f"round-{move}/resolutions", "control")
            + f"\nperla ingest resolutions --from round-{move}/resolutions\nperla adjudicate show --move {move}",
        ),
        (
            "Close after review and reconciliation",
            f"perla adjudicate close --move {move}\nperla status",
        ),
    ]


def fill_template(source, replacements):
    body = source.read_text()
    body = re.sub(
        r'<pre data-language="([^"]+)">(.*?)</pre>',
        lambda m: code(html.unescape(m[2]), m[1]),
        body,
        flags=re.S,
    )
    for key, value in replacements.items():
        body = body.replace("{{" + key + "}}", value)
    if re.search(r"\{\{[A-Z_]+\}\}", body):
        raise ValueError(f"Unfilled tutorial placeholder in {source}")
    return body


def build():
    count = len(list((LIVE / "execution").glob("*/accepted.json")))
    closed_count = len(list((LIVE / "execution").glob("round-*-closed.json")))
    status = p(
        f"Original dated run: {count} accepted live EDSL responses and {closed_count} closed rounds."
    )
    audit_path = LIVE / "execution/audit.json"
    if audit_path.exists():
        audit = read(audit_path)
        status += p(
            f"{audit['remote_jobs']} remote jobs; {audit['rejected_attempts']} structurally rejected attempts; {len(audit['escalation_debt'])} unassessed judgment rulings. Final public calendar: {audit['final_calendar_period']}."
        )
        status += "<p>" + artifact(audit_path, "Run audit and Results hashes") + "</p>"
    initial = ROOT / "examples/starbucks-queue/live"
    challenge_folder = initial / "execution/round-1-rebuttals-buyer"
    challenge = read(challenge_folder / "answer-1.json")["rebuttals"]["rebuttals"][0]
    challenge_quote = "<blockquote>" + p(challenge["argument"]) + "</blockquote>"
    exchange = challenge_quote
    for kind, attempt, label in [
        ("rulings", 5, "Original ruling"),
        ("rebuttals", 1, "Customer challenge"),
        ("resolutions", 1, "Control's revision"),
    ]:
        actor = "buyer" if kind == "rebuttals" else "control"
        folder = initial / f"execution/round-1-{kind}-{actor}"
        exchange += f"<h3>{esc(label)}</h3>"
        exchange += detail(label + " in full", code(read(folder / f"answer-{attempt}.json")))
        exchange += (
            "<p>"
            + " · ".join(
                artifact(
                    folder / filename,
                    title,
                    root=initial,
                    output=DOCS / "run/starbucks-queue-initial",
                )
                for filename, title in [
                    (f"results-{attempt}.ep", "Original EDSL Results"),
                    (f"answer-{attempt}.json", "Answer JSON"),
                    ("manifest.json", "Frozen manifest"),
                ]
            )
            + "</p>"
        )
    sources = ""
    for source in read(CASE / "sources.json")["sources"]:
        sources += f'<p><a href="{esc(source["url"])}">{esc(source["title"])}</a>'
        if source["published"]:
            sources += f" ({esc(source['published'])})"
        sources += ". " + esc(source["supports"]) + "</p>"
    excerpts = "".join(move_transcript(1, actor, compact=True) for actor in NAMES)
    home = read(CASE / "home.json")
    fact = next(f for f in home["facts"] if f["id"] == "h6")
    authoring = f"perla dossier fact home h6 \\\n  {shlex.quote(fact['claim'])} \\\n  --source {shlex.quote(fact['sources'][0])}\n\nperla dossier constraint home \\\n  {shlex.quote(home['constraints'][0])}"
    commands = ""
    for move, month in [(1, "January"), (2, "February"), (3, "March")]:
        commands += f'<section id="round-{move}"><h2>{month}: round {move}</h2>'
        commands += p(
            f"Start with closed_move = {move - 1}. Use the same models directory. The named export directories belong only to this round."
        )
        for label, command in stages(move):
            commands += f"<h3>{esc(label)}</h3>" + code(command, "bash")
            if label.startswith("Collect"):
                commands += p(
                    "This case normally involves all four actors. If your rulings affect fewer actors, execute only the files Perla exported for that rebuttal batch."
                )
            if label.startswith("Close"):
                commands += p(
                    f"Success sets closed_move to {move}. Resolve any conflicts and satisfy the selected review policy before closure."
                )
        commands += "</section>"
    verification = p("Fresh CLI verification is being recorded separately from the original case.")
    cli = ROOT / "examples/starbucks-queue/cli-live"
    cli_audit = cli / "audit.json"
    if cli_audit.exists():
        audit = read(cli_audit)
        verification = '<h3 id="cli-verification">Verification using the current ep CLI</h3>'
        verification += p(
            f"A fresh game completed {audit['closed_rounds']} rounds with {audit['accepted_responses']} accepted responses through ep run and the documented named-file handoff. Model choices and case dossiers match the tutorial. It verifies the executable workflow; its new responses are not substituted for the original case's quotations."
        )
        verification += '<p><a href="run/starbucks-queue-cli.zip">Download the complete CLI verification</a> · <a href="run/starbucks-queue-cli-audit.json">CLI audit, commands and remote receipts</a></p>'
        shutil.copyfile(cli_audit, DOCS / "run/starbucks-queue-cli-audit.json")
        with zipfile.ZipFile(
            DOCS / "run/starbucks-queue-cli.zip", "w", zipfile.ZIP_DEFLATED
        ) as archive:
            for path in sorted(cli.rglob("*")):
                if path.is_file() and path.name not in {".lock", ".perla-project.json"}:
                    archive.write(path, Path("starbucks-queue-cli") / path.relative_to(cli))
    replacements = {
        **{f"BUILD_{key.upper()}": construction_code(value) for key, value in construction_steps()},
        "CHALLENGE_QUOTE": challenge_quote,
        "AUTHORING_EXAMPLE": '<div class="dossier-commands">' + code(authoring, "bash") + "</div>",
        "MODEL_COMMANDS": code(model_commands(), "bash"),
        "FIRST_MOVE_COMMANDS": code(actor_commands(1, "moves"), "bash"),
        "MOVE_EXCERPTS": excerpts,
        "CONTROL_COMMANDS": code(stages(1)[1][1], "bash"),
        "REBUTTAL_COMMANDS": code(stages(1)[2][1], "bash"),
        "RESOLUTION_COMMANDS": code(stages(1)[3][1], "bash"),
        "PLAYERS": players(),
        "EXECUTION_STATUS": status,
        "CLI_VERIFICATION": verification,
        "ROUNDS": "".join(round_section(n) for n in range(1, 4)),
        "ARCHIVED_EXCHANGE": exchange,
        "SOURCES": sources,
        "ROUND_COMMANDS": commands,
    }
    pages = [
        (
            "index.html",
            "Perla — the Starbucks queue strategy case",
            fill_template(DOCS / "guide.html", replacements),
        ),
        (
            "reference.html",
            "Perla — evidence and complete transcripts",
            fill_template(DOCS / "reference-source.html", replacements),
        ),
        (
            "commands.html",
            "Perla — execution reference",
            fill_template(DOCS / "commands-source.html", replacements),
        ),
        (
            "build.html",
            "Perla — build the Starbucks case through commands",
            fill_template(DOCS / "build-source.html", replacements),
        ),
    ]
    css = """
:root { color-scheme: light; --ink:#202a2a; --muted:#586361; --accent:#12624b; }
* { box-sizing:border-box; }
html { scroll-behavior:smooth; scroll-padding-top:1.5rem; }
body { margin:0; background:#fbfaf7; color:var(--ink); font:19px/1.72 Georgia,"Times New Roman",serif; }
main { width:min(100% - 3rem, 820px); margin:0 auto; padding:4.5rem 0 3rem; }
header { margin-bottom:3rem; }
h1 { font-size:clamp(2.7rem,6vw,4.2rem); font-weight:normal; line-height:1.08; letter-spacing:-.035em; margin:.5em 0; }
h2 { font-size:2rem; font-weight:normal; line-height:1.2; margin:3rem 0 1.2rem; }
h3 { font-size:1.5rem; line-height:1.3; margin:2.2rem 0 1rem; }
h4 { font-size:1.15rem; margin:1.6rem 0 .3rem; }
h5 { font:600 .9rem/1.5 system-ui,sans-serif; }
p { margin:.9rem 0 1.1rem; }
a { color:var(--accent); text-decoration-thickness:1px; text-underline-offset:3px; overflow-wrap:anywhere; }
a:hover { color:#06412f; }
a:focus-visible, summary:focus-visible { outline:2px solid var(--accent); outline-offset:4px; }
.eyebrow, nav, .artifacts, .fact-id, footer, summary { font-family:system-ui,sans-serif; }
.eyebrow { font-size:.85rem; letter-spacing:.08em; text-transform:uppercase; color:var(--accent); }
.dek { font-size:1.38rem; line-height:1.5; color:var(--muted); }
nav { border-block:1px solid #d8dcd6; font-size:.95rem; padding:1.2rem 0; margin:2rem 0 3rem; }
nav h2 { font:600 1.05rem/1.4 system-ui,sans-serif; margin:0 0 .8rem; }
nav ol { margin:0; padding-left:1.6rem; }
nav li { padding:.2rem 0; }
.fact-id { font-size:.77rem; color:var(--muted); padding-right:.4rem; }
code { font: .84em/1.6 ui-monospace,SFMono-Regular,Consolas,monospace; overflow-wrap:anywhere; }
p code { background:#edf0e9; padding:.1em .25em; }
.highlight { margin:1.3rem 0; background:#f0f2ed; border-left:3px solid #a8bdb0; }
.highlight pre { margin:0; padding:1.1rem 1.2rem; overflow-x:auto; font-size:.87rem; line-height:1.65; tab-size:2; }
.highlight code { font-size:inherit; overflow-wrap:normal; }
.dossier-commands pre { white-space:pre-wrap; overflow-wrap:anywhere; }
details { border-block-end:1px solid #d8dcd6; padding:.8rem 0; margin:1rem 0; }
summary { cursor:pointer; font-size:.95rem; color:var(--accent); }
.prompt { white-space:pre-wrap; overflow-wrap:anywhere; font: .85rem/1.7 ui-monospace,SFMono-Regular,Consolas,monospace; }
.artifacts { font-size:.85rem; line-height:1.7; }
blockquote { margin:1.2rem 0; padding-left:1.3rem; border-left:2px solid #a8bdb0; }
.model-play { margin:2rem 0 2.8rem; border:1px solid #b9c7c2; background:#fff; }
.play-header { margin:0; padding:1.2rem 1.5rem; background:#203e38; color:#fff; }
.play-kicker, .play-model, .play-label, .play-caption, .play-context, .play-footer { font-family:system-ui,sans-serif; }
.play-kicker { margin:0 0 .5rem; font-size:.73rem; line-height:1.6; letter-spacing:.07em; text-transform:uppercase; color:#d4e4de; }
.play-header h3, .play-header h4 { margin:0; color:#fff; font:normal 1.65rem/1.3 Georgia,serif; }
.play-model { margin:.55rem 0 0; font-size:.84rem; color:#e3ece8; }
.play-input { padding:1.2rem 1.5rem; background:#edf1f2; border-bottom:1px solid #cdd6d5; }
.play-output { padding:1.3rem 1.5rem; border-left:4px solid #26765b; }
.play-label { margin:0 0 .7rem; font-size:.78rem; font-weight:750; letter-spacing:.065em; text-transform:uppercase; color:#29483f; }
.play-caption { margin:.9rem 0 .45rem; font-size:.78rem; color:#52625e; }
.play-instruction { margin:0; padding:0; border:0; font: .85rem/1.7 ui-monospace,SFMono-Regular,Consolas,monospace; }
.play-instruction p { margin:0; }
.play-context { margin:.9rem 0 0; font-size:.82rem; line-height:1.65; color:#41524d; }
.play-actions, .play-rationale { margin:0; padding:0; border:0; }
.play-actions { font-size:1.1rem; line-height:1.7; }
.play-actions p { margin:.5rem 0 1rem; }
.play-actions p + p { padding-top:1rem; border-top:1px solid #e2e8e3; }
.play-actions p:last-child, .play-rationale p:last-child { margin-bottom:0; }
.play-rationale { font-size:1rem; }
.play-footer { margin:0; padding:.8rem 1.5rem; border-top:1px solid #d9e1dc; color:#52625e; font-size:.78rem; line-height:1.6; }
.status { border-left:3px solid #a07a37; padding-left:1rem; }
footer { margin-top:3rem; padding-top:1rem; border-top:1px solid #d8dcd6; font-size:.8rem; color:var(--muted); }
@media(max-width:600px) { body { font-size:17px; } main { width:calc(100% - 2rem); padding-top:2.5rem; } h2 { font-size:1.7rem; } .highlight pre { padding:.9rem; } }
@media(max-width:600px) { .play-header, .play-input, .play-output { padding:1rem; } .play-footer { padding:.75rem 1rem; } .play-actions { font-size:1rem; } }
@media(prefers-reduced-motion:reduce) { html { scroll-behavior:auto; } }
@media print { main { width:100%; padding:0; } body { font-size:11pt; background:white; } nav { display:none; } .highlight pre { white-space:pre-wrap; overflow-wrap:anywhere; } }
"""
    css += FMT.get_style_defs(".highlight")
    for filename, title, body in pages:
        page = (
            '<!doctype html>\n<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            + f"<title>{esc(title)}</title><style>"
            + css
            + "</style></head><body><main>"
            + body
            + "</main></body></html>\n"
        )
        (DOCS / filename).write_text(page)
    if closed_count == 3:
        with zipfile.ZipFile(
            DOCS / "run/starbucks-queue-dated.zip", "w", zipfile.ZIP_DEFLATED
        ) as archive:
            for path in sorted(LIVE.rglob("*")):
                if path.is_file() and path.suffix in {".json", ".ep"}:
                    archive.write(path, Path("starbucks-queue") / path.relative_to(LIVE))
    with zipfile.ZipFile(DOCS / "case/starbucks-queue.zip", "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(CASE.rglob("*")):
            if path.is_file():
                archive.write(path, Path("starbucks-queue") / path.relative_to(CASE))
    with zipfile.ZipFile(DOCS / "perla-source.zip", "w", zipfile.ZIP_DEFLATED) as archive:
        for path in [
            ROOT / "pyproject.toml",
            *sorted((ROOT / "perla").rglob("*.py")),
            *sorted(CASE.iterdir()),
        ]:
            if path.is_file():
                archive.write(path, Path("perla") / path.relative_to(ROOT))
        archive.writestr(
            "perla/README.md",
            "# Perla tutorial source bundle\n\nContains the installable Perla package and the complete Starbucks case.\nUse the accompanying tutorial's installation and execution instructions.\nPython 3.11+ and Git are required for installation with the EDSL extra.\n",
        )
    print(
        f"Built tutorial, reference and command pages; {count} original live responses; {closed_count} closed rounds"
    )


if __name__ == "__main__":
    build()
