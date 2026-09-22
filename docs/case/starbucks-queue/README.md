# Starbucks: protecting the café during the morning rush

Research cutoff: September 12, 2026. This independent teaching case asks whether
Starbucks should reserve peak production capacity for café orders and offer later
mobile pickup slots when its mobile allocation fills. The proposed experiment is
not an announced Starbucks policy, and the case contains no private operating
records or actual customer interviews.

The public context is sourced in `sources.json`. The 50 pilot cafés, 50 comparison
cafés, 20% reservation, $3 transaction contribution, competitor capacity and
promotion limits, and every behavioral constraint are explicit assumptions.
Verify actual local overlap with Dutch Bros and Dunkin before selecting stores.
The customer actor is a composite, not a research respondent.

## Setup

The tutorial also provides a complete command-authoring route in `docs/build.html`:
build a draft with `perla game decision set`, `perla game criterion add` and
`perla game board set`, inspect it with `perla game show`, then register it with
`perla init`. That walkthrough builds the same case and all four dossiers without
loading input files. The file-loading alternative follows below.

After installing Perla with its EDSL extra, run these commands from the source
folder. Use a fresh game directory. The walkthrough delegates dossier and ruling
review explicitly for a model-only demonstration:

```sh
mkdir -p games/starbucks-queue
cp -R docs/case/starbucks-queue games/starbucks-queue/case
cd games/starbucks-queue
perla init --input case/game.json --delegate dossiers --delegate rulings
perla actor load --input case/players.json
perla adjudicate policy set --input case/live-policy.json
perla status
```

Expected status: `data.phase` is `move_generation`, with no missing dossiers.
For the standalone case ZIP, put the extracted files in a `case` subdirectory
of a fresh game folder, enter that game folder, and start with `perla init` above.
The case ZIP contains inputs; it does not install Perla.

`players.json` is a readable roster linking each actor to its complete dossier.
The load is atomic and does not overwrite existing edits. You can inspect a
player with `perla dossier show home`, or author individual facts and strategy
statements using `perla dossier fact`, `incentive`, `constraint`, `capability`
and `red-line`.

For a facilitated run, omit both `--delegate` flags and use `policy.json`.
Record actual dossier approval before play, then obtain the required ruling
assessments and approval in each round. The main tutorial and execution reference
explain those checkpoints.

## Measurement rules to settle before play

Use January, February, and March 2027 as calendar months, not Starbucks fiscal
quarters. Select store pairs and baseline periods before the experiment; keep
weekday peak windows and measurement definitions consistent. Real matched-store
comparisons require scrutiny of seasonality, selection, spillovers, and local
promotions; Perla does not validate a causal identification design.

Service: for each complete store pair, compute the percentage reduction as
`100 * (1 - (pilot_post_p90 / pilot_pre_p90) /
(comparison_post_p90 / comparison_pre_p90))`, then weight store pairs equally.
Use positive baseline values and equivalent weekday peak windows. The `service`
criterion triggers below a 20% mean reduction, measured over at least 40 complete
pairs by the end of February. Missing or invalid waits leave it unevaluated.

Economics: compute change in average daily all-channel contribution for the pilot
store minus the corresponding comparison-store change, then average equally
across at least 40 complete pairs. Include direct transaction contribution,
credits, promotional costs and added labor, without double-counting. A mobile
order that becomes an in-café purchase is a channel shift, not a new transaction.
A delayed purchase is not necessarily lost. The `economics` criterion triggers
below $0 per pilot café per day by the end of March. No real costs are supplied.

Retention: define customer cohorts from baseline mobile use, keep assignment
fixed, and count any subsequent Starbucks purchase across channels as a repeat.
Calculate the change in 30-day repeat rate in the pilot cohort minus the change
in the comparison cohort, in percentage points. Require at least 1,000 customers
per arm with complete follow-up by the end of March; enroll cohorts early enough
for that window. The `retention` criterion triggers below -3 percentage points.
Customer overlap and visits to other Starbucks stores need explicit treatment.

Null is unmeasured, not zero. These metrics are proposed tutorial definitions,
not disclosed Starbucks financial measures. Control must explicitly adjudicate
hypothetical estimates in separate `simulated_*` fields and cite its basis. Keep
actual measurement fields missing and observed sample counts at zero without
operating records. Numerical recomputation is not built into Perla's adjudicator. Actual observations belong in lookback.

## Player dossiers and execution

Each player has a complete dossier and a separate `ACTOR-facts.json` research
packet. Starbucks has segment financials, service strategy, assumed pilot unit
economics and competing functional incentives. Dutch Bros has quarterly financials,
its operating history, an assumed 12-shop remit and a $30,000 monthly envelope.
Dunkin has franchise governance, ownership history, a 15-store remit and a $20,000
monthly envelope; private unit margins are explicitly unknown. The customer
composite distinguishes three occasions with different assumed budgets, time
limits and outside options. No segment weights are asserted as measured facts.

`policy.json` is for a facilitated run with a five-review human budget.
`live-policy.json` is for the explicitly delegated, model-only documentation run:
zero human assessments, with unresolved escalation debt preserved. It is not a
record of human approval.

Perla exports named Jobs batches; ep runs them and saves Results; Perla validates
and imports the Results. Follow `docs/index.html` and `docs/commands.html` in the
source checkout or the accompanying tutorial. No custom Python execution runner
is needed. The original dated transcripts and the fresh CLI verification are
identified separately in `docs/reference.html`.
