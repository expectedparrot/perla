# perla — competitive wargaming CLI

**Specification v0.1 (draft)**

perla runs a structured business wargame for a strategy under test: define the
decision and pre-register kill criteria, build grounded competitor dossiers,
elicit committed moves from isolated actor teams, adjudicate collisions through
mechanism-based rulings with declared bases, run structured rebuttals, evaluate
kill criteria at endgame, and register predictions for later calibration.
Teams and the control function may be played by AI agents (via EDSL jobs),
by humans (via humanized surveys), or any mixture per role and per ruling.

perla is named for Peter Perla (*The Art of Wargaming*), whose treatment of
umpiring is the methodological backbone of the adjudication subsystem. The
business-wargaming adaptations follow Benjamin Gilad (*Business War Games*)
and Mark Chussil (quantitative adjudication).

---

## 1. Positioning and method claim

A wargame tests a **specific strategic decision** against the strongest
responses competitors could mount, over 1–3 committed moves, with a neutral
control function adjudicating what plausibly happens in the market after each
round of moves. The value of the method comes from **adjudicated collisions** —
interaction effects between competitor responses that single-team planning
reliably misses — and from forcing market assumptions into the open where they
can be argued, recorded, and later graded against reality.

The known failure modes of human wargames (Gilad, Zenko) are design targets
for this tool:

| Failure mode | perla countermeasure |
|---|---|
| Home team wins comfortably | endgame health checks; warning if no pre-registered assumption died |
| Mirror-imaging competitors | mandatory dossiers with incentives/constraints; dossier approval gate |
| Retconned moves | moves hashed and committed before reveal (§6.3) |
| Adjudication as unaccountable judgment | every ruling declares a basis (§7.2); basis distribution is measured; judgment-basis rulings are escalation candidates |
| Consensus theater | kill criteria pre-registered and locked at init; endgame refuses to render until each is formally evaluated |
| No follow-through | insight log with owners/dates; prediction registry; `lookback` scoring |

The agent-native failure mode — **self-collusion**, where one model plays all
roles and quietly converges on plausible consensus — is addressed structurally:
information isolation between actor jobs, per-actor model assignment with a
same-model warning, structured rebuttals, and human adjudication escalation.

## 2. Ecosystem conventions adopted

perla follows the Expected Parrot standalone-CLI conventions:

- **Agent-first contract.** `perla agent-start` returns the method summary,
  current phase, approval checkpoints, and `next_actions`. The calling agent
  treats the JSON envelope as the source of truth.
- **JSON envelope.** Every command emits one versioned envelope by default
  (§9). `--human` renders Rich output for interactive use.
- **Local-first state.** `.perla/` in the project directory is the source of
  truth (§10). All state is plain JSON on disk: inspectable, diffable,
  version-controllable.
- **Explicit EDSL execution boundary.** perla never calls models. It generates
  portable, model-free EDSL Jobs packages (`.ep`); the operator runs them with
  `ep run` (choosing models) or humanizes them (generating survey links); the
  results are ingested back with provenance recorded.
- **Approval checkpoints.** The workflow marks phases that require human
  approval before proceeding (§6). `next_actions` distinguishes executable
  commands from facilitator instructions and marks networked, mutating, or
  approval-sensitive actions.
- **Derived artifacts.** Jobs/Results packages, rendered reports, and export
  bundles are derived; the state store is canonical.
- **Ecosystem handoffs, not reinvention.** Panel aggregation is delegated to
  `voting`; report writing consumes a canonical context bundle in the style
  consumed by `gutenberg`; strategic-futures framing comes from `kahn`;
  residual uncertainties export to `langley`; post-game internal stress-testing
  hands off to `premortem` (§12).

## 3. When to use this

- The user has a concrete strategy, launch, pricing change, or market entry
  and wants to know how competitors will respond before committing.
- The competitive landscape has 2–6 identifiable actors whose incentives and
  constraints can be researched.
- Interaction effects matter: the user suspects the strategy's risk comes from
  *combinations* of responses (price war + talent poaching + platform
  commoditization), not any single competitor.
- The user wants a durable, auditable record: committed moves, reasoned
  rulings, evaluated kill criteria, and a prediction registry that can be
  graded in 6–12 months.

## 4. When this is a stretch (and how to adapt)

- **The strategy is not yet chosen.** Use `kahn` to map strategic futures and
  select a candidate strategy first; wargame the leading option. Optionally
  use `mcda` to choose among candidates.
- **The risk is internal execution, not competition.** Use `premortem`. A good
  sequence is perla → premortem: wargame the strategy against the market, then
  pre-mortem the surviving version against internal failure.
- **There is effectively one competitor and one move.** A wargame degenerates
  to competitor analysis; a structured memo or `langley` ACH on the
  competitor's likely response is cheaper.
- **The key questions are demand-side adoption rates.** Consider `green`
  (conjoint / discrete choice) to measure willingness-to-pay directly, or
  `umriss`/`zwill` to build and validate synthetic buyer populations; feed the
  results into dossiers or adjudication evidence rather than asking the
  wargame to conjure adoption numbers.
- **The user only wants a red-team critique of a document.** Run a lightweight
  single-move game (one actor: "strongest critic"), or skip perla and have the
  calling agent critique directly.

## 5. Decision rule for the calling agent

Before dispatching to perla, confirm:

1. There is a specific, falsifiable strategic decision to test (not "our
   strategy" generally).
2. At least two external actors with distinct incentives could respond.
3. The user cares about competitive interaction over a 1–3 year horizon.
4. The user will engage with approval checkpoints (dossiers, rulings) or has
   delegated them explicitly.

If yes to 1–3, perla is the right method. If 1 fails, elicit a sharper
decision statement first (§13.1) — do not start a game on a vague decision.

---

## 6. Domain model and method guarantees

### 6.1 Entities

| Entity | Description |
|---|---|
| `decision` | The strategy under test: statement, horizon, move count, launch scope. Locked after init (amendable only via `decision amend`, which records a versioned amendment with rationale). |
| `kill_criterion` | Pre-registered falsifiable condition that would invalidate the strategy or a load-bearing assumption. Locked at init; each must be formally evaluated at endgame with a verdict: `triggered`, `grazed`, or `survived`, with citations to rulings. |
| `actor` | A player: `home`, `competitor`, or `wildcard` (an actor whose strategic question is whether this market matters to them at all). Each actor has a dossier and a per-move job lineage. |
| `dossier` | The actor's grounding: financials, cash position, stated strategy, leadership incentives, investor promises, constraints, red lines. Sources recorded per fact. |
| `board` | The adjudicated market state, versioned per move: segment definitions, share estimates, price levels, talent/supply state, buyer sentiment. Move 0 board is the initial market model. |
| `move` | One actor's committed play in one round: actions, rationale, resource commitments. Content-hashed at commitment; hash recorded before any reveal. |
| `ruling` | One adjudication node: the moves it resolves, the mechanism chain, the outcome applied to the board, a declared basis (§7.2), confidence, optional branch structure, rebuttal record, and resolution mode (`agent`, `human_panel`, `testimony`, `model`). |
| `testimony` | First-class human ground-truth evidence injected into adjudication context (e.g., "enterprise infosec review takes 9–12 months"), with attribution and scope. |
| `panel` | Roster of human adjudicators: id, expertise tags, contact/link provenance, response history. |
| `insight` | Scribe-log entry: text, the collision or ruling that produced it, novelty flag (§11.4), owner, due date. |
| `prediction` | Registry entry for lookback: who predicted what, by when, resolution criteria, and (later) the graded outcome. |

### 6.2 Method invariants (enforced by the state machine)

1. **Pre-registration.** Kill criteria cannot be added, edited, or deleted
   after the first move-generation job is emitted. `endgame` refuses to run
   until every criterion has a verdict.
2. **Isolation.** A move-generation job for actor A contains only: A's
   dossier, the public board as of the last closed adjudication, and the
   public decision statement. It never contains other actors' dossiers,
   pending moves, or prior private rationales. Job manifests record exactly
   what was included; `workflow validate` checks manifests for leakage.
3. **Commitment before reveal.** Ingesting a move records its content hash and
   timestamp. Adjudication jobs cannot be generated until all expected moves
   for the round are committed; no actor job for round N+1 can be generated
   until round N's adjudication is closed.
4. **Declared basis.** Every ruling must declare exactly one primary basis
   (§7.2). Rulings with basis `judgment` are flagged and counted; the
   escalation policy (§7.4) consumes this flag.
5. **One rebuttal round.** Each affected actor gets one structured rebuttal
   per ruling; control must affirm or revise with reasons; then the ruling
   locks. No relitigating closed rounds.
6. **Provenance.** Every ingested artifact records: source package, model(s)
   used or `human` with panel/testimony ids, timestamp, and the job manifest
   it answered. Simulation-derived findings are never silently merged with
   human-derived findings (convention shared with `messick`).

---

## 7. Workflow

### 7.1 Canonical sequence

Phases marked ⏸ are approval checkpoints: the calling agent must present the
artifacts to the user and record approval before the state machine allows the
next phase. `--delegate` at init can waive named checkpoints for unattended
runs; waivers are recorded in state and surfaced in the final report.

1. `perla init` — decision statement, horizon (moves × period), initial board
   sketch, kill criteria. Validation rejects untestable decisions and
   unfalsifiable criteria (§13).
2. `perla actor add` × N — declare home, competitors, wildcards.
3. `perla job generate dossiers` — one research-structuring job per actor.
   The calling agent supplies researched facts (filings, pricing pages,
   funding data); the job structures them into incentives, constraints,
   capabilities, and red lines. Ingest with `perla ingest dossiers`.
4. ⏸ **Dossier review.** Mirror-imaging check: does each actor have distinct
   incentives and constraints? Are venture-backed actors modeled with venture
   economics? Human edits via `perla dossier edit`.
5. `perla adjudicate model build` (optional but recommended) — construct the
   quantitative sub-models used for `model`-basis rulings: margin math, cash
   runway, price-competition arithmetic, capacity constraints. Stored as
   inspectable formulas with named parameters, not code blobs.
6. Per move M = 1..N:
   a. `perla job generate moves --move M` — one isolated job per actor (§6.2
      inv. 2). Run each with `ep run` (recommended: different models per
      actor; §11.2) or humanize for human-played teams.
   b. `perla ingest moves --move M` — commit (hash + timestamp) each move.
   c. `perla adjudicate open --move M` — generate the control job: all
      committed moves + board + all dossiers + testimony + sub-models,
      prompted for mechanism-chain rulings with declared bases (§7.2–7.3).
   d. `perla ingest rulings --move M`.
   e. `perla adjudicate route --move M` — apply the escalation policy (§7.4):
      auto-close eligible rulings; queue escalated rulings for humanization
      or testimony.
   f. For escalated rulings: `perla adjudicate humanize --ruling <id>` →
      distribute links → `perla ingest panel --ruling <id>` → aggregate
      (§7.5).
   g. `perla job generate rebuttals --move M` — each affected actor receives
      the rulings against it (only those) and may attack one specific link
      per ruling with dossier evidence. Ingest; control affirms or revises.
   h. ⏸ **Ruling review.** Present the round's rulings with basis
      distribution, escalations, revisions, and any declared branch points.
   i. `perla adjudicate close --move M` — lock rulings, apply outcomes to the
      board, version the board.
7. `perla endgame` — evaluate each kill criterion against the ruling record;
   compile insights; populate the prediction registry; run health checks
   (home-team-comfort warning, basis-distribution report, same-model
   warning).
8. `perla report context` — canonical JSON bundle (evidence, derivations,
   provenance, writing brief) for a downstream writing agent / `gutenberg`.
9. `perla export uncertainties` — branch points and rulings control declined
   to make, formatted as competing hypotheses for `langley`.
10. Months later: `perla lookback open` → record real-world outcomes →
    `perla lookback score` — grades predictions, agent rulings, and human
    rulings on the same events (§8.3).

### 7.2 Ruling bases

Every ruling declares exactly one primary basis (secondary bases may be
listed as support):

| Basis | Meaning | Requirement |
|---|---|---|
| `model` | Computed from a registered quantitative sub-model | Must cite the model id and parameter values; recomputation must be deterministic |
| `reference_class` | Analogy to historical cases | Must cite ≥2 named cases and state the mapping and known disanalogies |
| `dossier_fact` | Follows from a documented actor constraint | Must cite dossier fact ids |
| `testimony` | Grounded in injected human ground truth | Must cite testimony ids |
| `panel` | Resolved by human panel aggregation | Must cite the panel round and aggregation rule |
| `judgment` | Residual structured judgment | Permitted, counted, and escalation-eligible; a game whose closed rulings exceed the configured judgment-share threshold (default 40%) fails `workflow validate` with a warning, and endgame reports it prominently |

### 7.3 Mechanism chains

Rulings are causal chains, not verdicts. Each ruling decomposes into links:

```json
{
  "ruling_id": "r014",
  "resolves": ["m2.toptal.poach", "m2.home.revshare"],
  "chain": [
    {"link": "L1", "claim": "Toptal can fund exclusivity bonuses for ~200 freelancers for 12 months", "basis": "model", "cite": ["qm.cash_runway"]},
    {"link": "L2", "claim": "Bonuses at 25% rate uplift historically move 10-20% of recipients", "basis": "reference_class", "cite": ["rc.marketplace_defection"]},
    {"link": "L3", "claim": "Defection concentrates among elites with least repeat-client revenue", "basis": "judgment"},
    {"link": "L4", "claim": "Home revenue-share program caps defection near the low end of L2's range", "basis": "dossier_fact", "cite": ["d.home.f031"]}
  ],
  "outcome": {"board_delta": "...", "confidence": 0.7},
  "branches": null,
  "resolution_mode": "agent",
  "rebuttals": []
}
```

Rebuttals attack a named link, not the outcome. Humanization decomposes by
link (§7.5). Lookback grades by link where reality permits.

Control may decline to rule: a `branch_declared` ruling records the
unresolvable uncertainty, assigns branch probabilities, states which branch
the game will play, and exports the question to the uncertainty list (§12,
`langley`). Declining honestly is preferred to manufacturing consensus.

### 7.4 Escalation policy (tiered adjudication)

`perla adjudicate policy set` configures routing; defaults:

- **Auto-close:** basis `model` or `dossier_fact` with confidence ≥ 0.7 and
  no kill-criterion linkage.
- **Escalate to human panel:** basis `judgment`; any ruling a kill criterion
  cites; any ruling revised after rebuttal; any ruling with declared
  branches; anything the user flags.
- **Budget:** at most K humanized rulings per move (default 5), ranked by
  kill-criterion relevance, then by board impact. Over-budget escalation
  candidates are closed provisionally and listed in the endgame report as
  unescalated judgment rulings.
- **Async semantics:** per ruling, `--block` (round cannot close until panel
  responses ingest) or `--provisional` (round closes on the agent ruling;
  panel results, when ingested, either confirm or reopen the ruling and
  recompute downstream board deltas, with the revision recorded). Default:
  `--block` for kill-criterion-linked rulings, `--provisional` otherwise.

### 7.5 Humanized adjudication

`perla adjudicate humanize --ruling <id>` compiles the ruling's mechanism
chain into an EDSL survey job: one item per contested link, typed to the
link's claim (probability elicitation for quantitative links; scaled
agreement + free-text mechanism critique for causal links; forced ranking of
"weakest link"). The job is humanized through the standard EDSL flow to
produce per-panelist links (per-person links and distribution mechanics
follow the `treffen` pattern). Responses ingest with panelist provenance.

**Aggregation is delegated to `voting`.** perla exports the panel's responses
as a ballot package in voting's JSON format and records which counting rule
resolved each link (defaults: median for probability links; approval voting
for branch selection; Condorcet fallback for contested orderings). Panel
splits beyond a configured disagreement threshold do not force a resolution:
the link converts to a declared branch and joins the uncertainty export.

**Testimony** is the lightweight alternative to a panel: a domain insider's
ground-truth constraint, entered once, cited by any ruling. Testimony is
scoped ("applies to enterprise procurement timelines, 2026–2028"), attributed,
and overridable only by later testimony or lookback evidence.

### 7.6 Human-played teams

Any actor may be human-played: `perla actor set-mode <actor> human` causes
`job generate moves` to emit a humanized move-elicitation survey (structured:
intended actions, resource commitments, rationale, expected competitor
responses) instead of an agent job. Isolation and commitment invariants apply
identically. Mixed games — e.g., human home team, agent competitors, tiered
adjudication — are the expected common case for high-stakes use.

---

## 8. Endgame, reporting, and calibration

### 8.1 Endgame

`perla endgame` performs, in order:

1. **Kill-criterion evaluation.** Each criterion receives a verdict —
   `triggered`, `grazed`, `survived` — with citations to the specific rulings
   and board states that ground it. A criterion that cannot be evaluated from
   the record is marked `unevaluated` and blocks the final report until the
   user explicitly accepts the gap (recorded).
2. **Health checks.**
   - *Home-comfort warning:* if no kill criterion was triggered or grazed and
     no pre-game assumption died, warn that the game may have lacked teeth.
   - *Basis distribution:* share of closed rulings by basis; judgment-share
     threshold check.
   - *Collusion checks:* same-model warning if all actor jobs ran on one
     model; isolation-manifest audit.
   - *Escalation debt:* judgment rulings that qualified for escalation but
     exceeded budget.
3. **Insight compilation.** Insights are auto-linked to the collisions that
   produced them and flagged for novelty (§11.4). Each surviving insight
   requires an owner and due date before the report renders (waivable,
   recorded).
4. **Prediction registry.** Every actor's committed moves and every closed
   ruling become dated, resolvable predictions with explicit resolution
   criteria.

### 8.2 Reporting

`perla report context` exports the canonical JSON bundle: decision, criteria
verdicts, move-by-move narrative skeleton, rulings with chains and bases,
board evolution, insights, uncertainty list, provenance index, and a writing
brief (audience, emphasis, caveats). This bundle is the input for a
report-writing agent or `gutenberg`. `perla report html` renders an optional
standalone deterministic report (board evolution timeline, ruling cards with
basis badges, criterion verdicts).

### 8.3 Lookback

`perla lookback open` (typically 6–12 months later) reopens the registry.
The operator records real-world outcomes with sources. `perla lookback score`
then grades three populations on the same resolved events:

- team move-predictions (did Fiverr actually fast-follow?),
- agent-resolved rulings,
- human-resolved rulings (panel and testimony),

producing calibration curves per population and per basis type. This is the
tool's long-run research payoff: measured evidence about where human
adjudication beats agent adjudication and vice versa, accumulated across
games. Lookback results feed the next game's escalation-policy defaults.

---

## 9. Output contract

Commands emit one JSON envelope by default:

```json
{
  "schema_version": "1.0",
  "ok": true,
  "command": ["adjudicate", "route"],
  "data": {},
  "warnings": [],
  "next_actions": []
}
```

Failures set `ok: false`, replace `data` with a structured `error`
(`code`, `message`, `remediation`), and exit nonzero. `--human` renders Rich
output. `next_actions` entries are typed: `run` (executable command),
`facilitate` (instruction to the calling agent, e.g. "present rulings r012,
r014 to the user for approval"), and carry flags for `mutating`, `networked`,
and `requires_approval`.

Representative error codes:

| Code | Condition |
|---|---|
| `E_NO_PROJECT` | No `.perla/` in scope |
| `E_VAGUE_DECISION` | Decision statement fails testability validation |
| `E_CRITERIA_LOCKED` | Attempt to modify kill criteria after first move job |
| `E_ISOLATION` | Move-job manifest would include another actor's private material |
| `E_UNCOMMITTED_MOVES` | Adjudication opened before all round moves are committed |
| `E_OPEN_RULINGS` | Round close attempted with blocking rulings unresolved |
| `E_UNEVALUATED_CRITERIA` | Endgame/report with unevaluated kill criteria |
| `E_JUDGMENT_SHARE` | Closed judgment-basis share exceeds threshold (warning-level by default; error with `--strict`) |
| `E_PANEL_PENDING` | Ingest/aggregation attempted before humanized responses exist |

## 10. State contract

```
.perla/
  project.json            decision, horizon, config, checkpoint waivers
  criteria.json           kill criteria (locked), verdicts
  actors/<id>.json        actor metadata, mode (agent|human), model history
  dossiers/<id>.json      facts with per-fact sources
  board/move-<n>.json     versioned adjudicated market state
  moves/move-<n>/<actor>.json      committed moves with content hashes
  rulings/move-<n>/<id>.json       chains, bases, rebuttals, resolution
  testimony/<id>.json     scoped ground-truth evidence
  panel/roster.json       adjudicator roster and response history
  panel/rounds/<id>/      humanized job manifests, responses, ballots
  models/<id>.json        quantitative sub-models (formulas + parameters)
  insights.json           scribe log with owners and novelty flags
  predictions.json        registry with resolution criteria and grades
  jobs/                   generated .ep packages and manifests (derived)
  ingest/                 ingested results provenance (derived)
  reports/                context bundles and renderings (derived)
```

The CLI-managed state is the source of truth; `.ep` packages and reports are
derived artifacts. All files are plain JSON intended for git.

## 11. EDSL integration

### 11.1 Job types

| Job | Cardinality | Contents |
|---|---|---|
| `dossiers` | per actor | structure researched facts into incentives/constraints/red lines |
| `moves` | per actor per round | isolated: own dossier + public board + decision |
| `adjudication` | per round | all moves + board + dossiers + testimony + sub-models; prompts for mechanism chains with declared bases |
| `rebuttals` | per affected actor per round | the rulings against that actor only |
| `panel` | per escalated ruling | link-decomposed elicitation for humanization |
| `board_init` | once | structure the initial market model from research |

All jobs are model-free `.ep` packages with manifests recording exactly what
state they embed (the isolation audit reads these manifests).

### 11.2 Model assignment

`ep run` chooses models at execution time. perla records which model produced
each ingested artifact. Recommended practice, encoded in the tutorial and
checked at endgame: **different models for different actors** (priors differ
across model families, which is a cheap hedge against self-collusion), and a
**different model for control** than for any actor. `workflow validate` warns
when all actors share a model; endgame reports the model map.

### 11.3 Humanize path

Any job may be humanized instead of run: move elicitation for human-played
teams (§7.6), panel jobs for escalated rulings (§7.5), and optionally dossier
review. perla treats `human` as a model value in provenance and never mixes
human-derived and simulation-derived findings without labeling (per the
`messick` convention).

### 11.4 Novelty flagging for insights

To resist the plausible-consensus failure mode, the insight compiler runs a
derivability check: an EDSL job is generated containing only the *inputs*
(decision + dossiers + initial board, no moves or rulings) and asked to
enumerate expected findings; compiled insights that match this enumeration
are flagged `derivable`, the rest `emergent`. A game with zero emergent
insights is reported as such — an honest signal that the run added structure
but not surprise. (Heuristic, not proof; the flag is informational.)

---

## 12. Cross-references

- **Upstream:** `kahn` maps environmental forces and critical uncertainties
  into strategic futures; the decision perla tests is typically a strategy
  selected against a kahn scenario set. `mcda` can select among candidate
  strategies. `green` / `umriss` / `zwill` can ground demand-side dossier
  facts and adjudication evidence (measured preferences, calibrated synthetic
  populations) instead of leaving adoption questions to judgment rulings.
- **Delegated components:** `voting` aggregates panel ballots (perla emits
  voting-format ballot packages and records the counting rule per link).
  Humanized link distribution follows the `treffen` per-person link pattern.
- **Downstream:** `premortem` stress-tests the surviving strategy against
  internal failure (perla answers "what will the market do to this?";
  premortem answers "how might we fumble it?"). `langley` receives the
  uncertainty export (declared branches, declined rulings, high-disagreement
  panel links) as competing hypotheses with an evidence matrix seeded from
  the game record. `gutenberg` (or any writing agent) consumes the report
  context bundle. `labeling` can be used to audit ruling quality at scale if
  a research program runs many games.
- **Validation:** `messick`'s separation of simulation findings from human
  evidence is adopted as a provenance rule throughout.

## 13. Inputs and elicitation

### 13.1 Decision statement

What it is: the specific strategy under test, phrased so competitors can
attack it and criteria can falsify it.

How the agent elicits this:

- Ask what the organization is about to do, at what price/scope, by when.
- Reject strategy-shaped mission statements ("win in AI") — require product,
  segment, pricing posture, and timing.
- Confirm the horizon: how many moves, covering what calendar span.

Default: "We will launch <offer> for <segment> at <pricing posture> in
<period>; does it survive <N> moves of competitive response over <span>?"

Fallback: if the user has options rather than a decision, route to `kahn` or
`mcda` first.

### 13.2 Kill criteria

What it is: 2–5 pre-registered conditions that, if adjudicated true, kill or
force revision of the strategy.

How the agent elicits this:

- Ask: "what, if true, would make you abandon this?"
- Require observability within the game: a criterion must be evaluable from
  rulings/board states ("two or more attackers profitably replicate the
  offering within 12 game-months"), not from unmeasurable sentiment.
- Push for at least one criterion the home team *expects* to be tested hard.

Fallback: derive candidate criteria from the strategy's stated assumptions
and have the user rank which are load-bearing.

### 13.3 Actors and dossiers

- 2–6 actors: the obvious competitors, one asymmetric/venture-logic actor,
  and one wildcard whose indifference could reshape the market (platform
  giants, regulators). The wildcard prompt asks "is this market strategic to
  you at all?" before asking for an attack.
- Dossier facts require sources. The calling agent performs the research
  (filings, pricing pages, funding databases); perla structures and stores.
- Distinctness check at the approval gate: no two actors with the same
  incentive structure.

### 13.4 Panel and testimony

- Panel members are recruited for *decision-process knowledge* (buyers,
  operators, ex-employees), not seniority. Expertise tags route link types
  to the right panelists.
- Testimony is elicited whenever a human in the loop reacts to a ruling with
  "that's not how it works" — the facilitator agent should offer
  `perla testimony add` on the spot.

## 14. Worked example (maintained)

`examples/marketplace-outcomes/` — a fictionalized game testing "A freelance marketplace launches a
fixed-price managed-outcomes product at premium pricing": five actors (home,
two marketplace competitors, a venture-backed agentic-delivery startup, a
frontier-lab wildcard), three moves over 24 game-months, three kill criteria
including a replication criterion, a mixed adjudication run (agent control,
two humanized rulings, one testimony), a declared branch exported to
`langley`, and a lookback scored against a scripted "reality" file so the
tutorial can demonstrate calibration output.

## 15. Common pitfalls

- A vague decision statement produces a game about nothing; validation
  rejects it, but a *narrow-but-wrong* decision passes — confirm scope with
  the user before move 1.
- Dossiers written from the home team's worldview reproduce mirror-imaging
  with extra steps; the approval gate exists to catch identical incentive
  structures.
- Letting the judgment-basis share drift up quietly converts the game to
  narrated vibes; watch the basis distribution per round, not just at
  endgame.
- Escalating everything to the panel recreates the cost the tool exists to
  avoid; the budget is a feature.
- Treating `derivable`-flagged insights as findings inflates the report;
  emergent insights and triggered/grazed criteria are the payload.
- Skipping lookback discards the tool's compounding value; schedule it at
  endgame while attention exists.

## 16. Non-goals

- perla does not call models, choose models, or manage API keys (EDSL/`ep`
  boundary).
- perla is not a market simulator: quantitative sub-models are small,
  inspectable arithmetic registered by the user, not a demand-forecasting
  engine.
- perla does not aggregate human judgments itself (`voting` does) and does
  not write reports (writing agents / `gutenberg` do).
- perla does not attempt game-theoretic equilibrium solving; it structures
  and audits a facilitated exercise.

## 17. Open questions for v0.2

- Should branch-declared rulings support *playing both branches* (forked
  board state) for one move, at 2× cost, rather than committing to the
  higher-probability branch?
- Should the isolation invariant have a configurable "intelligence leak"
  option (actors receive noisy summaries of others' prior moves, simulating
  real competitive intelligence) — realism vs. audit simplicity?
- Panel identity: pseudonymous panelist ids with expertise tags are stored
  locally; is that sufficient for multi-game calibration across projects, or
  is a portable panelist profile needed (privacy tradeoff)?
- Minimum viable board schema: free-form JSON per game vs. a typed segment/
  share/price schema that sub-models and lookback can rely on.
