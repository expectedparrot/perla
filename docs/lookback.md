# Lookback outcomes and calibration

Lookback records what happened after a finalized game. It preserves the frozen
prediction registry, registered probabilities, endgame assessment, and original
provenance. Observations and score snapshots live in a separate, hash-pinned
`.perla/lookback.json` ledger. No models run during lookback.

## Record observations

```sh
perla lookback open --by reviewer --note "Scheduled follow-up"
perla lookback show
perla lookback record --input outcomes.json --by reviewer --note "Reviewed launch records"
perla lookback score --as-of 2028-01-01
perla report lookback
perla report html
```

`open` requires finalized endgame and records attribution. Repeating it returns
the original opening. `show` lists the immutable registry with its prediction ids,
current observations, revision history, latest score, and whether that score is
stale. The game's phase remains `complete`; status includes separate lookback
counts and next actions.

An `outcomes.json` submission has this shape:

```json
{
  "events": [
    {
      "id": "rival_launch",
      "statement": "The rival launched the specified offer before its deadline",
      "prediction_ids": ["p002"],
      "status": "resolved",
      "outcome": true,
      "as_of": "2028-01-01",
      "occurred_on": "2027-10-15",
      "rationale": "The dated release confirms the offer and segment specified in the forecast",
      "sources": [
        {
          "reference": "archive:company-release-2027-10-15",
          "description": "Archived release identifying the offer, launch date, and eligible buyers"
        }
      ]
    }
  ]
}
```

Use the ids and resolution criteria from your registry. Sources can be URLs or
stable documentary references; each needs a description. Perla records those
references and descriptions without fetching or verifying the source contents.
The operator must assess their quality and applicability.

There are three observation states:

- `resolved`: `outcome` must be the boolean `true` or `false`. An occurrence
  requires `occurred_on`, no later than the observation's `as_of` or any mapped
  prediction's deadline. Non-occurrence requires `as_of` at or after every mapped
  deadline and no `occurred_on`.
- `unresolved`: outcome and occurrence date are null. Evidence is insufficient,
  possibly only temporarily.
- `unresolvable`: outcome and occurrence date are null. Explain why the evidence
  cannot resolve this event. This remains distinct from a false outcome.

All states require sources and rationale. Missing predictions remain
`unrecorded`. Partial batches are allowed; invalid batches write nothing. Dates
are explicit observation cutoffs, so scripted future examples are possible.
Scoring requires an `--as-of` date at least as late as every current observation;
it does not infer reality from the wall clock or silently rewind to old evidence.

## Shared events and corrections

An event may map several predictions only when they concern the **same
proposition and resolution criteria**, with the same boolean interpretation.
Opposite predictions need separate propositions; do not combine them merely
because they concern the same company. Each prediction belongs to at most one
event. The operator asserts equivalence and must explain it in the rationale.
Perla labels the mapping `retrospective_operator_assertion`; it is not a
pre-registered comparison or automatic semantic match.

The first recording freezes an event's statement and prediction mapping.
Submitting its id again can revise the outcome, status, rationale, sources, and
observation cutoff. The cutoff cannot move backwards. `--by` and `--note` are
required for every submission. Previous content and attribution remain in
history; this allows a mistaken occurrence to become non-occurrence or an
unresolved event to become resolved without erasing the earlier assessment.

A correction makes the previous score stale. `lookback score` appends a new
snapshot containing the exact observations it used. Repeating a score with the
same evidence and cutoff returns the existing snapshot. Earlier scores retain
their evidence and computation; `workflow validate` recomputes them and checks
the ledger and frozen registry hashes.

## Scoring contract

For each resolved forecast with a registered probability, the Brier score is
`(probability - outcome)^2`, where true is 1 and false is 0. Group scores average
these values; lower is better. A missing probability stays missing: neither a
committed action nor control confidence is converted into an event probability.
Resolved forecasts without probabilities receive an observed outcome but no
Brier score. Unrecorded, unresolved, and unresolvable entries are excluded from
probabilistic scoring and counted separately.

The output includes all three populations even when empty:

- `team_moves`: committed actor moves.
- `agent_rulings`: rulings whose final resolver was an agent.
- `human_rulings`: rulings whose final resolver was human.

These identify the original resolver. `registration_provenance` separately
identifies who registered the probability, which may be an endgame evaluator
rather than the original actor or resolver. Observation attribution does not
change either provenance. An agent ruling supported by testimony remains an
agent ruling, with `testimony` as its basis.

Summaries include overall, population, basis, and population-within-basis groups.
Moves use the basis label `not_applicable`. Calibration uses ten fixed bins:
`[0, 0.1)`, ..., `[0.9, 1]`. Each reports its count, mean probability, and observed
frequency. Empty bins return null coordinates. JSON provides curve coordinates;
HTML draws population and basis plots, omitting empty bins.

Population summaries can concern different events. `matched_comparisons`
therefore also reports each population pair restricted to shared resolved events
with probabilities in both populations. It lists the exact event ids. Its
`event_weighted_brier_score` first averages forecasts within each event and
population, then weights shared events equally. Ordinary Brier summaries and
calibration bins weight forecasts equally. No overlap means null comparative
scores, not a winning population.

These are descriptive scores for one game, with counts and exclusions exposed.
They provide no causal or statistical superiority claim. Cross-game aggregation,
pre-registered shared-event ids, mechanism-link grading, and automatic tuning of
future escalation policies remain future work; exported results can inform
manual review of the next game's policy.

## Exports and demonstration

`report lookback` writes `.perla/reports/lookback.json` with artifact type
`perla.lookback_score`. It includes the project id, registry hash, score id,
evidence snapshot, forecast-level grades, group summaries, curves, matched
comparisons, and method notes. It requires a current score and rejects stale
scores. `--output PATH` can change the destination; canonical state cannot be
overwritten. Repeated exports from unchanged state are deterministic.

The regular JSON context and HTML report include lookback when opened. HTML
explicitly labels an old score as historical if evidence has changed and includes
observation history. Re-export reports after updating observations or scoring.
The report's original `context_hash` continues to identify the endgame game
context; lookback carries its own registry and evidence hashes.

`examples/marketplace-outcomes/demo.py` registers explicit teaching probabilities,
then reads `reality.json` as fictional future evidence. It resolves ten forecasts,
leaves one unresolved and one unresolvable, and exports the scores and plots.
The example's observations are invented, and no real-world findings or model
inference are involved.
