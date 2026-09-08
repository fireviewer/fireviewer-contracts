from __future__ import annotations

import os
from datetime import datetime
from enum import StrEnum
from hashlib import sha256
from math import fmod
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from fireviewer_contracts.geometry_contract import validate_geojson_geometry


class EventModel(BaseModel):
    """Closed, immutable contract used at the event-analysis boundary."""

    model_config = ConfigDict(extra="forbid", frozen=True)


Identifier = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")]
Sha256Hex = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class EvidenceAssetKind(StrEnum):
    IMAGE = "image"
    VIDEO = "video"
    DOCUMENT = "document"


class ViewpointOrigin(StrEnum):
    USER_PLACED = "USER_PLACED"
    DEVICE_GPS = "DEVICE_GPS"
    NAMED_PLACE = "NAMED_PLACE"
    HUMAN_CONFIRMED = "HUMAN_CONFIRMED"


class ShotScale(StrEnum):
    WIDE = "wide"
    DISTANT = "distant"
    CLOSE = "close"
    TIGHT = "tight"


class ViewProfile(StrEnum):
    GROUND_WIDE_KNOWN_VIEWPOINT = "ground_wide_known_viewpoint"
    GROUND_WIDE_NAMED_VIEWPOINT = "ground_wide_named_viewpoint"
    GROUND_DISTANT_KNOWN_VIEWPOINT = "ground_distant_known_viewpoint"
    GROUND_CLOSE_KNOWN_VIEWPOINT = "ground_close_known_viewpoint"
    GROUND_TIGHT_KNOWN_VIEWPOINT = "ground_tight_known_viewpoint"


class SemanticRole(StrEnum):
    RAW_EARTH_OBSERVATION = "raw_earth_observation"
    SENSOR_DETECTION = "sensor_detection"
    INTERPRETED_OBSERVATION = "interpreted_observation"
    OFFICIAL_INCIDENT_STATEMENT = "official_incident_statement"
    WEATHER_OBSERVATION = "weather_observation"
    WEATHER_FORECAST = "weather_forecast"
    GEOSPATIAL_REFERENCE = "geospatial_reference"
    HISTORICAL_REGISTRY = "historical_registry"
    SIMULATION = "simulation"


class PhenomenonKind(StrEnum):
    ACTIVE_FIRE_POINT = "active_fire_point"
    VISIBLE_FIRE_FRONT = "visible_fire_front"
    SMOKE_COLUMN_BASE = "smoke_column_base"
    SMOKE_ORIGIN = "smoke_origin"
    THERMAL_HOTSPOT = "thermal_hotspot"
    ACTIVITY_ENVELOPE = "activity_envelope"
    BURNED_AREA = "burned_area"
    SIMULATION = "simulation"


class LocalizationStatus(StrEnum):
    LOCALIZED = "localized"
    SECTOR = "sector"
    ABSTAINED = "abstained"


class LocalizationMethod(StrEnum):
    CAMERA_RAYCAST = "camera_raycast"
    TRIANGULATION = "triangulation"
    VIEWPOINT_SECTOR = "viewpoint_sector"
    CROSS_VIEW_RAYCAST = "cross_view_raycast"
    EXPLICIT_SOURCE_GEOMETRY = "explicit_source_geometry"


class PipelineStatus(StrEnum):
    NEEDS_REVIEW = "needs_review"
    ABSTAINED = "abstained"
    FAILED = "failed"


ProposalPhenomenon = Literal[
    PhenomenonKind.ACTIVE_FIRE_POINT,
    PhenomenonKind.VISIBLE_FIRE_FRONT,
    PhenomenonKind.SMOKE_ORIGIN,
]


class Viewpoint(EventModel):
    longitude: float = Field(ge=-180, le=180)
    latitude: float = Field(ge=-90, le=90)
    horizontal_accuracy_m: float = Field(gt=0, le=100_000)
    altitude_m: float | None = Field(default=None, allow_inf_nan=False)
    label: str | None = Field(default=None, min_length=1, max_length=500)
    yaw_deg: float | None = Field(default=None, ge=0, lt=360)
    fov_deg: float | None = Field(default=None, gt=0, lt=180)
    origin: ViewpointOrigin

    @model_validator(mode="after")
    def orientation_is_complete(self) -> Viewpoint:
        if (self.yaw_deg is None) != (self.fov_deg is None):
            raise ValueError("viewpoint direction requires both yaw_deg and fov_deg")
        if self.origin == ViewpointOrigin.NAMED_PLACE and not self.label:
            raise ValueError("named viewpoints require a label")
        return self


class ObservedTime(EventModel):
    start_at: datetime
    end_at: datetime | None = None

    @model_validator(mode="after")
    def validate_interval(self) -> ObservedTime:
        values = (self.start_at,) if self.end_at is None else (self.start_at, self.end_at)
        if any(value.tzinfo is None or value.utcoffset() is None for value in values):
            raise ValueError("event observation times must include a timezone")
        if self.end_at is not None and self.end_at < self.start_at:
            raise ValueError("event observation end must not precede its start")
        return self


class EvidenceAsset(EventModel):
    evidence_asset_id: Identifier
    kind: EvidenceAssetKind
    sha256: Sha256Hex
    object_uri: str = Field(min_length=1, max_length=2_048)
    declared_media_type: str = Field(min_length=3, max_length=255)
    size_bytes: int = Field(gt=0, le=2_147_483_648)
    working_file_url: str | None = Field(default=None, min_length=1, max_length=2_048)


class EventConsent(EventModel):
    analysis: Literal[True]
    retention: Literal[True]
    public_derivative: bool = False


class BundleProvenance(EventModel):
    received_at: datetime
    idempotency_key: Identifier
    trace_id: Identifier | None = None

    @model_validator(mode="after")
    def received_time_is_aware(self) -> BundleProvenance:
        if self.received_at.tzinfo is None or self.received_at.utcoffset() is None:
            raise ValueError("received_at must include a timezone")
        return self


class ExternalObservation(EventModel):
    observation_id: Identifier
    artifact_revision_id: Identifier
    lineage_family_id: Identifier
    semantic_role: SemanticRole
    phenomenon: PhenomenonKind | None = None
    observed_at: datetime | None = None
    geometry_geojson: dict[str, Any] | None = None
    resolution_m: float | None = Field(default=None, gt=0)
    conflicts_with: tuple[Identifier, ...] = Field(default=(), max_length=64)

    @model_validator(mode="after")
    def validate_external_observation(self) -> ExternalObservation:
        if self.observed_at is not None and (
            self.observed_at.tzinfo is None or self.observed_at.utcoffset() is None
        ):
            raise ValueError("external observation times must include a timezone")
        if self.geometry_geojson is not None:
            validate_geojson_geometry(self.geometry_geojson)
        if self.semantic_role == SemanticRole.WEATHER_FORECAST and self.phenomenon is not None:
            raise ValueError("a weather forecast cannot assert an observed fire phenomenon")
        if self.semantic_role == SemanticRole.SIMULATION and self.phenomenon not in {
            None,
            PhenomenonKind.SIMULATION,
        }:
            raise ValueError("simulation inputs must remain semantically marked as simulation")
        return self


class EventCandidateBundle(EventModel):
    schema_version: Literal["event-2.0"] = "event-2.0"
    candidate_id: Identifier
    incident_id: Identifier | None = None
    incident_candidate_id: Identifier | None = None
    episode_id: Identifier | None = None
    viewpoint: Viewpoint
    observed_time: ObservedTime
    shot_scale: ShotScale | None = None
    message: str | None = Field(default=None, min_length=1, max_length=100_000)
    evidence_assets: tuple[EvidenceAsset, ...] = Field(default=(), max_length=20)
    consent: EventConsent
    provenance: BundleProvenance
    external_observations: tuple[ExternalObservation, ...] = Field(default=(), max_length=256)

    @model_validator(mode="after")
    def validate_bundle(self) -> EventCandidateBundle:
        if (self.incident_id is None) == (self.incident_candidate_id is None):
            raise ValueError("exactly one incident or private incident candidate is required")
        if not self.message and not self.evidence_assets:
            raise ValueError("an event candidate requires a message or at least one media asset")
        asset_ids = [asset.evidence_asset_id for asset in self.evidence_assets]
        if len(asset_ids) != len(set(asset_ids)):
            raise ValueError("evidence asset identifiers must be unique")
        return self


class PerceptionAnchor(EventModel):
    anchor_id: Identifier
    evidence_asset_id: Identifier
    phenomenon: Literal[
        PhenomenonKind.ACTIVE_FIRE_POINT,
        PhenomenonKind.VISIBLE_FIRE_FRONT,
        PhenomenonKind.SMOKE_COLUMN_BASE,
    ]
    source_point_normalized: tuple[float, float] | None = None
    source_geometry_normalized: dict[str, Any] | None = None
    model_id: str = Field(min_length=1, max_length=500)
    model_revision: str = Field(min_length=1, max_length=255)
    model_score: float | None = Field(default=None, ge=0, le=1)

    @model_validator(mode="after")
    def validate_anchor(self) -> PerceptionAnchor:
        if (self.source_point_normalized is None) == (self.source_geometry_normalized is None):
            raise ValueError("a perception anchor requires exactly one pixel point or geometry")
        if self.source_point_normalized is not None and any(
            coordinate < 0 or coordinate > 1 for coordinate in self.source_point_normalized
        ):
            raise ValueError("source point coordinates must be normalized")
        if self.source_geometry_normalized is not None:
            allowed = (
                {"LineString", "MultiLineString"}
                if self.phenomenon == PhenomenonKind.VISIBLE_FIRE_FRONT
                else {"Point"}
            )
            validate_geojson_geometry(
                self.source_geometry_normalized,
                allowed_types=allowed,
                normalized=True,
            )
        return self


class SpatialEvidence(EventModel):
    anchor_id: Identifier
    status: Literal["projected", "insufficient_geometry"]
    method: LocalizationMethod | None = None
    geometry_geojson: dict[str, Any] | None = None
    horizontal_accuracy_m: float | None = Field(default=None, gt=0, le=100_000)
    direction_uncertainty_deg: float | None = Field(default=None, ge=0, le=180)
    distance_uncertainty_m: float | None = Field(default=None, ge=0, le=100_000)
    reason_codes: tuple[str, ...] = Field(default=(), max_length=32)
    reference_revision: str | None = Field(default=None, max_length=255)

    @model_validator(mode="after")
    def validate_spatial_evidence(self) -> SpatialEvidence:
        if self.status == "projected":
            if self.method is None or self.geometry_geojson is None:
                raise ValueError("projected spatial evidence requires a method and geometry")
            if self.horizontal_accuracy_m is None:
                raise ValueError("projected spatial evidence requires horizontal accuracy")
            validate_geojson_geometry(self.geometry_geojson)
        elif not self.reason_codes:
            raise ValueError("insufficient spatial evidence requires a reason code")
        return self


class SectorEstimate(EventModel):
    bearing_deg: float = Field(ge=0, lt=360)
    angular_uncertainty_deg: float = Field(gt=0, le=180)
    distance_min_m: float = Field(default=0, ge=0)
    distance_max_m: float | None = Field(default=None, gt=0)


class LocalizationAttempt(EventModel):
    attempt_id: Identifier
    anchor_id: Identifier | None = None
    phenomenon: PhenomenonKind | None = None
    status: LocalizationStatus
    method: LocalizationMethod | None = None
    geometry_geojson: dict[str, Any] | None = None
    sector: SectorEstimate | None = None
    horizontal_accuracy_m: float | None = None
    direction_uncertainty_deg: float | None = None
    distance_uncertainty_m: float | None = None
    reason_codes: tuple[str, ...] = Field(default=(), max_length=32)
    model_id: str | None = None
    model_revision: str | None = None
    reference_revision: str | None = None
    shadow_only: bool = False

    @model_validator(mode="after")
    def validate_attempt(self) -> LocalizationAttempt:
        if self.status == LocalizationStatus.LOCALIZED and self.geometry_geojson is None:
            raise ValueError("localized attempts require geometry")
        if self.status == LocalizationStatus.SECTOR and self.sector is None:
            raise ValueError("sector attempts require sector parameters")
        if self.status == LocalizationStatus.ABSTAINED and not self.reason_codes:
            raise ValueError("abstentions require reason codes")
        if self.geometry_geojson is not None:
            validate_geojson_geometry(self.geometry_geojson)
        return self


class FireActivityProposal(EventModel):
    proposal_id: Identifier
    attempt_id: Identifier
    phenomenon: ProposalPhenomenon
    observed_time: ObservedTime
    geometry_geojson: dict[str, Any]
    horizontal_accuracy_m: float = Field(gt=0, le=100_000)
    status: Literal["DRAFT"] = "DRAFT"
    requires_human_review: Literal[True] = True

    @model_validator(mode="after")
    def validate_proposal_geometry(self) -> FireActivityProposal:
        allowed = (
            {"LineString", "MultiLineString"}
            if self.phenomenon == PhenomenonKind.VISIBLE_FIRE_FRONT
            else {"Point"}
        )
        validate_geojson_geometry(self.geometry_geojson, allowed_types=allowed)
        return self


class EventPipelineInput(EventModel):
    schema_version: Literal["event-2.0"] = "event-2.0"
    bundle: EventCandidateBundle
    perception_anchors: tuple[PerceptionAnchor, ...] = Field(default=(), max_length=512)
    spatial_evidence: tuple[SpatialEvidence, ...] = Field(default=(), max_length=512)

    @model_validator(mode="after")
    def validate_references(self) -> EventPipelineInput:
        asset_ids = {asset.evidence_asset_id for asset in self.bundle.evidence_assets}
        anchor_ids: set[str] = set()
        for anchor in self.perception_anchors:
            if anchor.evidence_asset_id not in asset_ids:
                raise ValueError("perception anchor references an unknown private evidence asset")
            if anchor.anchor_id in anchor_ids:
                raise ValueError("perception anchor identifiers must be unique")
            anchor_ids.add(anchor.anchor_id)
        spatial_anchor_ids = [item.anchor_id for item in self.spatial_evidence]
        if len(spatial_anchor_ids) != len(set(spatial_anchor_ids)):
            raise ValueError("only one spatial result is allowed for each anchor")
        if not set(spatial_anchor_ids).issubset(anchor_ids):
            raise ValueError("spatial evidence references an unknown perception anchor")
        return self


class EventPipelineOutput(EventModel):
    schema_version: Literal["event-result-2.0"] = "event-result-2.0"
    candidate_id: Identifier
    status: PipelineStatus
    view_profile: ViewProfile | None
    perception_anchors: tuple[PerceptionAnchor, ...]
    spatial_evidence: tuple[SpatialEvidence, ...]
    localization_attempts: tuple[LocalizationAttempt, ...]
    event_proposals: tuple[FireActivityProposal, ...]
    independent_external_families: tuple[Identifier, ...]
    contradictions: tuple[tuple[Identifier, Identifier], ...]
    reason_codes: tuple[str, ...]
    requires_human_review: Literal[True] = True


class PerceptionFailure(EventModel):
    """Internal, non-spatial record for one media perception abstention."""

    evidence_asset_id: Identifier | None = None
    reason_code: Identifier
    model_id: str | None = Field(default=None, min_length=1, max_length=500)
    model_revision: str | None = Field(default=None, min_length=1, max_length=255)


