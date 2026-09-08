from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field, model_validator

from fireviewer_contracts.contracts import SafeIdentifierV2, Sha256HexV2, StrictModel
from fireviewer_contracts.mvp.contracts.common import (
    ProviderRun,
    SchemaContractModel,
    is_timezone_aware,
    validate_lon_lat,
)
from fireviewer_contracts.mvp.contracts.geographic_hypothesis_v1 import GeographicReference

AUTO_PUBLICATION_CONFIDENCE_THRESHOLD = 0.85


class CandidatePoint(StrictModel):
    """Immutable upstream spatial hypothesis; no supervising model may create it."""

    point_id: SafeIdentifierV2
    phenomenon: Literal["active_fire_point", "smoke_origin"]
    longitude: float = Field(ge=-180, le=180, allow_inf_nan=False)
    latitude: float = Field(ge=-90, le=90, allow_inf_nan=False)
    radius_m: float = Field(gt=0, le=1_000_000, allow_inf_nan=False)
    source_candidate_ids: tuple[SafeIdentifierV2, ...] = Field(
        min_length=1,
        max_length=128,
    )

    @model_validator(mode="after")
    def validate_point(self) -> CandidatePoint:
        validate_lon_lat((self.longitude, self.latitude), label="candidate point")
        if len(self.source_candidate_ids) != len(set(self.source_candidate_ids)):
            raise ValueError("candidate point source references must be unique")
        return self


class UploadLocationEvidence(StrictModel):
    location_id: SafeIdentifierV2
    media_id: SafeIdentifierV2
    longitude: float = Field(ge=-180, le=180, allow_inf_nan=False)
    latitude: float = Field(ge=-90, le=90, allow_inf_nan=False)
    accuracy_m: float = Field(gt=0, le=100_000, allow_inf_nan=False)
    location_origin: Literal["user_declared", "human_confirmed", "metadata"]
    captured_at: datetime | None = None
    heading_deg: float | None = Field(default=None, ge=0, lt=360, allow_inf_nan=False)
    pitch_deg: float | None = Field(default=None, ge=-90, le=90, allow_inf_nan=False)
    roll_deg: float | None = Field(default=None, ge=-180, le=180, allow_inf_nan=False)
    horizontal_fov_deg: float | None = Field(
        default=None,
        gt=0,
        lt=180,
        allow_inf_nan=False,
    )
    vertical_fov_deg: float | None = Field(
        default=None,
        gt=0,
        lt=180,
        allow_inf_nan=False,
    )
    image_width_px: int | None = Field(default=None, ge=1, le=100_000)
    image_height_px: int | None = Field(default=None, ge=1, le=100_000)
    heading_uncertainty_deg: float | None = Field(
        default=None,
        gt=0,
        le=180,
        allow_inf_nan=False,
    )
    altitude_m: float | None = Field(default=None, allow_inf_nan=False)
    altitude_uncertainty_m: float | None = Field(
        default=None,
        gt=0,
        le=10_000,
        allow_inf_nan=False,
    )
    source_record_sha256: Sha256HexV2

    @model_validator(mode="after")
    def validate_location(self) -> UploadLocationEvidence:
        validate_lon_lat((self.longitude, self.latitude), label="upload location")
        if self.captured_at is not None and not is_timezone_aware(self.captured_at):
            raise ValueError("upload location captured_at must include a timezone")
        if self.heading_deg is None and self.heading_uncertainty_deg is not None:
            raise ValueError("heading uncertainty requires a heading")
        if self.heading_deg is None and self.horizontal_fov_deg is not None:
            raise ValueError("horizontal field of view requires a heading")
        if (self.pitch_deg is None) != (self.roll_deg is None):
            raise ValueError("pitch and roll must be supplied together")
        if self.pitch_deg is not None and self.heading_deg is None:
            raise ValueError("camera pitch and roll require a heading")
        if self.vertical_fov_deg is not None and self.horizontal_fov_deg is None:
            raise ValueError("vertical field of view requires horizontal field of view")
        if (self.image_width_px is None) != (self.image_height_px is None):
            raise ValueError("image dimensions must be supplied together")
        if self.altitude_m is None and self.altitude_uncertainty_m is not None:
            raise ValueError("altitude uncertainty requires an altitude")
        return self


class PointEvidenceReference(StrictModel):
    evidence_id: SafeIdentifierV2
    evidence_type: Literal[
        "source",
        "claim",
        "media",
        "visual_observation",
        "satellite_observation",
        "location_candidate",
        "candidate_cluster",
        "contradiction",
        "uncertainty",
        "prior_fire_state",
        "geographic_reference",
    ]
    source_id: SafeIdentifierV2 | None = None
    media_id: SafeIdentifierV2 | None = None
    result_reference: SafeIdentifierV2 | None = None
    artifact_sha256: Sha256HexV2 | None = None


class PriorFireStateReference(StrictModel):
    state_id: SafeIdentifierV2
    state_kind: Literal["published_perimeter", "active_points", "situation_report"]
    observed_at: datetime
    artifact_reference: str = Field(min_length=1, max_length=2_000)
    artifact_sha256: Sha256HexV2
    distance_to_candidate_m: float | None = Field(
        default=None,
        ge=0,
        le=2_000_000,
        allow_inf_nan=False,
    )
    direction_consistency: float | None = Field(default=None, ge=0, le=1)
    read_only: Literal[True] = True

    @model_validator(mode="after")
    def validate_observed_at(self) -> PriorFireStateReference:
        if not is_timezone_aware(self.observed_at):
            raise ValueError("prior fire state observed_at must include a timezone")
        return self


class GeospatialConsistencyCheck(StrictModel):
    check_id: SafeIdentifierV2
    check_type: Literal[
        "camera_distance",
        "camera_bearing",
        "line_of_sight",
        "terrain_visibility",
        "satellite_overlap",
        "temporal_alignment",
        "history_progression",
    ]
    status: Literal["supported", "contradicted", "unknown"]
    score: float | None = Field(default=None, ge=0, le=1)
    reason_code: SafeIdentifierV2
    evidence_ids: tuple[SafeIdentifierV2, ...] = Field(min_length=1, max_length=128)
    hard_constraint: bool = False

    @model_validator(mode="after")
    def validate_check(self) -> GeospatialConsistencyCheck:
        if len(self.evidence_ids) != len(set(self.evidence_ids)):
            raise ValueError("geospatial check evidence references must be unique")
        if self.status == "unknown" and self.score is not None:
            raise ValueError("an unknown geospatial check cannot carry a score")
        if self.hard_constraint and self.status == "unknown":
            raise ValueError("an unknown geospatial check cannot be a hard constraint")
        return self


class RagContextExcerpt(StrictModel):
    document_id: SafeIdentifierV2
    evidence_type: SafeIdentifierV2
    text: str = Field(min_length=1, max_length=4_000)
    score: float = Field(ge=0, le=1)
    evidence_ids: tuple[SafeIdentifierV2, ...] = Field(min_length=1, max_length=128)
    observed_at: datetime | None = None
    center: tuple[float, float] | None = None
    content_sha256: Sha256HexV2

    @model_validator(mode="after")
    def validate_context(self) -> RagContextExcerpt:
        if self.observed_at is not None and not is_timezone_aware(self.observed_at):
            raise ValueError("RAG context observed_at must include a timezone")
        if self.center is not None:
            validate_lon_lat(self.center, label="RAG context center")
        if len(self.evidence_ids) != len(set(self.evidence_ids)):
            raise ValueError("RAG context evidence references must be unique")
        return self


class PointEvidenceBundleV1(SchemaContractModel):
    """Read-only evidence dossier for one upstream spatial hypothesis."""

    schema_name: Literal["fireviewer.point-evidence-bundle.v1"] = Field(
        default="fireviewer.point-evidence-bundle.v1",
        alias="schema",
    )
    bundle_id: SafeIdentifierV2
    event_id: SafeIdentifierV2
    point: CandidatePoint
    upload_locations: tuple[UploadLocationEvidence, ...] = Field(default=(), max_length=128)
    evidence_references: tuple[PointEvidenceReference, ...] = Field(
        min_length=1,
        max_length=2_048,
    )
    prior_fire_states: tuple[PriorFireStateReference, ...] = Field(default=(), max_length=256)
    geographic_references: tuple[GeographicReference, ...] = Field(
        default=(),
        max_length=512,
    )
    geospatial_checks: tuple[GeospatialConsistencyCheck, ...] = Field(
        default=(),
        max_length=512,
    )
    retrieved_context: tuple[RagContextExcerpt, ...] = Field(default=(), max_length=64)
    missing_evidence_codes: tuple[SafeIdentifierV2, ...] = Field(default=(), max_length=128)
    source_event_evidence_sha256: Sha256HexV2
    assembler_run: ProviderRun
    needs_human_review: bool = True
    geometry_mutation_allowed: Literal[False] = False

    @model_validator(mode="after")
    def validate_bundle(self) -> PointEvidenceBundleV1:
        collections = {
            "upload location": tuple(item.location_id for item in self.upload_locations),
            "evidence reference": tuple(item.evidence_id for item in self.evidence_references),
            "prior fire state": tuple(item.state_id for item in self.prior_fire_states),
            "geographic reference": tuple(item.reference_id for item in self.geographic_references),
            "geospatial check": tuple(item.check_id for item in self.geospatial_checks),
            "RAG document": tuple(item.document_id for item in self.retrieved_context),
        }
        for label, identifiers in collections.items():
            if len(identifiers) != len(set(identifiers)):
                raise ValueError(f"duplicate {label} identifier")

        evidence_ids = set(collections["evidence reference"])
        candidate_reference_ids = {
            item.evidence_id
            for item in self.evidence_references
            if item.evidence_type == "location_candidate"
        }
        if not set(self.point.source_candidate_ids).issubset(candidate_reference_ids):
            raise ValueError("candidate point references missing location candidate evidence")
        media_reference_ids = {
            item.evidence_id for item in self.evidence_references if item.evidence_type == "media"
        }
        if any(item.media_id not in media_reference_ids for item in self.upload_locations):
            raise ValueError("upload location references missing media evidence")
        if any(
            not set(item.evidence_ids).issubset(evidence_ids) for item in self.geospatial_checks
        ):
            raise ValueError("geospatial check references missing evidence")
        if any(
            not set(item.evidence_ids).issubset(evidence_ids) for item in self.retrieved_context
        ):
            raise ValueError("RAG context references missing evidence")
        prior_reference_ids = {
            item.evidence_id
            for item in self.evidence_references
            if item.evidence_type == "prior_fire_state"
        }
        if {item.state_id for item in self.prior_fire_states} != prior_reference_ids:
            raise ValueError("prior fire state references must match the supplied states")
        typed_geographic_reference_ids = {
            item.evidence_id
            for item in self.evidence_references
            if item.evidence_type == "geographic_reference"
        }
        supplied_geographic_reference_ids = {
            item.reference_id for item in self.geographic_references
        }
        if not typed_geographic_reference_ids.issubset(
            supplied_geographic_reference_ids
        ) or not supplied_geographic_reference_ids.issubset(evidence_ids):
            raise ValueError("geographic evidence references must match the supplied references")
        if len(self.missing_evidence_codes) != len(set(self.missing_evidence_codes)):
            raise ValueError("missing evidence codes must be unique")
        return self


class AssessmentSubscores(StrictModel):
    visual: float | None = Field(default=None, ge=0, le=1)
    camera_geo: float | None = Field(default=None, ge=0, le=1)
    satellite: float | None = Field(default=None, ge=0, le=1)
    history: float | None = Field(default=None, ge=0, le=1)
    text_sources: float | None = Field(default=None, ge=0, le=1)


class CompetingPointJsonV1(SchemaContractModel):
    """Standalone alternative point JSON that never replaces its source document."""

    schema_name: Literal["fireviewer.competing-point-correction.v1"] = Field(
        default="fireviewer.competing-point-correction.v1",
        alias="schema",
    )
    correction_id: SafeIdentifierV2
    event_id: SafeIdentifierV2
    source_point_id: SafeIdentifierV2
    source_bundle_sha256: Sha256HexV2
    point: CandidatePoint
    reason_codes: tuple[SafeIdentifierV2, ...] = Field(min_length=1, max_length=128)
    evidence_ids: tuple[SafeIdentifierV2, ...] = Field(default=(), max_length=512)
    relationship: Literal["competes_with_source"] = "competes_with_source"
    state: Literal["proposed"] = "proposed"
    source_mutation_allowed: Literal[False] = False

    @model_validator(mode="after")
    def validate_competing_json(self) -> CompetingPointJsonV1:
        if self.point.point_id == self.source_point_id:
            raise ValueError("a competing point JSON requires a distinct point identifier")
        if len(self.reason_codes) != len(set(self.reason_codes)):
            raise ValueError("competing point reason codes must be unique")
        if len(self.evidence_ids) != len(set(self.evidence_ids)):
            raise ValueError("competing point evidence references must be unique")
        return self


class PointAssessmentV1(SchemaContractModel):
    """Advisory evidence assessment with no coordinate or review authority."""

    schema_name: Literal["fireviewer.point-assessment.v1"] = Field(
        default="fireviewer.point-assessment.v1",
        alias="schema",
    )
    assessment_id: SafeIdentifierV2
    event_id: SafeIdentifierV2
    point_id: SafeIdentifierV2
    bundle_sha256: Sha256HexV2
    verdict: Literal["accept", "reject", "abstain"]
    model_confidence: float = Field(ge=0, le=1)
    calibrated_confidence: float | None = Field(default=None, ge=0, le=1)
    calibrator_id: SafeIdentifierV2 | None = None
    subscores: AssessmentSubscores = Field(default_factory=AssessmentSubscores)
    reason_codes: tuple[SafeIdentifierV2, ...] = Field(min_length=1, max_length=128)
    supporting_evidence_ids: tuple[SafeIdentifierV2, ...] = Field(default=(), max_length=512)
    contradicting_evidence_ids: tuple[SafeIdentifierV2, ...] = Field(
        default=(),
        max_length=512,
    )
    hard_contradiction_codes: tuple[SafeIdentifierV2, ...] = Field(
        default=(),
        max_length=128,
    )
    missing_evidence_codes: tuple[SafeIdentifierV2, ...] = Field(default=(), max_length=128)
    competing_point_json: CompetingPointJsonV1 | None = None
    release_status: Literal[
        "eligible_for_automatic_publication",
        "held_for_review",
    ]
    supervisor_mode: Literal["managed_vl", "simulated"]
    provider_run: ProviderRun
    prompt_version: str = Field(min_length=1, max_length=255)
    needs_human_review: bool = True
    geometry_mutation_allowed: Literal[False] = False

    @model_validator(mode="after")
    def validate_assessment(self) -> PointAssessmentV1:
        if (self.calibrated_confidence is None) != (self.calibrator_id is None):
            raise ValueError("calibrated confidence and calibrator id must be supplied together")
        for label, values in (
            ("reason code", self.reason_codes),
            ("supporting evidence", self.supporting_evidence_ids),
            ("contradicting evidence", self.contradicting_evidence_ids),
            ("hard contradiction", self.hard_contradiction_codes),
            ("missing evidence", self.missing_evidence_codes),
        ):
            if len(values) != len(set(values)):
                raise ValueError(f"duplicate {label}")
        if set(self.supporting_evidence_ids) & set(self.contradicting_evidence_ids):
            raise ValueError("one evidence record cannot be both supporting and contradicting")
        if self.competing_point_json is not None:
            competing = self.competing_point_json
            if (
                competing.event_id != self.event_id
                or competing.source_point_id != self.point_id
                or competing.source_bundle_sha256 != self.bundle_sha256
            ):
                raise ValueError("competing point JSON must reference the assessed source document")
        auto_publication = self.release_status == "eligible_for_automatic_publication"
        eligible = (
            self.verdict == "accept"
            and self.calibrated_confidence is not None
            and self.calibrated_confidence > AUTO_PUBLICATION_CONFIDENCE_THRESHOLD
            and self.supervisor_mode == "managed_vl"
            and not self.hard_contradiction_codes
            and not self.missing_evidence_codes
        )
        if auto_publication != eligible:
            raise ValueError(
                "automatic publication requires an accepted assessment with calibrated "
                "confidence strictly above 0.85"
            )
        if self.needs_human_review == auto_publication:
            raise ValueError("human review is required unless automatic publication is eligible")
        return self


__all__ = [
    "AUTO_PUBLICATION_CONFIDENCE_THRESHOLD",
    "AssessmentSubscores",
    "CandidatePoint",
    "CompetingPointJsonV1",
    "GeospatialConsistencyCheck",
    "PointAssessmentV1",
    "PointEvidenceBundleV1",
    "PointEvidenceReference",
    "PriorFireStateReference",
    "RagContextExcerpt",
    "UploadLocationEvidence",
]
