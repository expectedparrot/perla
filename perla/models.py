"""Validated inputs. Structured observability supplements facilitator judgment."""

from datetime import date
from typing import Annotated, Any, Literal

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)

Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
Identifier = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_-]{0,63}$")]
Texts = Annotated[list[Text], Field(min_length=1)]


class Record(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Decision(Record):
    statement: Annotated[Text, Field(min_length=30)]
    offer: Text
    segment: Text
    pricing: Text
    launch_period: Text
    scope: Text
    moves: Annotated[int, Field(ge=1, le=3)]
    months_per_move: Annotated[int, Field(ge=1, le=36)]


class Criterion(Record):
    id: Identifier
    condition: Annotated[Text, Field(min_length=15)]
    observable: Text
    threshold: Text
    deadline_move: Annotated[int, Field(ge=1, le=3)]


class InitInput(Record):
    decision: Decision
    criteria: Annotated[list[Criterion], Field(min_length=2, max_length=5)]
    board: dict[str, Any]

    @model_validator(mode="after")
    def check_criteria(self):
        if len({c.id for c in self.criteria}) != len(self.criteria):
            raise ValueError("Criterion ids must be unique")
        if any(c.deadline_move > self.decision.moves for c in self.criteria):
            raise ValueError("Criterion deadline exceeds the decision horizon")
        if not self.board:
            raise ValueError("An initial public market board is required")
        return self


class Actor(Record):
    id: Identifier
    name: Text
    role: Literal["home", "competitor", "wildcard"]
    mode: Literal["agent", "human"] = "agent"


class Fact(Record):
    id: Identifier
    claim: Text
    sources: Texts


class Research(Record):
    facts: Annotated[list[Fact], Field(min_length=1)]

    @model_validator(mode="after")
    def unique_facts(self):
        if len({f.id for f in self.facts}) != len(self.facts):
            raise ValueError("Fact ids must be unique within a dossier")
        return self


class Dossier(Research):
    incentives: Texts
    constraints: Texts
    capabilities: Texts
    red_lines: Texts


class Move(Record):
    actions: Texts
    rationale: Text
    resource_commitments: Texts
    expected_responses: Texts


class HumanSubmission(Record):
    project_id: Text
    job_id: Identifier
    actor_id: Identifier
    kind: Literal["dossiers", "moves", "rulings", "rebuttals", "resolutions", "endgame", "novelty"]
    move: int
    participant_id: Text
    answer: dict[str, Any]


Probability = Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]
Basis = Literal["model", "reference_class", "dossier_fact", "testimony", "panel", "judgment"]


class Testimony(Record):
    id: Identifier
    claim: Text
    attribution: Text
    scope: Text
    sources: Texts


class HistoricalCase(Record):
    name: Text
    sources: Texts
    mapping: Text
    disanalogies: Text


class Evidence(Record):
    basis: Basis
    cite: list[Text] = Field(default_factory=list)
    cases: list[HistoricalCase] = Field(default_factory=list)


class MechanismLink(Evidence):
    link: Annotated[str, StringConstraints(pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")]
    claim: Text


class Outcome(Record):
    description: Text
    board_delta: dict[str, Any]
    confidence: Probability


class Branch(Record):
    id: Identifier
    description: Text
    probability: Probability
    board_delta: dict[str, Any]


class Branches(Record):
    uncertainty: Text
    options: Annotated[list[Branch], Field(min_length=2)]
    played: Identifier

    @model_validator(mode="after")
    def valid_distribution(self):
        ids = [b.id for b in self.options]
        if len(set(ids)) != len(ids) or self.played not in ids:
            raise ValueError("Branch ids must be unique and include the played branch")
        if abs(sum(b.probability for b in self.options) - 1) > 1e-9:
            raise ValueError("Branch probabilities must sum to one")
        return self


class Ruling(Evidence):
    ruling_id: Identifier
    rationale: Text
    resolves: Texts
    affected_actors: Annotated[list[Identifier], Field(min_length=1)]
    chain: Annotated[list[MechanismLink], Field(min_length=1)]
    outcome: Outcome
    kill_criteria: list[Identifier] = Field(default_factory=list)
    branches: Branches | None = None

    @model_validator(mode="after")
    def unique_references(self):
        for items in (
            self.resolves,
            self.affected_actors,
            self.kill_criteria,
            [link.link for link in self.chain],
        ):
            if len(set(items)) != len(items):
                raise ValueError("Ruling references and mechanism link ids must be unique")
        if self.branches:
            played = next(b for b in self.branches.options if b.id == self.branches.played)
            if played.board_delta != self.outcome.board_delta:
                raise ValueError("The outcome must match the played branch's board delta")
        return self


class RulingBatch(Record):
    rulings: Annotated[list[Ruling], Field(min_length=1)]

    @model_validator(mode="after")
    def unique_ids(self):
        if len({r.ruling_id for r in self.rulings}) != len(self.rulings):
            raise ValueError("Ruling ids must be unique within a round")
        return self


class EscalationPolicy(Record):
    human_budget: Annotated[int, Field(ge=0)] = 5
    auto_confidence: Probability = 0.7
    judgment_share_threshold: Probability = 0.4


class RoutingOptions(Record):
    flagged: list[Identifier] = Field(default_factory=list)
    impact_scores: dict[Identifier, Annotated[float, Field(ge=0, allow_inf_nan=False)]] = Field(
        default_factory=dict
    )


class Rebuttal(Record):
    ruling_id: Identifier
    stance: Literal["accept", "challenge"]
    argument: Text
    link: Text | None = None
    cite: list[Text] = Field(default_factory=list)

    @model_validator(mode="after")
    def attack_one_link(self):
        if self.stance == "challenge" and (not self.link or not self.cite):
            raise ValueError("A challenge must name one link and cite own-dossier evidence")
        if self.stance == "accept" and (self.link is not None or self.cite):
            raise ValueError("An acceptance has no attacked link or evidence citations")
        return self


class RebuttalBatch(Record):
    rebuttals: Annotated[list[Rebuttal], Field(min_length=1)]


class ControlResponse(Record):
    actor_id: Identifier
    reason: Text


class Resolution(Record):
    ruling_id: Identifier
    decision: Literal["affirm", "revise"]
    reason: Text
    responses: Annotated[list[ControlResponse], Field(min_length=1)]
    revision: Ruling | None = None

    @model_validator(mode="after")
    def check_revision(self):
        if (self.decision == "revise") != (self.revision is not None):
            raise ValueError("Only a revise decision must supply a replacement ruling")
        return self


class ResolutionBatch(Record):
    resolutions: Annotated[list[Resolution], Field(min_length=1)]


class BoardChoice(Record):
    ruling_id: Identifier
    reason: Text


class BoardChoices(Record):
    choices: dict[Text, BoardChoice]


def calendar_date(value):
    date.fromisoformat(value)
    return value


ISODate = Annotated[
    str, StringConstraints(pattern=r"^\d{4}-\d{2}-\d{2}$"), AfterValidator(calendar_date)
]


class CriterionEvaluation(Record):
    criterion_id: Identifier
    verdict: Literal["triggered", "grazed", "survived", "unevaluated"]
    rationale: Text
    ruling_refs: list[Text] = Field(default_factory=list)
    board_refs: list[Text] = Field(default_factory=list)


class InsightInput(Record):
    id: Identifier
    text: Text
    ruling_refs: Texts
    owner: Text | None = None
    due_date: ISODate | None = None
    baseline_matches: list[Identifier] = Field(default_factory=list)


class PredictionInput(Record):
    source: Text
    due_date: ISODate
    resolution_criteria: Text
    probability: Probability | None = None


class EndgameAssessment(Record):
    evaluations: Annotated[list[CriterionEvaluation], Field(min_length=1)]
    insights: list[InsightInput]
    predictions: Annotated[list[PredictionInput], Field(min_length=1)]


class BaselineFinding(Record):
    id: Identifier
    text: Text


class NoveltyBaseline(Record):
    findings: Annotated[list[BaselineFinding], Field(min_length=1)]


class OutcomeSource(Record):
    reference: Text
    description: Text


class LookbackEvent(Record):
    id: Identifier
    statement: Text
    prediction_ids: Annotated[list[Identifier], Field(min_length=1)]
    status: Literal["resolved", "unresolved", "unresolvable"]
    outcome: bool | None = None
    as_of: ISODate
    occurred_on: ISODate | None = None
    rationale: Text
    sources: Annotated[list[OutcomeSource], Field(min_length=1)]

    @model_validator(mode="after")
    def check_resolution(self):
        if (self.status == "resolved") != (self.outcome is not None):
            raise ValueError("Only resolved events require a boolean outcome")
        if self.outcome is True:
            if self.occurred_on is None or self.occurred_on > self.as_of:
                raise ValueError("Occurred events require occurred_on no later than as_of")
        elif self.occurred_on is not None:
            raise ValueError("Only occurred events may have occurred_on")
        if len(set(self.prediction_ids)) != len(self.prediction_ids):
            raise ValueError("Prediction ids must be unique within an event")
        return self


class LookbackInput(Record):
    events: Annotated[list[LookbackEvent], Field(min_length=1)]
