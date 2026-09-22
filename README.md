# perla

<p align="center">
  <img src="docs/assets/perla-artwork.jpg" width="640" alt="Perla artwork: a green parrot wearing a burgundy hooded cloak, inside expectation brackets">
</p>

A local-first CLI for competitive wargaming. It tests a specific strategic
decision against grounded actors with isolated, committed moves.

**Early implementation:** the CLI runs from initialization through 1–3 adjudicated
rounds, criterion evaluation, prediction registration, and final JSON/HTML reports.
Sourced lookback outcomes and calibration scoring are also supported. The full method is in [SPEC.md](SPEC.md); unfinished
capabilities are listed in [ROADMAP.md](ROADMAP.md).

Start with the [narrative field guide](docs/index.html): a sourced Starbucks
strategy case, detailed player dossiers, downloadable setup files, and a guide to
move elicitation, neutral adjudication and player rebuttals. Public company facts and hypothetical pilot assumptions are labeled
separately throughout. The guide includes three completed live EDSL rounds with
30 accepted responses, original Jobs/Results packages, and a downloadable audit.
The simulation supplies hypotheses and arguments, not actual pilot measurements.

## Install

Python 3.11+ on macOS or Linux:

```sh
uv venv
uv pip install -e '.[dev,edsl]'
source .venv/bin/activate
perla agent-start
```

EDSL is optional for setup and dossier review, and required for job generation,
Results ingestion, and package audits. The EDSL extra follows the neighboring
Expected Parrot CLIs' GitHub `main` dependency. `perla` never runs inference,
chooses models, sends survey links, or manages credentials.

## Select a project once

After initializing a game, remember it for your current directory and its children:

```sh
perla project set /path/to/game
perla project show
perla status
perla project unset
```

The selection lives in `.perla-project.json` in the directory where you ran `set`.
An explicit `--project` overrides `PERLA_PROJECT`, which overrides directory
selection. Without those overrides, Perla walks up from the current directory,
using the nearest actual project or selection. A real project in the working
directory takes precedence over a selection inherited from an ancestor.
A missing selected project is an error. `unset` removes only the selection in
the current directory; the game is preserved. `init` uses its explicit path or
the current directory, not an inherited selection. The pre-initialization `game`
commands use the same rule.

Rebuild the standalone narrative guide after editing its source or adding live
execution artifacts:

```sh
uv pip install -e '.[docs]'
python scripts/build_docs.py
```

## Load the complete worked case

To author the experiment yourself, follow [Build the game through commands](docs/build.html).
`game decision set`, `game criterion add` and `game board set` build a local draft;
`game show` reports what is missing. Once ready, `perla init` registers that draft
without an input file. The guide then constructs all four dossiers with `actor add`
and the dossier commands, and sets review policy through named CLI options.
Initialization fixes the experiment design; further changes require a fresh game.

From an initialized game directory containing a copy of the tutorial case:

```sh
perla actor load --input case/players.json
perla dossier show home
perla status
```

The readable manifest registers the cast and its full sourced dossiers in one
transaction. Repeating the same load is harmless; conflicting edits are preserved
by refusing replacement. This removes the need for shell loops or copying every
statement. Individual authoring commands remain available for inspecting and
changing the assumptions that matter.

The [tutorial](docs/index.html) teaches the strategic argument and one complete
round. [Execution commands](docs/commands.html) cover all three rounds and recovery;
[the reference](docs/reference.html) retains the full players, prompts and transcripts.

## Build a dossier in plain text

Add facts and strategy statements directly; Perla records the edits automatically:

```sh
perla actor add home --name Starbucks --role home
perla dossier fact home h1 'Assumed pilot limit: 50 cafés.' \
  --source 'Scenario design; not an announced company policy'
perla dossier incentive home 'Improve café service without losing mobile purchases (h1).'
perla dossier constraint home 'Keep the pilot within 50 cafés (h1).'
perla dossier capability home 'Test pickup windows within the pilot (h1).'
perla dossier red-line home 'Do not claim that a national rollout is authorized (h1).'
perla dossier show home
```

This small example illustrates the interface; the guide builds all four complete
Starbucks-case dossiers with sourced history, financials, budgets and information
limits. Each fact needs an ID and at least one `--source`; repeat that flag for
multiple sources. `--replace` explicitly revises an existing fact. Identical
facts and statements are not duplicated. Unfinished dossiers remain drafts and
cannot enter play. A complete dossier still needs approval unless review was
explicitly delegated. Edits record the local OS account, timestamp and before/after
contents, without claiming human review. Dossiers freeze at the first move export.
The existing whole-file `dossier edit` interface remains available for imports.

## Pass named Jobs files to ep

Run from your game directory. Export a batch directly to files, choose models
with ep, and import the completed Results without extracting IDs from JSON:

```sh
perla job generate moves --move 1 --output round-1/moves
ep models create --model gpt-4.1 --service openai \
  --temperature 0.5 --max-tokens 16000 --output home-model.ep
ep run --jobs round-1/moves/home.jobs.ep --model_list home-model.ep \
  --background --wait --timeout 900 --remote_inference_results_visibility private \
  --output round-1/moves/home.results.ep
# Run the other exported actor files with their chosen models, then:
perla ingest moves --from round-1/moves
```

`--output DIRECTORY` also works with `adjudicate open`, `adjudicate resolve`,
and the other `job generate` kinds. It creates `ACTOR.jobs.ep` and `batch.json`.
Save each ep result as `ACTOR.results.ep` in that directory. Paths are relative
to the shell's working directory, including when Perla uses a selected project.

`ingest KIND --from DIRECTORY` checks the receipt against registered jobs and
validates all pending Results in one transaction. A missing or invalid result
commits none of that batch. Repeating import skips previously accepted jobs.
Re-exporting the same batch preserves Results; a different batch or altered
package is refused. The existing `--job ID --source FILE` interface remains
available for individual imports, including retries with separately named files.
Use ep's job UUID with `ep jobs wait` or `ep jobs results` to resume a remote
execution after a timeout. No inference runs inside Perla.

## First game

Run from this checkout. The examples use **fictional teaching data**, not
researched claims about real companies.

```sh
perla --project /tmp/perla-example init --input examples/marketplace-outcomes/game.json
perla --project /tmp/perla-example actor add home --name 'Home marketplace' --role home
perla --project /tmp/perla-example actor add rival --name 'Incumbent marketplace' --role competitor
perla --project /tmp/perla-example actor add startup --name 'Venture-backed entrant' --role competitor
perla --project /tmp/perla-example dossier edit home --input examples/marketplace-outcomes/home.json --by facilitator --note 'Imported fictional teaching data'
perla --project /tmp/perla-example dossier edit rival --input examples/marketplace-outcomes/rival.json --by facilitator --note 'Imported fictional teaching data'
perla --project /tmp/perla-example dossier edit startup --input examples/marketplace-outcomes/startup.json --by facilitator --note 'Imported fictional teaching data'
```

Review the dossiers using `dossier show ACTOR`. After the facilitator actually
approves them, record that review:

```sh
perla --project /tmp/perla-example workflow approve dossiers --by facilitator --note 'Reviewed distinct incentives, constraints, and source grounding'
perla --project /tmp/perla-example job generate moves --move 1
perla --project /tmp/perla-example workflow validate
```

Each generated job has a `job_id`, actor id, and absolute `.jobs.ep` package
path in the JSON response. Inspect the packages with `ep inspect PATH`. Execute
each externally with `ep run PATH --model MODEL --output RESULTS.ep`, choosing
different models for the actors. Then ingest each result:

```sh
perla --project /tmp/perla-example ingest moves --job JOB_ID --source RESULTS.ep
```

Ingestion checks the project, actor, round, job id, and embedded context, requires
exactly one structured answer, and stores its model provenance. It rejects
replacement submissions. The next phase is `ready_for_adjudication`.

## Adjudication

After all actors commit, optionally add scoped, attributed human testimony using
`testimony add --input testimony.json --by RECORDER`. The example
[testimony file](examples/marketplace-outcomes/testimony.json) shows the format.
Testimony is immutable, audited, and remains separate from simulation provenance.
Supply it before opening control; the control snapshot freezes its evidence.

```sh
perla --project /tmp/perla-example adjudicate open --move 1
# Inspect the emitted control package and execute it externally, then:
perla --project /tmp/perla-example ingest rulings --job CONTROL_JOB_ID --source CONTROL_RESULTS.ep
perla --project /tmp/perla-example adjudicate show --move 1
perla --project /tmp/perla-example workflow validate
```

Opening fails with `E_UNCOMMITTED_MOVES` until every expected actor has a valid
commitment. It emits one model-free control job containing all moves, action ids,
dossiers, criteria, the initial board, and testimony. Reopening returns the same
package. Choose a control model independently of the actors. Use `--mode human`
at opening for human control, then ingest a participant-attributed JSON submission
with `kind: rulings`, `actor_id: control`, and `answer: {"rulings": [...]}`.

The [ruling example](examples/marketplace-outcomes/rulings.json) contains a complete
answer. The exported control prompt also includes its JSON schema. Each ruling
must name the committed action ids it resolves (`m1.home.a1` refers to the home
actor's first action), all affected actors, a causal chain, a primary basis,
rationale, outcome description, confidence, and proposed `board_delta`. Across
the batch, rulings must address every committed action, including no-effect
outcomes. Unknown references, duplicate ids, missing evidence, and invalid
probabilities reject the entire batch without partial ingestion.

Supported evidence bases are:

- `dossier_fact`: qualified citations such as `d.home.h1` must exist in the snapshot.
- `testimony`: citations such as `t.procurement` must identify registered testimony.
- `reference_class`: at least two distinct named cases, each with sources, a
  mapping to this game, and known disanalogies. Perla checks structure, not the
  truth of supplied historical claims.
- `judgment`: explicit residual judgment, flagged as an escalation candidate.

These checks apply to both the primary basis and each causal link. `model` and
`panel` bases are rejected until deterministic recomputation and panel aggregation
are implemented. Resolution provenance comes from the ingested artifact; an agent
cannot label its own answer as a human panel ruling.

Optional `branches` contain `uncertainty`, at least two `options` (each with `id`,
`description`, `probability`, and `board_delta`), and the `played` option id.
Probabilities must sum to one and the chosen delta must match the ruling outcome.
Criterion links, judgment links, and branches are flagged for later escalation.

Accepted initial rulings are immutable proposals. The workflow becomes
`adjudication_routing`; the following sequence resolves and closes the round.

## Routing, rebuttals, and closure

```sh
perla adjudicate route --move 1
perla job generate rebuttals --move 1
# Execute each actor package externally, then ingest each response:
perla ingest rebuttals --job ACTOR_JOB_ID --source ACTOR_RESULTS.ep
perla adjudicate resolve --move 1
# Execute the new control package externally:
perla ingest resolutions --job RESOLUTION_JOB_ID --source CONTROL_RESULTS.ep
perla adjudicate show --move 1
```

`route` snapshots the escalation policy for the round. Defaults are a human-review
budget of 5, automatic eligibility at confidence 0.7, and a judgment-share warning
threshold of 0.4. Configure future unrouted rounds using
`adjudicate policy set --input policy.json`:

```json
{"human_budget": 5, "auto_confidence": 0.7, "judgment_share_threshold": 0.4}
```

Criterion-linked candidates rank first, then board impact, then ruling id.
Default impact is the number of board fields changed, a coarse proxy. An optional
`route --input routing.json` can supply `{"flagged": ["r001"], "impact_scores":
{"r001": 10.0}}` to flag rulings and specify impact scores. Policy and routing
options freeze when the round is first routed. Routes are recalculated after
control resolutions so revised rulings become escalation candidates.

Selected criterion-linked rulings block closure until a human assesses them.
Other escalation candidates are provisional; over-budget candidates are also
provisional and explicitly marked as over budget. Unassessed candidates become
recorded escalation debt when the round closes. Automatic eligibility skips
escalation, but never skips rebuttals or the final approval checkpoint.

Each actor receives only its own dossier, the public decision and board, and
the initial rulings affecting it. Its single response must accept or challenge
every assigned ruling. A challenge names one mechanism link and cites the actor's
own dossier facts. Example answer for a home-actor rebuttal job:

```json
{"rebuttals": [{"ruling_id": "r001", "stance": "challenge", "link": "L1",
"argument": "The capacity constraint supports a narrower conclusion", "cite": ["d.home.h1"]}]}
```

An acceptance uses `stance: accept`, a reason in `argument`, `link: null`, and
`cite: []`. Duplicate submissions and missing assigned rulings are rejected.

Control receives all rebuttals after all affected actors respond. It must affirm
or revise every ruling, explain the decision, and respond to each affected actor.
Example answer:

```json
{"resolutions": [{"ruling_id": "r001", "decision": "affirm", "reason": "The original mechanism remains supported",
"responses": [{"actor_id": "home", "reason": "The narrower interpretation does not change the outcome"},
{"actor_id": "rival", "reason": "Acceptance recorded"}, {"actor_id": "startup", "reason": "Acceptance recorded"}],
"revision": null}]}
```

A revision supplies the full replacement ruling in `revision`. It must preserve
the ruling id, resolved actions, affected actors, and existing criterion links.
Initial rulings and their provenance stay intact; final rulings, control reasons,
rebuttals, assessments, and workflow events are stored in `.perla/review/move-N.json`.

After actual human review, record any required individual assessments and the
round's final approval:

```sh
perla adjudicate assess --move 1 --ruling r001 --by REVIEWER --note 'Reviewed the revised mechanism against the evidence'
perla workflow approve rulings --move 1 --by FACILITATOR --note 'Approved the final rulings, proposed board, and escalation debt'
perla adjudicate close --move 1
perla job generate moves --move 2
```

An assessment confirms the current final ruling and is labeled
`individual_review`; it does not become panel evidence or overwrite simulation
provenance. Hosted panels and `voting` aggregation remain pending. Direct human
assessment is the currently implemented way to satisfy blocking escalations.

Board deltas replace top-level fields. If rulings propose different values for
one field, approval and closure refuse to choose silently. Inspect
`adjudicate show`, then use `adjudicate reconcile --move 1 --by REVIEWER --input
choices.json` with a ruling and rationale for every conflicting field:

```json
{"choices": {"home_qualified_teams": {"ruling_id": "r002", "reason": "This mechanism determines final capacity"}}}
```

Approval binds to the exact final rulings, routing/assessments, reconciliations,
and proposed board. Later assessment or reconciliation changes invalidate it.
Closure atomically locks the review, versions the board, and advances the game;
the next actor jobs receive only that public board and their own dossier.
Closed rounds cannot be altered. Testimony may be added for subsequent rounds
without changing older control snapshots. Asynchronous reopening and propagation
of later panel outcomes are not yet implemented.

`init --delegate rulings` explicitly waives the round approval checkpoint and is
recorded at closure. It does not waive blocking human assessments. Once all
registered rounds close, the phase is `ready_for_endgame`.

## Endgame and reports

Optionally generate an isolated novelty baseline, then evaluate the completed
game through another EDSL package:

```sh
perla job generate novelty
# Execute externally and ingest its findings:
perla ingest novelty --job BASELINE_JOB_ID --source BASELINE_RESULTS.ep
perla job generate endgame
# Execute externally and ingest its assessment:
perla ingest endgame --job EVALUATION_JOB_ID --source EVALUATION_RESULTS.ep
perla endgame show
perla endgame
perla report context
perla report html
perla export uncertainties
```

Finalization requires every criterion to have a cited verdict, every insight
to have an owner and due date, and every committed move and final ruling to have
a dated prediction with explicit resolution criteria. Human corrections use
`endgame revise`; unsupported criteria remain `unevaluated` and require explicit,
attributed gap acceptance before reports render. Missing insight assignments can
also be waived explicitly. No baseline means novelty stays `unassessed`.

See [the endgame guide](docs/endgame.md) for schemas, revisions, waivers,
provenance, and report contracts. Exports default to `.perla/reports/` and can be
regenerated deterministically from canonical state.

## Lookback

After real outcomes become observable, record sourced evidence and score the
probabilities registered at endgame:

```sh
perla lookback open --by reviewer --note "Scheduled follow-up"
perla lookback show
perla lookback record --input outcomes.json --by reviewer --note "Reviewed dated evidence"
perla lookback score --as-of 2028-01-01
perla report lookback
perla report html
```

Outcome corrections retain history and invalidate the current score. The original
registry stays frozen. Reports include Brier scores, calibration plots by
population and basis, shared-event comparisons, and explicit missing-evidence
counts. Probabilities are never inferred from control confidence. See the
[lookback guide](docs/lookback.md) for the input schema and scoring contract.

## Offline demonstration

From the installed checkout, use a new output directory:

```sh
python examples/marketplace-outcomes/demo.py --project /tmp/perla-control-demo
perla --project /tmp/perla-control-demo adjudicate show --move 1
```

This explicitly fictional three-round demo generates genuine EDSL Jobs and synthetic Results
using the `test` model identifier, without executing inference. It records the
dossier and ruling approval delegations, a zero human-review budget, scripted
testimony, move commitments, rebuttals, control resolutions, and closed boards.
Escalation debt remains recorded. The demo also runs a baseline and evaluation,
explicitly accepts the replication and margin measurement gaps, registers 12
predictions with teaching probabilities, and records scripted future observations
from `reality.json`. It scores ten predictions, preserves two unresolved or
unresolvable outcomes, and writes final JSON/HTML reports with calibration plots.
It ends at `complete` and audits the whole project; no real-world findings are implied.

## Dossier structuring jobs

Supply actual researched facts as `{"facts": [{"id": "f1", "claim": "...",
"sources": ["source URL or documentary reference"]}]}`:

```sh
perla job generate dossiers --actor rival --facts facts.json
# Execute the emitted package externally, then:
perla ingest dossiers --job JOB_ID --source RESULTS.ep
```

The job supplies incentives, constraints, capabilities, and red lines. Ingestion
preserves the original supplied facts and sources. A human can supply or revise
a complete dossier through `dossier edit` with author and rationale; edits have
before/after audit records and invalidate the previous review approval.

## Human submissions

Set `actor set-mode ACTOR human` before move generation. This milestone exports
the structured EDSL survey job but does not yet generate hosted survey links.
Use `ingest ... --human-input` to ingest a local JSON answer:

```json
{
  "project_id": "PROJECT_UUID",
  "job_id": "job-JOB_HEX",
  "actor_id": "home",
  "kind": "moves",
  "move": 1,
  "participant_id": "facilitator-alice",
  "answer": {
    "actions": ["Launch a bounded pilot"],
    "rationale": "Limit exposure while measuring competitor response",
    "resource_commitments": ["Assign two delivery teams for six months"],
    "expected_responses": ["The incumbent discounts comparable contracts"]
  }
}
```

Human provenance remains labeled separately from simulation results. Recording
names and approvals is a facilitator assertion, not identity authentication.

## Contracts and guarantees

- Every invocation emits one versioned JSON envelope; errors exit nonzero and
  include `code`, `message`, and `remediation`. `--human` renders it with Rich.
  Put global flags (`--project`, `--human`) before the command.
- `.perla/` stores canonical JSON. Writes are serialized with a file lock;
  a write-ahead journal recovers interrupted multi-file commits on next access.
- Decision and criteria lock at initialization (the stricter draft-spec rule).
  Input validation requires explicit offer, segment, pricing, timing, scope,
  and observable criterion thresholds; it cannot prove semantic testability.
- Review requires one home and two to five external actors, all with sourced
  dossiers. Identical incentive/constraint sets are rejected. Deeper
  mirror-imaging checks remain the facilitator's responsibility.
- `init --delegate dossiers` explicitly records a review waiver. Without it,
  approval binds to the exact actor/dossier snapshot.
- Actor roster, modes, and dossiers freeze at the first move export. Repeating
  generation returns existing packages. Each move package contains only its
  own dossier, public decision, and previous public board, plus routing metadata.
- `workflow validate` checks actual exported package hashes as well as manifests,
  registration integrity, committed content hashes, lineage, testimony, frozen
  control snapshots, review records, approvals, deterministic board outcomes,
  evaluation snapshots, and finalized insight/prediction records. Same-model
  simulations warn; `--strict` makes warnings errors.
- Local files remain inspectable by the facilitator. Isolation applies to
  generated actor jobs; hashes detect accidental edits, not an adversary able
  to rewrite both records and hashes. Keep private state out of actor access.

## Development

```sh
pytest -q
ruff check .
```

The tests include actual EDSL package round trips without model calls. EDSL
integration tests skip explicitly if the optional dependency is absent.
