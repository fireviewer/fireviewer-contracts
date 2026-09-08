"""Inputs and audit contracts for seeded, geographically bounded reconstruction."""

from __future__ import annotations

import json
import math
from datetime import date
from typing import Any, Literal, Self
from zoneinfo import ZoneInfo

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator
from pyproj import Transformer
from shapely.geometry import MultiPolygon, Point, Polygon, shape
from shapely.ops import transform

_PROJECT = Transformer.from_crs(4326, 2154, always_xy=True)


def checked_polygon(value: dict[str, Any]) -> dict[str, Any]:
    """Reject invalid input; never repair an administrative/evidence polygon."""
    geometry = shape(value)
    if not isinstance(geometry, Polygon | MultiPolygon) or geometry.is_empty:
        raise ValueError("a non-empty Polygon or MultiPolygon is required")
    if not geometry.is_valid:
        raise ValueError("polygon topology is invalid")
    bounds = geometry.bounds
    if not all(math.isfinite(x) for x in bounds):
        raise ValueError("polygon coordinates must be finite")
    if bounds[0] < -180 or bounds[2] > 180 or bounds[1] < -90 or bounds[3] > 90:
        raise ValueError("polygon must use WGS84 longitude/latitude")
    return value


class FramingModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid", frozen=True, allow_inf_nan=False, populate_by_name=True
    )


class InitialSpatialStateInput(FramingModel):
    affected: dict[str, Any]
    valid_at: AwareDatetime
    horizontal_accuracy_m: float = Field(default=100, gt=0, le=10_000)
    position_accuracy_m: float = Field(default=25, gt=0, le=10_000)

    @model_validator(mode="after")
    def validate_geometry(self) -> Self:
        checked_polygon(self.affected)
        # JSON normalization only; coordinates and topology are not repaired.
        object.__setattr__(self, "affected", json.loads(json.dumps(self.affected, allow_nan=False)))
        return self


class IncidentSpatialSeedV1(InitialSpatialStateInput):
    schema_name: Literal["fireviewer.incident-spatial-seed.v1"] = Field(
        default="fireviewer.incident-spatial-seed.v1", alias="schema"
    )
    seed_id: str = Field(min_length=3, max_length=160)
    revision: int = Field(ge=1)
    incident_id: str = Field(min_length=3, max_length=128)
    episode_id: str = Field(min_length=3, max_length=128)
    longitude: float = Field(ge=-180, le=180)
    latitude: float = Field(ge=-90, le=90)
    created_at: AwareDatetime
    created_by: str = Field(min_length=1, max_length=255)
    provenance: Literal["admin_declared", "historical_seed_only"] = "admin_declared"
    source_reference_id: str | None = None
    supersedes_seed_id: str | None = None
    calibration_state: Literal["uncalibrated"] = "uncalibrated"

    @model_validator(mode="after")
    def validate_position(self) -> Self:
        geometry = transform(_PROJECT.transform, shape(self.affected))
        position = transform(_PROJECT.transform, Point(self.longitude, self.latitude))
        if geometry.distance(position) > self.position_accuracy_m + self.horizontal_accuracy_m:
            raise ValueError("incident position is inconsistent with the initial perimeter")
        if self.provenance == "historical_seed_only" and not self.source_reference_id:
            raise ValueError("a historical seed must identify its excluded reference")
        return self


class SpatialSeedRevisionRequest(InitialSpatialStateInput):
    longitude: float = Field(ge=-180, le=180)
    latitude: float = Field(ge=-90, le=90)
    expected_revision: int = Field(ge=0)
    reason: str = Field(min_length=10, max_length=500)


class SurfaceAreaClaimV1(FramingModel):
    """Provider extraction: provenance and incident identity are attached by the backend."""

    component: Literal["affected", "active"]
    scope: Literal["incident", "episode"] = "incident"
    accumulation: Literal["cumulative", "incremental"] = "cumulative"
    qualifier: Literal["exact", "approximate", "minimum", "maximum", "interval"]
    value_ha: float | None = Field(default=None, ge=0)
    lower_ha: float | None = Field(default=None, ge=0)
    upper_ha: float | None = Field(default=None, ge=0)
    valid_from: AwareDatetime
    valid_until: AwareDatetime

    @model_validator(mode="after")
    def validate_claim(self) -> Self:
        # Use the same semantic validator before adding trusted provenance.
        SurfaceAreaEvidenceV1.model_validate(
            {
                **self.model_dump(),
                "evidence_id": "validation",
                "incident_id": "validation",
                "episode_id": "validation",
                "available_at": self.valid_until,
                "source_id": "validation",
                "source_revision": "validation",
                "lineage_id": "validation",
            }
        )
        return self


class SurfaceAreaEvidenceV1(FramingModel):
    schema_name: Literal["fireviewer.surface-area-evidence.v1"] = Field(
        default="fireviewer.surface-area-evidence.v1", alias="schema"
    )
    evidence_id: str = Field(min_length=1, max_length=160)
    incident_id: str = Field(min_length=3, max_length=128)
    episode_id: str | None = None
    component: Literal["affected", "active"]
    scope: Literal["incident", "episode"] = "incident"
    accumulation: Literal["cumulative", "incremental"] = "cumulative"
    qualifier: Literal["exact", "approximate", "minimum", "maximum", "interval"]
    value_ha: float | None = Field(default=None, ge=0)
    lower_ha: float | None = Field(default=None, ge=0)
    upper_ha: float | None = Field(default=None, ge=0)
    valid_from: AwareDatetime
    valid_until: AwareDatetime
    available_at: AwareDatetime
    source_id: str = Field(min_length=1, max_length=512)
    source_revision: str = Field(min_length=1, max_length=512)
    lineage_id: str = Field(min_length=1, max_length=512)

    @model_validator(mode="after")
    def validate_bounds(self) -> Self:
        if self.valid_until < self.valid_from:
            raise ValueError("surface evidence time interval is reversed")
        if self.scope == "episode" and not self.episode_id:
            raise ValueError("episode-scoped area evidence requires an episode")
        if (
            self.lower_ha is not None
            and self.upper_ha is not None
            and self.lower_ha > self.upper_ha
        ):
            raise ValueError("surface evidence bounds are reversed")
        if self.qualifier in {"exact", "approximate"} and self.value_ha is None:
            raise ValueError("nominal area is required")
        if self.qualifier == "minimum" and self.lower_ha is None:
            raise ValueError("an explicit minimum is required")
        if self.qualifier == "maximum" and self.upper_ha is None:
            raise ValueError("an explicit maximum is required")
        if self.qualifier == "interval" and (self.lower_ha is None or self.upper_ha is None):
            raise ValueError("an explicit area interval is required")
        return self


class SpatialAnchorV1(FramingModel):
    observation_id: str
    observed_at: AwareDatetime
    geometry: dict[str, Any]
    horizontal_accuracy_m: float = Field(ge=0)
    source_family_id: str
    lineage_id: str
    kind: str


class SpatialObservationAvailabilityV1(FramingModel):
    known_at: AwareDatetime
    basis: Literal["source_available_at", "first_case_cutoff"]


class SpatialAdmissionDecisionV1(FramingModel):
    product_key: str
    observation_fingerprint: str
    source_revision_sha256: str
    processor_revision: str
    decided_at: AwareDatetime
    admitted_observation_id: str | None = None
    reason: str
    held_component_ids: tuple[str, ...] = ()
    proposal_ids: tuple[str, ...] = ()


class Part4SpatialContextV1(FramingModel):
    schema_name: Literal["fireviewer.part4-spatial-context.v1"] = Field(
        default="fireviewer.part4-spatial-context.v1", alias="schema"
    )
    seed: IncidentSpatialSeedV1
    local_date: date
    state_valid_at: AwareDatetime
    evaluation_cutoff_at: AwareDatetime
    parent_state_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    parent_valid_at: AwareDatetime | None = None
    admissible_domain: dict[str, Any]
    calculation_domain: dict[str, Any]
    anchors: tuple[SpatialAnchorV1, ...] = ()
    area_evidence: tuple[SurfaceAreaEvidenceV1, ...] = ()
    forbidden_reference_ids: tuple[str, ...] = ()
    policy_revision: Literal[
        "part4-spatial-framing-1.0.0", "part4-spatial-framing-1.1.0"
    ] = "part4-spatial-framing-1.1.0"
    observation_availability: dict[str, SpatialObservationAvailabilityV1] = Field(
        default_factory=dict
    )
    human_correction_id: str | None = None

    @model_validator(mode="after")
    def validate_context(self) -> Self:
        checked_polygon(self.admissible_domain)
        checked_polygon(self.calculation_domain)
        if self.seed.valid_at > self.state_valid_at:
            raise ValueError("initial state is in the future")
        if self.state_valid_at > self.evaluation_cutoff_at:
            raise ValueError("state exceeds the evaluation cutoff")
        if self.state_valid_at.astimezone(ZoneInfo("Europe/Paris")).date() != self.local_date:
            raise ValueError("state time differs from its local day")
        if (self.parent_state_sha256 is None) != (self.parent_valid_at is None):
            raise ValueError("parent identity and time must be supplied together")
        if self.parent_valid_at is not None and self.parent_valid_at >= self.state_valid_at:
            raise ValueError("parent must precede the current state")
        if not shape(self.calculation_domain).covers(shape(self.admissible_domain)):
            raise ValueError("calculation domain must contain the admissible domain")
        return self


class FramingArtifactReference(FramingModel):
    uri: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    byte_count: int = Field(gt=0)


class Part4FramingReceiptV1(FramingModel):
    schema_name: Literal["fireviewer.part4-framing-receipt.v1"] = Field(
        default="fireviewer.part4-framing-receipt.v1", alias="schema"
    )
    context_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    seed_id: str
    seed_revision: int
    parent_state_sha256: str | None = None
    parent_eligible: bool = True
    previous_area_ha: float = Field(ge=0)
    affected_area_ha: float = Field(ge=0)
    added_area_ha: float = Field(ge=0)
    spatially_supported_area_ha: float = Field(ge=0)
    outside_domain_area_ha: float = Field(ge=0)
    admitted_observation_ids: tuple[str, ...] = ()
    rejected_observations: dict[str, str] = Field(default_factory=dict)
    observation_admissions: dict[str, SpatialAdmissionDecisionV1] = Field(default_factory=dict)
    late_observation_ids: tuple[str, ...] = ()
    pending_proposal_ids: tuple[str, ...] = ()
    area_checks: tuple[dict[str, Any], ...] = ()
    reason_codes: tuple[str, ...] = ()
    active_knowledge: Literal["unknown", "estimated"] = "unknown"
    # GeometryCorrectionProposalV1 is validated by the producer; storing the
    # serialized contract here avoids a circular dependency with fire states.
    competing_proposals: tuple[dict[str, Any], ...] = ()
    human_review: dict[str, Any] | None = None


class Part4HumanCorrectionRequestV1(FramingModel):
    expected_state_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    correction_id: str = Field(min_length=3, max_length=200)
    reason: str = Field(min_length=10, max_length=1000)
