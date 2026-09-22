# Endgame evaluation and reporting

Endgame starts after every registered round is closed. Perla generates evaluation
jobs; the operator runs them externally or collects attributed human responses.
The CLI validates the structure and references of verdicts. It does not prove
that an evaluator's interpretation of a free-form criterion is correct.

## Optional novelty baseline

`perla job generate novelty` exports a model-free job containing only the decision,
all dossiers, and the initial public board. It excludes moves, rulings, testimony,
criterion verdicts, and private rationales. Although generated after the game,
its information is restricted to the pre-game inputs.

The answer is `{"findings": [{"id": "capacity", "text": "Limited delivery teams
constrain expansion"}]}`. At least one finding is required. Ingest with
`perla ingest novelty --job JOB_ID --source RESULTS.ep`. If a baseline job exists,
its answer must ingest before an endgame job is generated.

The evaluator later maps insights to baseline finding ids. Matching insights are
`derivable`; unmatched insights are `emergent`. This is an evaluator-mediated
heuristic, not proof. If the baseline is skipped, every insight is `unassessed`,
and the report says so. Baseline evidence cannot be added after an endgame job
has frozen its input snapshot.

## Evaluation contract

`perla job generate endgame` exports the full closed-game context and response
JSON schema. Its answer has three arrays: `evaluations`, `insights`, `predictions`.
Every registered criterion must appear exactly once in `evaluations`.

An example evaluation entry:

```json
{
  "criterion_id": "supply",
  "verdict": "survived",
  "rationale": "Seventeen teams remain at the move-two deadline, above the fifteen-team threshold",
  "ruling_refs": ["ruling:2:r001"],
  "board_refs": ["board:2"]
}
```

Valid verdicts are `triggered`, `grazed`, `survived`, and `unevaluated`. All
citations must resolve to final rulings and actual board versions. References
after the criterion's registered deadline are rejected. Non-gap verdicts need
both ruling and board citations; `survived` and `grazed` must cite the deadline
board itself. An unsupported criterion should be `unevaluated`, with a rationale
explaining the missing evidence. The original registration is never altered.

An example insight entry:

```json
{
  "id": "capacity",
  "text": "Concurrent competitor moves draw on a shared delivery workforce",
  "ruling_refs": ["ruling:1:r001", "ruling:2:r001"],
  "owner": "Strategy lead",
  "due_date": "2027-02-28",
  "baseline_matches": ["capacity"]
}
```

Insight ids must be unique and each insight must cite at least one final ruling.
Unknown owners or due dates may be `null` at ingestion; finalization blocks until
they are assigned or explicitly waived. Dates are validated calendar dates in
`YYYY-MM-DD` format. The evaluator should not invent human assignments.

An example prediction entry:

```json
{
  "source": "move:1:rival",
  "due_date": "2027-08-31",
  "resolution_criteria": "Dated pricing records confirm the actor implemented all listed fee reductions by the deadline",
  "probability": null
}
```

Register exactly one prediction for every `move:N:ACTOR` and `ruling:N:ID` key in
the job context. The CLI derives the forecast statement and original forecaster
from the source record. The evaluator supplies its due date, observable resolution
criteria, and optional explicit event probability. Adjudicator confidence is not
automatically reused as an event probability. Missing or duplicate sources block
ingestion. Unknown event probabilities remain `null`.

Both job types support `--mode human`. For `--human-input` ingestion, use the
standard submission wrapper with `actor_id: scribe`, the actual project/job ids,
and the matching kind. `move` is `0` for novelty and the last closed round number
for endgame. Otherwise ingest native EDSL Results; perla never calls a model.

## Review and human corrections

Inspect the ingested answer, provenance, and completion gates:

```sh
perla endgame show
```

To correct verdicts or assign insight owners, save the complete revised answer
(the three arrays, without the envelope) and record a human revision:

```sh
perla endgame revise --input revised-assessment.json --by Alice --note 'Assigned follow-up owners and corrected the supply verdict'
```

Revisions retain previous answers and provenance. The revised assessment is
labeled human-authored, while cited moves and rulings keep their original
simulation/human provenance. Revised assessments must still satisfy all coverage,
reference, and date checks. They can be edited only before finalization.

## Finalization and explicit gaps

```sh
perla endgame
```

Finalization atomically writes the endgame record, `insights.json`, and
`predictions.json`. It refuses incomplete verdicts or unassigned insight follow-up.
If the facilitator explicitly accepts those limitations, record them by id:

```sh
perla endgame --accept-gap margin --waive-insight capacity --by Alice --note 'Margin was not measured; follow-up ownership will be assigned at the strategy review'
```

Repeat flags for multiple gaps or insights. Only actual gaps or missing
assignments may be waived, and a reviewer and rationale are mandatory. Waivers
preserve the `unevaluated` verdict and missing assignment in the report; they do
not manufacture findings. Model answers cannot grant waivers.

Finalization is immutable; repeating plain `perla endgame` returns its existing
record. The phase becomes `complete`. Report export and `workflow validate`
verify evaluation snapshots, revision records, insight compilation, and forecast
registration against the frozen game evidence.

## Reports and handoffs

```sh
perla report context --audience 'Executive team' --emphasis 'Competitive collisions and follow-up decisions'
perla report html
perla export uncertainties
```

Default artifacts are `.perla/reports/context.json`, `report.html`, and
`uncertainties.json`. Each command accepts `--output PATH`. Exports cannot
overwrite canonical `.perla` files. Repeated exports from unchanged state and
the same writing brief are deterministic. HTML is standalone and escapes all
user-supplied text; it loads no external scripts or assets.

The context bundle includes the decision, criterion verdicts, original/final
rulings, board history, dossiers, testimony, round reviews, insights, predictions,
uncertainties, provenance index, waivers, and a writing brief. It is suitable as
input to a writing agent; perla does not call or publish to one.

The uncertainty artifact uses the `perla.uncertainties` schema. Declared branches
become competing hypotheses with probabilities, the played branch, causal
evidence, and provenance. Accepted criterion gaps are exported separately.

Health reporting includes basis distribution, same-model actors, actor/control
model overlap, escalation debt, accepted gaps, ownership waivers, and absent
emergent insights. If no criterion triggered or grazed, a home-comfort warning
asks the facilitator to inspect how hard the strategy was challenged. These are
method diagnostics, not proof that a game was or was not useful.

Forecast records preserve both their source provenance and registration
provenance. Populations (`team_moves`, `agent_rulings`, `human_rulings`) describe
the actual resolver; testimony supporting an agent ruling does not silently turn
it into a human-resolved forecast. [Lookback](lookback.md) records sourced actual
outcomes in a separate ledger and scores registered probabilities while preserving
the endgame registry and provenance.
