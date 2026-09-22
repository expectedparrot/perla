# Implementation milestones

The draft specification describes the full target. Only checked items below
are implemented; unsupported commands are rejected by the CLI.

- [x] Python package, CLI entry point, versioned JSON envelopes, agent-start.
- [x] Local JSON store with serialized writes and interrupted-commit recovery.
- [x] Structured decision, 2–5 pre-registered criteria, public initial board.
- [x] Actors, sourced dossiers, editing provenance, content-bound review approval.
- [x] Optional EDSL dossier structuring and isolated actor move exports.
- [x] EDSL Results and participant-attributed JSON ingestion with lineage checks.
- [x] Immutable move commitment, actual package audits, same-model warnings.
- [x] Fictional setup example and tests of method invariants.
- [ ] Versioned decision amendments with rationale.
- [x] Adjudication open: reveal only after every expected move is committed.
- [x] Typed mechanism chains, four supported bases, citation validation, and immutable testimony.
- [x] Control Results/human ingestion, declared branches, pending rulings, and integrity audits.
- [x] Offline scripted three-round example through finalized JSON/HTML reports.
- [ ] Inspectable quantitative formulas with deterministic recomputation.
- [x] Escalation policy, budgets, blocking/provisional rulings, individual assessments, and revision history.
- [ ] Asynchronous panel outcomes, reopening, and downstream board recomputation.
- [ ] Hosted human survey links, panel provenance, voting ballot handoff.
- [x] One rebuttal per affected actor/ruling, control resolution, ruling approval.
- [x] Atomic round closure, explicit board conflict reconciliation, and subsequent rounds.
- [x] Endgame criterion evaluation, owned insights, novelty jobs, prediction registry.
- [x] Human assessment revisions and explicit gap/ownership waivers with provenance.
- [x] Canonical report context, deterministic HTML, uncertainty export.
- [x] Sourced lookback outcomes, attributed revisions, frozen forecast probabilities, and audited score snapshots.
- [x] Brier scores, calibration plots by population/basis, matched-event comparisons, and report exports.
- [ ] Cross-game calibration, pre-registered shared-event ids, mechanism-link grading, and policy tuning.
- [ ] Full five-actor, three-move worked example with mixed adjudication.

Current choices resolve draft ambiguities conservatively: criteria are immutable
from initialization; the board is free-form JSON; the minimum roster is one home
plus two external actors; human local submissions are available before hosted
humanization. These decisions can be extended with explicit state migrations.

Control supports `judgment`, `dossier_fact`, `testimony`, and `reference_class`.
`model` and `panel` are recognized but rejected until their computation and
aggregation paths exist. Blocking escalation is currently satisfied by an
attributed individual human assessment, never labeled as a panel outcome.
Provisional and over-budget escalation debt is recorded at closure. Budget
ranking uses criterion relevance, then explicit impact scores (falling back
to the count of changed board fields), then ruling id. Direct human control
uses `resolution_mode: human` to distinguish it from a human panel. Testimony
is immutable and must be added before its control snapshot opens; new evidence
can be added for later rounds. Existing-testimony revisions remain pending.
