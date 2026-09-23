"""Incident revisions: event time, knowledge time and immutable evidence identities."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any, Literal, Self

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from .fire_state_schemas import (
    FireProbabilityGridReference,
    FireSpatialProvenanceGridReference,
    SpatialObservationV2,
)
from .part4_framing_schemas import checked_polygon

Identifier = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$")]
Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
ReconstructionMode = Literal["causal", "retrospective"]


class TemporalModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid", frozen=True, populate_by_name=True, allow_inf_nan=False
    )

    @field_validator("*", mode="after")
    @classmethod
    def normalize_instants(cls, value: Any) -> Any:
        if isinstance(value, datetime):
            return value.astimezone(UTC)
        return value


class EvidenceRevisionRef(TemporalModel):
    evidence_id: Identifier
    revision: int = Field(ge=1)
    content_sha256: Digest


class ProcessingStep(TemporalModel):
    component: Literal[
        "vision", "ocr", "transcription", "geolocation", "supervisor", "fusion"
    ]
    status: Literal["required", "completed", "not_applicable", "blocked"]
    reason: str = Field(min_length=1, max_length=256)


class TemporalEvidence(TemporalModel):
    schema_name: Literal["fireviewer.temporal-evidence.v1"] = Field(
        default="fireviewer.temporal-evidence.v1", alias="schema"
    )
    evidence_id: Identifier
    revision: int = Field(ge=1)
    incident_id: Identifier
    episode_id: Identifier | None = None
    source_id: str = Field(min_length=1, max_length=512)
    content_sha256: Digest
    license: str = Field(min_length=1, max_length=512)
    access: Literal["private", "public"] = "private"
    public_derivative_allowed: bool = True
    admissibility: Literal["pending", "admitted", "rejected", "withdrawn"] = "pending"
    media_kind: Literal["geometry", "image", "video", "text", "audio", "satellite"]
    observed_at: AwareDatetime | None = None
    observed_until: AwareDatetime | None = None
    time_precision: Literal["instant", "interval", "unknown"] = "instant"
    published_at: AwareDatetime | None = None
    available_at: AwareDatetime | None = None
    retrieved_at: AwareDatetime
    recorded_at: AwareDatetime
    observations: tuple[SpatialObservationV2, ...] = Field(default=(), max_length=4096)
    needs_human_review: bool = False
    supersedes_revision: int | None = Field(default=None, ge=1)
    enrichment_state: Literal["pending", "completed", "abstained", "failed"] = "pending"
    enrichment_receipt_sha256: Digest | None = None
    processing: tuple[ProcessingStep, ...] = Field(default=(), max_length=64)

    @property
    def reference(self) -> EvidenceRevisionRef:
        return EvidenceRevisionRef(
            evidence_id=self.evidence_id,
            revision=self.revision,
            content_sha256=self.content_sha256,
        )

    @property
    def known_at(self) -> datetime:
        """When this version was available to this system, never acquisition time."""
        return max(
            value
            for value in (
                self.recorded_at,
                self.retrieved_at,
                self.published_at,
                self.available_at,
            )
            if value is not None
        )

    @model_validator(mode="after")
    def coherent_evidence(self) -> Self:
        if self.observations and self.enrichment_state == "pending":
            object.__setattr__(self, "enrichment_state", "completed")
        if self.enrichment_state == "abstained" and self.observations:
            raise ValueError("an abstention cannot supply spatial observations")
        if self.enrichment_state == "completed" and not self.observations:
            raise ValueError("completed enrichment requires spatial observations")
        if self.revision == 1 and self.supersedes_revision is not None:
            raise ValueError("initial evidence cannot supersede another revision")
        if self.revision > 1 and self.supersedes_revision != self.revision - 1:
            raise ValueError(
                "evidence revisions must explicitly supersede their predecessor"
            )
        if self.recorded_at < self.retrieved_at:
            raise ValueError("recorded_at must not precede retrieved_at")
        if self.time_precision == "unknown":
            if (
                self.observed_at is not None
                or self.observed_until is not None
                or self.observations
            ):
                raise ValueError(
                    "unknown observation time cannot supply a fabricated instant"
                )
        elif self.observed_at is None:
            raise ValueError("dated evidence requires observed_at")
        if self.time_precision == "interval":
            if (self.observed_at is None or self.observed_until is None
                    or self.observed_until <= self.observed_at):
                raise ValueError(
                    "interval evidence requires an ordered, nonempty interval"
                )
        elif self.observed_until is not None:
            raise ValueError("observed_until requires interval precision")
        ids = [item.observation_id for item in self.observations]
        if len(ids) != len(set(ids)):
            raise ValueError(
                "observation identities must be unique within a proof version"
            )
        for item in self.observations:
            if self.observed_at is not None and item.observed_at < self.observed_at:
                raise ValueError("observation precedes its evidence interval")
            end = item.observed_end_at or item.observed_at
            bound = self.observed_until or self.observed_at
            if bound is None or end > bound:
                raise ValueError("observation exceeds its evidence interval")
        return self


def check_cutoff(
    valid_at: datetime, cutoff: datetime, mode: ReconstructionMode
) -> None:
    if cutoff < valid_at:
        raise ValueError("knowledge cutoff cannot precede the represented instant")
    if mode == "causal" and cutoff != valid_at:
        raise ValueError("causal reconstruction requires knowledge_cutoff == valid_at")


class IncidentStateRevision(TemporalModel):
    schema_name: Literal["fireviewer.incident-state-revision.v1"] = Field(
        default="fireviewer.incident-state-revision.v1", alias="schema"
    )
    revision_id: Identifier
    incident_id: Identifier
    episode_id: Identifier
    valid_from: AwareDatetime
    valid_until: AwareDatetime | None = None
    knowledge_cutoff: AwareDatetime
    reconstruction_mode: ReconstructionMode
    calculated_at: AwareDatetime
    parent_revision_id: Identifier | None = None
    supersedes_revision_id: Identifier | None = None
    affected_geometry: dict[str, Any] | None = None
    active_geometry: dict[str, Any] | None = None
    uncertainty_geometry: dict[str, Any] | None = None
    observable_fraction: float | None = Field(default=None, ge=0, le=1)
    probability_grid: FireProbabilityGridReference | None = None
    provenance_grid: FireSpatialProvenanceGridReference | None = None
    evidence_refs: tuple[EvidenceRevisionRef, ...] = Field(default=(), max_length=20000)
    algorithm_id: str = Field(min_length=1, max_length=160)
    algorithm_revision: str = Field(min_length=1, max_length=160)
    fusion_profile_sha256: Digest
    spatial_context_sha256: Digest
    seed_id: Identifier
    source_input_sha256: Digest
    quality: Literal["observed", "fused", "interpolated", "insufficient"]
    calibration_state: Literal["uncalibrated", "calibrated"] = "uncalibrated"
    limitations: tuple[str, ...] = Field(default=(), max_length=256)
    human_review_state: Literal["pending", "approved", "rejected"] = "pending"
    publication_state: Literal["unpublished", "published", "withdrawn"] = "unpublished"

    @model_validator(mode="after")
    def coherent_state(self) -> Self:
        check_cutoff(self.valid_from, self.knowledge_cutoff, self.reconstruction_mode)
        if self.calculated_at < self.knowledge_cutoff:
            raise ValueError("calculation cannot use future knowledge")
        if self.valid_until is not None and self.valid_until <= self.valid_from:
            raise ValueError("state intervals are nonempty and half-open")
        if self.revision_id in (self.parent_revision_id, self.supersedes_revision_id):
            raise ValueError("a revision cannot depend on or replace itself")
        refs = [ref.evidence_id for ref in self.evidence_refs]
        if len(refs) != len(set(refs)):
            raise ValueError("only one version of each proof belongs in a calculation")
        for geometry in (
            self.affected_geometry,
            self.active_geometry,
            self.uncertainty_geometry,
        ):
            if geometry is not None:
                checked_polygon(geometry)
        if (self.probability_grid is None) != (self.provenance_grid is None):
            raise ValueError("probability and provenance grids must travel together")
        if (
            self.publication_state == "published"
            and self.human_review_state != "approved"
        ):
            raise ValueError("publication requires approval of this exact revision")
        return self


class IncidentUpdateRequest(TemporalModel):
    schema_name: Literal["fireviewer.incident-update-request.v1"] = Field(
        default="fireviewer.incident-update-request.v1", alias="schema"
    )
    incident_id: Identifier
    episode_id: Identifier
    trigger: Literal["evidence", "correction", "withdrawal", "expiry", "manual"]
    valid_at: AwareDatetime
    knowledge_cutoff: AwareDatetime
    reconstruction_mode: ReconstructionMode = "retrospective"
    expected_current_revision_id: Identifier | None = None
    changed_evidence_refs: tuple[EvidenceRevisionRef, ...] = Field(
        default=(), max_length=4096
    )
    idempotency_key: str = Field(min_length=8, max_length=128)

    @model_validator(mode="after")
    def coherent_request(self) -> Self:
        check_cutoff(self.valid_at, self.knowledge_cutoff, self.reconstruction_mode)
        identities = [ref.evidence_id for ref in self.changed_evidence_refs]
        if len(identities) != len(set(identities)):
            raise ValueError("changed evidence identities must be unique")
        return self


class IncidentUpdateResult(TemporalModel):
    schema_name: Literal["fireviewer.incident-update-result.v1"] = Field(
        default="fireviewer.incident-update-result.v1", alias="schema"
    )
    incident_id: Identifier
    status: Literal[
        "created",
        "unchanged",
        "awaiting_initialization",
        "awaiting_evidence",
        "awaiting_enrichment",
    ]
    revision: IncidentStateRevision | None = None
    affected_from: AwareDatetime | None = None
    affected_until: AwareDatetime | None = None
    recalculated_revision_ids: tuple[Identifier, ...] = ()
    processing: tuple[ProcessingStep, ...] = ()

    @model_validator(mode="after")
    def coherent_result(self) -> Self:
        if (self.status in {"created", "unchanged"}) != (self.revision is not None):
            raise ValueError("only completed updates carry a revision")
        if self.revision is not None and self.revision.incident_id != self.incident_id:
            raise ValueError("result revision belongs to another incident")
        if self.affected_until is not None and (
            self.affected_from is None or self.affected_until <= self.affected_from
        ):
            raise ValueError("affected temporal range must be ordered")
        return self
