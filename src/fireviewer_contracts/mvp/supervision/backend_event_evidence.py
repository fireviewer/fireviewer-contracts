"""Integrity-checked adapters for durable EventEvidence served by the Azure backend."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from hashlib import sha256
from http import HTTPStatus
from ipaddress import ip_address
from typing import Any, Literal, Protocol, cast
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from pydantic import Field, SecretStr, field_validator, model_validator

from fireviewer_contracts.contracts import SafeIdentifierV2, Sha256HexV2, StrictModel
from fireviewer_contracts.mvp.contracts import (
    Claim,
    DetectionResultV1,
    EventEvidenceV1,
    EvidenceMedia,
    EvidenceSource,
    GeographicReference,
    GeospatialConsistencyCheck,
    LocationCandidate,
    PointAssessmentV1,
    PointEvidenceBundleV1,
    PriorFireStateReference,
    SatelliteObservation,
    UploadLocationEvidence,
    VisualObservation,
)
from fireviewer_contracts.mvp.contracts.common import TimeWindow, is_timezone_aware
from fireviewer_contracts.mvp.supervision.point_supervisor import PointSupervisorInputImage

_SNAPSHOT_SCHEMA = "event-evidence-read-1.0"
_SNAPSHOT_PATH = "/api/v1/internal/event-evidence/{candidate_id}"
_ASSET_PATH = "/api/v1/internal/event-evidence/{candidate_id}/assets/{asset_id}/content"
_KEYFRAME_PATH = "/api/v1/internal/event-evidence/{candidate_id}/keyframes/{keyframe_id}/content"
_KEYFRAME_UPLOAD_PATH = "/api/v1/internal/derived-keyframes/{candidate_id}/{keyframe_id}"
_VISUAL_EVIDENCE_PATH = "/api/v1/internal/event-evidence/{candidate_id}/visual-observations"
_RESEARCH_EVIDENCE_PATH = "/api/v1/internal/event-evidence/{candidate_id}/research-pages"
_INCIDENT_DAY_RESEARCH_PATH = "/api/v1/internal/incident-day-research/{analysis_id}"
_INCIDENT_DAY_RESEARCH_PAGE_PATH = "/api/v1/internal/incident-day-research/{analysis_id}/pages"
_INCIDENT_DAY_MEDIA_ANALYSIS_PATH = (
    "/api/v1/internal/incident-day-research/{analysis_id}/media-analyses"
)
_INCIDENT_DAY_SATELLITE_ANALYSIS_PATH = (
    "/api/v1/internal/incident-day-research/{analysis_id}/satellite-analyses"
)
_INCIDENT_DAY_SATELLITE_OBSERVATION_PATH = (
    "/api/v1/internal/incident-day-research/{analysis_id}/satellite-observations"
)
_INCIDENT_DAY_GEOMETRY_REVIEW_PATH = (
    "/api/v1/internal/incident-day-supervision/{analysis_id}"
)
_INCIDENT_DAY_GEOMETRY_CORRECTION_PATH = (
    "/api/v1/internal/incident-day-supervision/{analysis_id}/geometry-corrections"
)
_GEOGRAPHIC_EVIDENCE_PATH = "/api/v1/internal/event-evidence/{candidate_id}/geographic-hypotheses"
_POINT_ASSESSMENT_PATH = "/api/v1/internal/event-evidence/{candidate_id}/point-assessments"
_DEFAULT_MAX_RESPONSE_BYTES = 4 * 1024 * 1024


class BackendEventEvidenceError(RuntimeError):
    """The durable backend evidence could not be read or verified."""


class BackendEventEvidenceNotFoundError(BackendEventEvidenceError, KeyError):
    """The requested backend EventEvidence does not exist."""


class BackendViewpoint(StrictModel):
    longitude: float = Field(ge=-180, le=180, allow_inf_nan=False)
    latitude: float = Field(ge=-90, le=90, allow_inf_nan=False)
    horizontal_accuracy_m: float = Field(gt=0, le=100_000, allow_inf_nan=False)
    altitude_m: float | None = None
    label: str | None = None
    yaw_deg: float | None = Field(default=None, ge=0, lt=360)
    pitch_deg: float | None = Field(default=None, ge=-90, le=90)
    roll_deg: float | None = Field(default=None, ge=-180, le=180)
    fov_deg: float | None = Field(default=None, gt=0, lt=180)
    vertical_fov_deg: float | None = Field(default=None, gt=0, lt=180)
    image_width_px: int | None = Field(default=None, ge=1, le=100_000)
    image_height_px: int | None = Field(default=None, ge=1, le=100_000)
    origin: str = Field(min_length=1, max_length=64)


class BackendObservedTime(StrictModel):
    start_at: datetime
    end_at: datetime | None = None

    @model_validator(mode="after")
    def validate_time(self) -> BackendObservedTime:
        if not is_timezone_aware(self.start_at):
            raise ValueError("backend observed time must include a timezone")
        if self.end_at is not None:
            if not is_timezone_aware(self.end_at):
                raise ValueError("backend observed end must include a timezone")
            if self.end_at < self.start_at:
                raise ValueError("backend observed end precedes its start")
        return self


class BackendMediaCaptureContext(StrictModel):
    evidence_asset_id: SafeIdentifierV2
    viewpoint: BackendViewpoint
    captured_at: datetime

    @field_validator("captured_at")
    @classmethod
    def validate_captured_at(cls, value: datetime) -> datetime:
        if not is_timezone_aware(value):
            raise ValueError("media capture time must include a timezone")
        return value


class BackendEvidenceAsset(StrictModel):
    evidence_asset_id: SafeIdentifierV2
    kind: str = Field(pattern=r"^(image|video)$")
    declared_media_type: str = Field(min_length=1, max_length=128)
    detected_media_type: str | None = Field(default=None, max_length=128)
    size_bytes: int = Field(gt=0)
    sha256: Sha256HexV2
    capture_context: BackendMediaCaptureContext | None = None

    @model_validator(mode="after")
    def validate_capture_context(self) -> BackendEvidenceAsset:
        if (
            self.capture_context is not None
            and self.capture_context.evidence_asset_id != self.evidence_asset_id
        ):
            raise ValueError("media capture context references another evidence asset")
        return self


class BackendDerivedKeyframe(StrictModel):
    keyframe_id: SafeIdentifierV2
    parent_media_id: SafeIdentifierV2
    frame_index: int = Field(ge=0)
    timestamp_seconds: float = Field(ge=0, allow_inf_nan=False)
    captured_at: datetime | None = None
    media_type: Literal["image/png"]
    size_bytes: int = Field(gt=0, le=104_857_600)
    sha256: Sha256HexV2
    content_path: str = Field(
        pattern=(
            r"^/api/v1/internal/event-evidence/[A-Za-z0-9._:-]+/"
            r"keyframes/KF-[A-Za-z0-9._:-]+/content$"
        ),
        max_length=512,
    )

    @field_validator("captured_at")
    @classmethod
    def aware_capture_time(cls, value: datetime | None) -> datetime | None:
        if value is not None and not is_timezone_aware(value):
            raise ValueError("derived keyframe capture time must include a timezone")
        return value


class BackendConsent(StrictModel):
    analysis: bool
    retention: bool
    public_derivative: bool


class BackendProvenance(StrictModel):
    received_at: datetime
    idempotency_key: str = Field(min_length=1, max_length=128)
    trace_id: str = Field(min_length=1, max_length=128)

    @field_validator("received_at")
    @classmethod
    def validate_received_at(cls, value: datetime) -> datetime:
        if not is_timezone_aware(value):
            raise ValueError("backend provenance time must include a timezone")
        return value


class BackendEventBundle(StrictModel):
    candidate_id: SafeIdentifierV2
    incident_id: SafeIdentifierV2 | None = None
    incident_candidate_id: SafeIdentifierV2 | None = None
    viewpoint: BackendViewpoint
    observed_time: BackendObservedTime
    message: str | None = Field(default=None, max_length=10_000)
    evidence_assets: tuple[BackendEvidenceAsset, ...] = Field(default=(), max_length=20)
    consent: BackendConsent
    provenance: BackendProvenance
    external_observations: tuple[dict[str, Any], ...] = Field(default=(), max_length=2_048)


class BackendTerrainReference(StrictModel):
    terrain_id: SafeIdentifierV2
    package_id: SafeIdentifierV2
    file_id: int = Field(ge=1)
    sha256: Sha256HexV2
    size_bytes: int = Field(gt=0, le=1_073_741_824)
    media_type: Literal["application/vnd.fireviewer.terrain"]
    crs: str = Field(min_length=4, max_length=128)
    resolution_m: float = Field(gt=0, le=10_000)
    content_path: str = Field(
        pattern=r"^/api/v1/internal/event-evidence/[A-Za-z0-9._:-]+/terrain/content$",
        max_length=512,
    )


class BackendLocalizationAttempt(StrictModel):
    attempt_id: SafeIdentifierV2
    state: str = Field(min_length=1, max_length=64)
    method: str = Field(min_length=1, max_length=128)
    model_id: str | None = Field(default=None, max_length=500)
    model_revision: str | None = Field(default=None, max_length=255)
    view_profile: str = Field(min_length=1, max_length=64)
    anchor: dict[str, Any] = Field(default_factory=dict)
    geometry: dict[str, Any] | None = None
    uncertainty: dict[str, Any] | None = None
    horizontal_uncertainty_m: float | None = Field(default=None, gt=0, le=100_000)
    abstention_reason: str | None = Field(default=None, max_length=1_000)
    provenance: dict[str, Any] = Field(default_factory=dict)
    updated_at: datetime

    @field_validator("updated_at")
    @classmethod
    def validate_updated_at(cls, value: datetime) -> datetime:
        if not is_timezone_aware(value):
            raise ValueError("backend localization time must include a timezone")
        return value


class BackendPriorFireActivityEvent(StrictModel):
    event_id: SafeIdentifierV2
    state: str = Field(min_length=1, max_length=64)
    phenomenon_kind: str = Field(min_length=1, max_length=64)
    observed_start_at: datetime
    observed_end_at: datetime | None = None
    geometry: dict[str, Any]
    uncertainty: dict[str, Any]
    method: str = Field(min_length=1, max_length=128)
    version: int = Field(ge=1)
    updated_at: datetime

    @model_validator(mode="after")
    def validate_times(self) -> BackendPriorFireActivityEvent:
        values = (self.observed_start_at, self.observed_end_at, self.updated_at)
        if any(value is not None and not is_timezone_aware(value) for value in values):
            raise ValueError("backend prior fire state times must include a timezone")
        return self


class BackendPersistedVisualObservation(StrictModel):
    observation_id: SafeIdentifierV2
    media_id: SafeIdentifierV2
    observation_type: str = Field(pattern=r"^detection$")
    result_reference: SafeIdentifierV2
    confidence: float | None = Field(default=None, ge=0, le=1)
    result: DetectionResultV1

    @model_validator(mode="after")
    def validate_result_media(self) -> BackendPersistedVisualObservation:
        if self.result.media_id != self.media_id:
            raise ValueError("backend visual result references another media item")
        expected_confidence = max(
            (item.score for item in self.result.detections),
            default=None,
        )
        if self.confidence != expected_confidence:
            raise ValueError("backend visual confidence differs from its detections")
        return self


class BackendVisualEvidence(StrictModel):
    schema_version: str = Field(pattern=r"^visual-evidence-1\.0$")
    candidate_id: SafeIdentifierV2
    source_revision_sha256: Sha256HexV2
    observations: tuple[BackendPersistedVisualObservation, ...] = Field(
        default=(),
        max_length=4_096,
    )
    request_sha256: Sha256HexV2
    persisted_at: datetime

    @model_validator(mode="after")
    def validate_visual_evidence(self) -> BackendVisualEvidence:
        if not is_timezone_aware(self.persisted_at):
            raise ValueError("backend visual persistence time must include a timezone")
        for label, identifiers in (
            ("observation", [item.observation_id for item in self.observations]),
            ("result", [item.result_reference for item in self.observations]),
            ("media", [item.media_id for item in self.observations]),
        ):
            if len(identifiers) != len(set(identifiers)):
                raise ValueError(f"duplicate backend visual {label} identifier")
        return self


class BackendResearchSource(EvidenceSource):
    content_sha256: Sha256HexV2


class BackendResearchMedia(EvidenceMedia):
    source_url: str = Field(min_length=8, max_length=2_048)
    content_type: str = Field(min_length=3, max_length=128)
    size_bytes: int = Field(gt=0, le=512 * 1_024 * 1_024)


class BackendResearchDetection(StrictModel):
    detection_id: SafeIdentifierV2
    label: Literal["fire", "smoke"]
    score: float = Field(ge=0, le=1, allow_inf_nan=False)
    x_min: float = Field(ge=0, le=1, allow_inf_nan=False)
    y_min: float = Field(ge=0, le=1, allow_inf_nan=False)
    x_max: float = Field(ge=0, le=1, allow_inf_nan=False)
    y_max: float = Field(ge=0, le=1, allow_inf_nan=False)

    @model_validator(mode="after")
    def validate_box(self) -> BackendResearchDetection:
        if self.x_max <= self.x_min or self.y_max <= self.y_min:
            raise ValueError("backend research detection must have positive area")
        return self


class BackendResearchKeyframeObservation(StrictModel):
    observation_id: SafeIdentifierV2
    media_id: SafeIdentifierV2
    keyframe_id: SafeIdentifierV2
    frame_index: int = Field(ge=0)
    timestamp_seconds: float = Field(ge=0, allow_inf_nan=False)
    frame_sha256: Sha256HexV2
    detector_provider_id: str = Field(min_length=1, max_length=128)
    detector_model_id: str = Field(min_length=1, max_length=255)
    detector_model_revision: str = Field(min_length=1, max_length=255)
    detections: tuple[BackendResearchDetection, ...] = Field(default=(), max_length=64)
    abstained: bool = False
    reason_codes: tuple[str, ...] = Field(default=(), max_length=32)
    frame_binary_stored: Literal[False]


class BackendResearchTranscriptionReceipt(StrictModel):
    receipt_id: SafeIdentifierV2
    media_id: SafeIdentifierV2
    provider_id: str = Field(min_length=1, max_length=128)
    model_revision: str = Field(min_length=1, max_length=255)
    transcript_sha256: Sha256HexV2
    duration_seconds: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    language: str | None = Field(default=None, min_length=2, max_length=16)
    claim_ids: tuple[SafeIdentifierV2, ...] = Field(default=(), max_length=256)
    partial: bool = False
    transcript_stored: Literal[False]
    audio_binary_stored: Literal[False]


class BackendResearchMediaAnalysisBatch(StrictModel):
    batch_id: SafeIdentifierV2
    media_id: SafeIdentifierV2
    media_sha256: Sha256HexV2
    processor_id: str = Field(min_length=1, max_length=128)
    processor_revision: str = Field(min_length=1, max_length=255)
    analyzed_at: datetime
    outcome: Literal["success", "partial", "failed"]
    request_sha256: Sha256HexV2
    claim_count: int = Field(ge=0, le=256)
    keyframe_observation_count: int = Field(ge=0, le=256)
    transcription_receipt_count: int = Field(ge=0, le=8)
    journal_entry_count: int = Field(ge=1, le=256)
    raw_content_stored: Literal[False]

    @model_validator(mode="after")
    def validate_time(self) -> BackendResearchMediaAnalysisBatch:
        if not is_timezone_aware(self.analyzed_at):
            raise ValueError("backend media analysis time must include a timezone")
        return self


class BackendResearchPage(StrictModel):
    page_id: SafeIdentifierV2
    page_number: int = Field(ge=1, le=10_000)
    wave_number: int = Field(default=1, ge=1, le=16)
    wave_focus: tuple[SafeIdentifierV2, ...] = Field(
        default=("general",),
        min_length=1,
        max_length=32,
    )
    cursor: str | None = Field(default=None, max_length=2_048)
    next_cursor: str | None = Field(default=None, max_length=2_048)
    completed: bool
    media_ticket_limit: int = Field(default=2_048, ge=1, le=2_048)
    safety_limit_reached: bool = False
    converged: bool = False
    zero_yield_wave_streak: int = Field(default=0, ge=0, le=100)
    coverage_ready: bool = False
    request_sha256: Sha256HexV2
    duplicate_counts: tuple[int, int, int]
    persisted_at: datetime

    @model_validator(mode="after")
    def validate_page(self) -> BackendResearchPage:
        if any(value < 0 for value in self.duplicate_counts):
            raise ValueError("backend research duplicate counts cannot be negative")
        if not is_timezone_aware(self.persisted_at):
            raise ValueError("backend research persistence time must include a timezone")
        return self


class BackendIncidentDaySourcePolicy(StrictModel):
    publisher: str = Field(min_length=1, max_length=500)
    source_type: Literal[
        "official",
        "press",
        "social",
        "witness",
        "satellite",
        "panoramax",
        "metadata",
        "other",
    ]
    independence_weight: float = Field(ge=0, le=1, allow_inf_nan=False)
    claim_types: tuple[SafeIdentifierV2, ...] = Field(min_length=1, max_length=32)


class BackendIncidentDaySatelliteBand(StrictModel):
    canonical_band: Literal["B02", "B03", "B04", "B8A", "B11", "B12"]
    asset_name: SafeIdentifierV2
    source_checksum: str = Field(pattern=r"^(?:1220|1620)[0-9a-f]{64}$")
    content_sha256: Sha256HexV2
    size_bytes: int = Field(gt=0, le=2_147_483_648)
    media_type: Literal["image/jp2"]
    gsd_m: Literal[20]
    proj_code: str = Field(min_length=3, max_length=128)
    proj_shape: tuple[int, int]
    proj_transform: tuple[float, float, float, float, float, float]
    content_path: str = Field(
        pattern=(
            r"^/api/v1/internal/satellite-materializations/"
            r"[A-Za-z0-9._:-]{3,96}/bands/(?:B02|B03|B04|B8A|B11|B12)/content$"
        )
    )


class BackendIncidentDaySatelliteArtifact(StrictModel):
    artifact_revision_id: SafeIdentifierV2
    provider_key: SafeIdentifierV2
    collection_key: SafeIdentifierV2
    semantic_role: str = Field(min_length=3, max_length=64)
    external_product_id: str = Field(min_length=1, max_length=512)
    source_url: str = Field(min_length=8, max_length=2_048)
    content_hash: Sha256HexV2
    acquisition_start_at: datetime | None = None
    acquisition_end_at: datetime | None = None
    native_crs: str | None = Field(default=None, min_length=3, max_length=128)
    footprint_geojson: dict[str, Any] | None = None
    resolution_m: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    quality_flags: dict[str, Any] = Field(default_factory=dict)
    license: str = Field(min_length=1, max_length=1_000)
    attribution: str = Field(min_length=1, max_length=1_000)
    materialization_state: str = Field(min_length=3, max_length=64)
    materialization_bundle_id: SafeIdentifierV2 | None = None
    materialization_manifest_sha256: Sha256HexV2 | None = None
    prithvi_bands: tuple[BackendIncidentDaySatelliteBand, ...] = Field(
        default=(),
        max_length=6,
    )

    @model_validator(mode="after")
    def validate_materialization(self) -> BackendIncidentDaySatelliteArtifact:
        if self.materialization_state == "materialized":
            if (
                self.materialization_bundle_id is None
                or self.materialization_manifest_sha256 is None
                or tuple(item.canonical_band for item in self.prithvi_bands)
                != ("B02", "B03", "B04", "B8A", "B11", "B12")
            ):
                raise ValueError("materialized satellite artifact is incomplete")
        elif (
            self.materialization_bundle_id is not None
            or self.materialization_manifest_sha256 is not None
            or self.prithvi_bands
        ):
            raise ValueError("unmaterialized satellite artifact exposes band receipts")
        return self


class BackendIncidentDaySpatialObservation(StrictModel):
    claim_id: SafeIdentifierV2
    artifact_revision_id: SafeIdentifierV2
    provider_key: SafeIdentifierV2
    semantic_role: Literal[
        "raw_earth_observation",
        "sensor_detection",
        "interpreted_observation",
    ]
    source_url: str = Field(min_length=8, max_length=2_048)
    attribution: str = Field(min_length=1, max_length=1_000)
    retrieved_at: datetime
    observed_at: datetime
    assertion_kind: str = Field(min_length=3, max_length=128)
    geometry_geojson: dict[str, Any]
    coverage_geojson: dict[str, Any] | None = None
    confidence: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)
    horizontal_accuracy_m: float | None = Field(
        default=None,
        gt=0,
        le=100_000,
        allow_inf_nan=False,
    )
    resolution_m: float | None = Field(
        default=None,
        gt=0,
        le=100_000,
        allow_inf_nan=False,
    )
    processor: str | None = Field(default=None, min_length=3, max_length=128)
    source_dataset: str | None = Field(default=None, min_length=1, max_length=128)
    satellite: str | None = Field(default=None, min_length=1, max_length=128)
    instrument: str | None = Field(default=None, min_length=1, max_length=128)
    metrics: dict[str, float | int] = Field(default_factory=dict, max_length=64)
    independent_family_key: str = Field(min_length=1, max_length=255)

    @model_validator(mode="after")
    def validate_times(self) -> BackendIncidentDaySpatialObservation:
        if not is_timezone_aware(self.retrieved_at) or not is_timezone_aware(self.observed_at):
            raise ValueError("satellite observation timestamps must include a timezone")
        return self


class BackendIncidentDayCoverage(StrictModel):
    queries_exhausted: bool
    safety_limit_reached: bool
    converged: bool
    source_count: int = Field(ge=0)
    official_source_count: int = Field(ge=0)
    independent_evidence_family_count: int = Field(ge=0)
    claim_count: int = Field(ge=0)
    image_count: int = Field(ge=0)
    video_count: int = Field(ge=0)
    audio_count: int = Field(ge=0)
    media_analysis_required_count: int = Field(ge=0)
    media_analysis_completed_count: int = Field(ge=0)
    media_analysis_failed_count: int = Field(ge=0)
    satellite_artifact_count: int = Field(ge=0)
    materialized_satellite_count: int = Field(ge=0)
    satellite_analysis_required_count: int = Field(ge=0)
    satellite_analysis_completed_count: int = Field(ge=0)
    spatial_observation_count: int = Field(ge=0)
    time_qualified_observation_count: int = Field(ge=0)
    expected_lifecycle_phases: tuple[str, ...] = Field(max_length=16)
    covered_lifecycle_phases: tuple[str, ...] = Field(max_length=16)
    missing_dimensions: tuple[str, ...] = Field(max_length=64)
    documentary_ready: bool
    spatial_ready: bool
    satellite_analysis_ready: bool
    media_analysis_ready: bool
    coverage_ready: bool


class BackendIncidentDayResearchContext(StrictModel):
    schema_version: Literal["incident-day-research-read-1.0"]
    analysis_id: SafeIdentifierV2
    fire_id: SafeIdentifierV2
    episode_id: SafeIdentifierV2
    incident_name: str = Field(min_length=2, max_length=255)
    incident_reference: tuple[float, float]
    incident_bbox: tuple[float, float, float, float]
    local_date: date
    timezone: str = Field(min_length=3, max_length=64)
    window_start_at: datetime
    window_end_at: datetime
    episode_started_at: datetime
    episode_last_observed_at: datetime
    episode_ended_at: datetime | None = None
    episode_status: str = Field(min_length=1, max_length=64)
    source_registry_version: str = Field(min_length=3, max_length=64)
    source_policies: dict[str, BackendIncidentDaySourcePolicy] = Field(
        min_length=1,
        max_length=200,
    )
    search_templates: dict[str, str] = Field(min_length=1, max_length=10)
    research_evidence: dict[str, Any] | None = None
    satellite_artifacts: tuple[BackendIncidentDaySatelliteArtifact, ...] = Field(
        default=(),
        max_length=512,
    )
    spatial_observations: tuple[BackendIncidentDaySpatialObservation, ...] = Field(
        default=(),
        max_length=2_048,
    )
    coverage: BackendIncidentDayCoverage
    source_sha256: Sha256HexV2

    @model_validator(mode="after")
    def validate_context(self) -> BackendIncidentDayResearchContext:
        longitude, latitude = self.incident_reference
        if not -180 <= longitude <= 180 or not -90 <= latitude <= 90:
            raise ValueError("incident reference must be WGS84")
        min_lon, min_lat, max_lon, max_lat = self.incident_bbox
        if not (-180 <= min_lon < max_lon <= 180 and -90 <= min_lat < max_lat <= 90):
            raise ValueError("incident bbox must be ordered WGS84")
        times = (
            self.window_start_at,
            self.window_end_at,
            self.episode_started_at,
            self.episode_last_observed_at,
            self.episode_ended_at,
        )
        if any(value is not None and not is_timezone_aware(value) for value in times):
            raise ValueError("incident-day timestamps must include a timezone")
        if self.window_end_at <= self.window_start_at:
            raise ValueError("incident-day analysis window is invalid")
        if set(self.source_policies) & set(self.search_templates):
            raise ValueError("search providers and evidence domains must be disjoint")
        return self


class BackendGeometryCorrectionProposal(StrictModel):
    schema_name: Literal["fireviewer.geometry-correction-proposal.v1"] = Field(
        default="fireviewer.geometry-correction-proposal.v1",
        alias="schema",
    )
    correction_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$")
    incident_id: SafeIdentifierV2
    local_date: date
    source_perimeter_sha256: Sha256HexV2
    competing_geometry_geojson: dict[str, Any]
    reason_codes: tuple[SafeIdentifierV2, ...] = Field(min_length=1, max_length=128)
    evidence_refs: tuple[SafeIdentifierV2, ...] = Field(default=(), max_length=512)
    relationship: Literal["competes_with_source"] = "competes_with_source"
    state: Literal["proposed"] = "proposed"
    source_mutation_allowed: Literal[False] = False


class BackendIncidentDayGeometryReviewContext(StrictModel):
    schema_name: Literal["fireviewer.incident-day-geometry-review-context.v1"] = Field(
        default="fireviewer.incident-day-geometry-review-context.v1",
        alias="schema",
    )
    analysis_id: SafeIdentifierV2
    fire_id: SafeIdentifierV2
    episode_id: SafeIdentifierV2
    local_date: date
    deterministic_perimeter: dict[str, Any]
    source_perimeter_sha256: Sha256HexV2
    candidate_geometries: tuple[dict[str, Any], ...] = Field(default=(), max_length=2_048)
    evidence_summary: dict[str, Any]
    prior_daily_states: tuple[dict[str, Any], ...] = Field(default=(), max_length=366)
    published_reference_accessed: Literal[False]
    geometry_mutation_allowed: Literal[False]
    source_sha256: Sha256HexV2


class BackendIncidentDayGeometryReviewRequest(StrictModel):
    schema_name: Literal["fireviewer.incident-day-geometry-review.v1"] = Field(
        default="fireviewer.incident-day-geometry-review.v1",
        alias="schema",
    )
    review_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$")
    analysis_id: SafeIdentifierV2
    source_review_sha256: Sha256HexV2
    source_perimeter_sha256: Sha256HexV2
    verdict: Literal["accept", "propose", "abstain"]
    model_confidence: float = Field(ge=0, le=1, allow_inf_nan=False)
    reason_codes: tuple[SafeIdentifierV2, ...] = Field(min_length=1, max_length=128)
    evidence_refs: tuple[SafeIdentifierV2, ...] = Field(default=(), max_length=512)
    supervisor_mode: Literal["managed_vl", "simulated"]
    provider_run: dict[str, Any]
    prompt_revision: str = Field(min_length=1, max_length=255)
    proposal: BackendGeometryCorrectionProposal | None = None
    raw_content_stored: Literal[False] = False
    source_mutation_allowed: Literal[False] = False


class BackendIncidentDayGeometryReviewReceipt(StrictModel):
    schema_name: Literal["fireviewer.incident-day-geometry-review-receipt.v1"] = Field(
        default="fireviewer.incident-day-geometry-review-receipt.v1",
        alias="schema",
    )
    analysis_id: SafeIdentifierV2
    review_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$")
    verdict: Literal["accept", "propose", "abstain"]
    proposal_state: Literal["persisted_for_human_review"] | None = None
    supervisor_mode: Literal["managed_vl", "simulated"]
    source_perimeter_unchanged: Literal[True]
    replayed: bool
    receipt_sha256: Sha256HexV2


class BackendResearchRetentionPolicy(StrictModel):
    raw_scraped_content_stored: Literal[False]
    articles_stored: Literal[False]
    transcripts_stored: Literal[False]
    public_media_binaries_stored: Literal[False]
    satellite_binaries_allowed: Literal[True]
    perimeter_tiles_allowed: Literal[True]
    user_media_requires_republication_consent: Literal[True]


class BackendResearchJournalEntry(StrictModel):
    entry_id: SafeIdentifierV2
    stage: str = Field(min_length=1, max_length=64)
    outcome: str = Field(min_length=1, max_length=64)
    error_code: str | None = Field(default=None, min_length=1, max_length=128)
    detail: str = Field(min_length=1, max_length=1_000)
    source_url: str | None = Field(default=None, min_length=8, max_length=2_048)
    occurred_at: datetime
    retryable: bool
    provider_id: str | None = Field(default=None, min_length=1, max_length=128)
    model_revision: str | None = Field(default=None, min_length=1, max_length=255)
    prompt_revision: str | None = Field(default=None, min_length=1, max_length=255)

    @model_validator(mode="after")
    def validate_time(self) -> BackendResearchJournalEntry:
        if not is_timezone_aware(self.occurred_at):
            raise ValueError("backend research journal time must include a timezone")
        return self


class BackendSatelliteAnalysisBatch(StrictModel):
    request_id: SafeIdentifierV2
    artifact_revision_id: SafeIdentifierV2
    materialization_bundle_id: SafeIdentifierV2
    materialization_manifest_sha256: Sha256HexV2
    prithvi_input_sha256: Sha256HexV2
    gpu_request_sha256: Sha256HexV2
    sink_request_sha256: Sha256HexV2
    status: Literal["completed", "abstained"]
    model_id: str = Field(min_length=1, max_length=500)
    model_revision: str = Field(pattern=r"^[0-9a-f]{40,64}$")
    reason_codes: tuple[SafeIdentifierV2, ...] = Field(default=(), max_length=16)
    claim_ids: tuple[SafeIdentifierV2, ...] = Field(default=(), max_length=16)
    raw_content_stored: Literal[False]
    persisted_at: datetime

    @field_validator("persisted_at")
    @classmethod
    def validate_persisted_at(cls, value: datetime) -> datetime:
        if not is_timezone_aware(value):
            raise ValueError("satellite analysis persistence time must include a timezone")
        return value


class BackendSatelliteObservationBatch(StrictModel):
    result_id: SafeIdentifierV2
    processing_context_sha256: Sha256HexV2 | None = None
    artifact_revision_id: SafeIdentifierV2
    reference_artifact_revision_id: SafeIdentifierV2 | None = None
    sink_request_sha256: Sha256HexV2
    status: Literal["completed", "no_observation", "unavailable"]
    unavailable_reason: Literal["cdse_openeo_not_authorized"] | None = None
    processor: Literal[
        "clms_burned_area_daily_v1",
        "sentinel1_vvvh_change_v1",
        "sentinel2_nbr_change_v1",
        "sentinel3_frp_v1",
    ]
    processor_revision: str = Field(min_length=8, max_length=255)
    claim_ids: tuple[SafeIdentifierV2, ...] = Field(default=(), max_length=2_048)
    observed_at: datetime | None = None
    valid_coverage_geojson: dict[str, Any] | None = None
    coverage_metrics: dict[str, float | int] = Field(default_factory=dict, max_length=16)
    asset_receipt_sha256: Sha256HexV2
    raw_content_stored: Literal[False]
    persisted_at: datetime

    @field_validator("observed_at", "persisted_at")
    @classmethod
    def validate_persisted_at(cls, value: datetime | None) -> datetime | None:
        if value is not None and not is_timezone_aware(value):
            raise ValueError("satellite observation persistence time must include a timezone")
        return value


class BackendResearchEvidence(StrictModel):
    schema_version: str = Field(pattern=r"^research-evidence-1\.[01]$")
    candidate_id: SafeIdentifierV2
    plan_id: SafeIdentifierV2
    plan_revision: Sha256HexV2
    wave_number: int = Field(default=1, ge=1, le=16)
    wave_focus: tuple[SafeIdentifierV2, ...] = Field(
        default=("general",),
        min_length=1,
        max_length=32,
    )
    pages: tuple[BackendResearchPage, ...] = Field(default=(), max_length=10_000)
    sources: tuple[BackendResearchSource, ...] = Field(default=(), max_length=512)
    claims: tuple[Claim, ...] = Field(default=(), max_length=2_048)
    media: tuple[BackendResearchMedia, ...] = Field(default=(), max_length=2_048)
    keyframe_observations: tuple[BackendResearchKeyframeObservation, ...] = Field(
        default=(),
        max_length=8_192,
    )
    transcription_receipts: tuple[BackendResearchTranscriptionReceipt, ...] = Field(
        default=(),
        max_length=2_048,
    )
    media_analysis_batches: tuple[BackendResearchMediaAnalysisBatch, ...] = Field(
        default=(),
        max_length=10_000,
    )
    satellite_analysis_batches: tuple[BackendSatelliteAnalysisBatch, ...] = Field(
        default=(),
        max_length=512,
    )
    satellite_observation_batches: tuple[BackendSatelliteObservationBatch, ...] = Field(
        default=(),
        max_length=2_048,
    )
    journal_entries: tuple[BackendResearchJournalEntry, ...] = Field(
        default=(),
        max_length=10_000,
    )
    retention_policy: BackendResearchRetentionPolicy
    completed: bool
    media_ticket_limit: int = Field(default=2_048, ge=1, le=2_048)
    safety_limit_reached: bool = False
    converged: bool = False
    zero_yield_wave_streak: int = Field(default=0, ge=0, le=100)
    coverage_ready: bool = False
    next_cursor: str | None = Field(default=None, max_length=2_048)

    @model_validator(mode="after")
    def validate_research(self) -> BackendResearchEvidence:
        if self.completed and self.next_cursor is not None:
            raise ValueError("completed backend research cannot expose a next cursor")
        if self.converged and not self.completed:
            raise ValueError("backend research convergence is inconsistent")
        if self.coverage_ready and (not self.completed or not self.converged):
            raise ValueError("backend research coverage is inconsistent")
        page_ids = [item.page_id for item in self.pages]
        if len(page_ids) != len(set(page_ids)):
            raise ValueError("duplicate backend research page identifier")
        source_ids = {item.source_id for item in self.sources}
        if len(source_ids) != len(self.sources):
            raise ValueError("duplicate backend research source identifier")
        if any(item.source_id not in source_ids for item in self.claims) or any(
            item.source_id not in source_ids for item in self.media
        ):
            raise ValueError("backend research evidence references an unknown source")
        media_by_id = {item.media_id: item for item in self.media}
        if len(media_by_id) != len(self.media):
            raise ValueError("duplicate backend research media identifier")
        if any(not set(item.evidence_media_ids).issubset(media_by_id) for item in self.claims):
            raise ValueError("backend research claim references unknown media")
        if any(
            item.media_id not in media_by_id or media_by_id[item.media_id].kind != "video"
            for item in self.keyframe_observations
        ):
            raise ValueError("backend keyframe observation references an unknown video")
        if any(
            item.media_id not in media_by_id
            or media_by_id[item.media_id].kind not in {"video", "audio"}
            for item in self.transcription_receipts
        ):
            raise ValueError("backend transcription references an unknown video")
        batch_ids = [item.batch_id for item in self.media_analysis_batches]
        if len(batch_ids) != len(set(batch_ids)):
            raise ValueError("duplicate backend media-analysis batch identifier")
        if any(
            item.media_id not in media_by_id
            or media_by_id[item.media_id].sha256 != item.media_sha256
            for item in self.media_analysis_batches
        ):
            raise ValueError("backend media-analysis batch references unknown media")
        satellite_request_ids = [item.request_id for item in self.satellite_analysis_batches]
        if len(satellite_request_ids) != len(set(satellite_request_ids)):
            raise ValueError("duplicate backend satellite-analysis request identifier")
        satellite_result_ids = [item.result_id for item in self.satellite_observation_batches]
        if len(satellite_result_ids) != len(set(satellite_result_ids)):
            raise ValueError("duplicate backend satellite-observation result identifier")
        claim_ids = {item.claim_id for item in self.claims}
        if any(not set(item.claim_ids).issubset(claim_ids) for item in self.transcription_receipts):
            raise ValueError("backend transcription references an unknown claim")
        for label, identifiers in (
            (
                "keyframe observation",
                [item.observation_id for item in self.keyframe_observations],
            ),
            (
                "transcription receipt",
                [item.receipt_id for item in self.transcription_receipts],
            ),
        ):
            if len(identifiers) != len(set(identifiers)):
                raise ValueError(f"duplicate backend research {label} identifier")
        journal_ids = [item.entry_id for item in self.journal_entries]
        if len(journal_ids) != len(set(journal_ids)):
            raise ValueError("duplicate backend research journal identifier")
        return self


class BackendEventEvidenceSnapshot(StrictModel):
    schema_version: str = Field(pattern=r"^event-evidence-read-1\.0$")
    candidate_id: SafeIdentifierV2
    candidate_revision: int = Field(ge=1)
    candidate_state: str = Field(min_length=1, max_length=64)
    updated_at: datetime
    bundle: BackendEventBundle
    visual_evidence: BackendVisualEvidence | None = None
    research_evidence: BackendResearchEvidence | None = None
    derived_keyframes: tuple[BackendDerivedKeyframe, ...] = Field(default=(), max_length=480)
    localization_attempts: tuple[BackendLocalizationAttempt, ...] = Field(
        default=(),
        max_length=512,
    )
    prior_fire_activity_events: tuple[BackendPriorFireActivityEvent, ...] = Field(
        default=(),
        max_length=512,
    )
    terrain_reference: BackendTerrainReference | None = None
    analysis_result_sha256: Sha256HexV2 | None = None
    source_sha256: Sha256HexV2

    @model_validator(mode="after")
    def validate_snapshot(self) -> BackendEventEvidenceSnapshot:
        if self.schema_version != _SNAPSHOT_SCHEMA:
            raise ValueError("unsupported backend EventEvidence snapshot schema")
        if not is_timezone_aware(self.updated_at):
            raise ValueError("backend snapshot time must include a timezone")
        if self.bundle.candidate_id != self.candidate_id:
            raise ValueError("backend snapshot candidate mismatch")
        if (
            self.visual_evidence is not None
            and self.visual_evidence.candidate_id != self.candidate_id
        ):
            raise ValueError("backend visual evidence candidate mismatch")
        if (
            self.research_evidence is not None
            and self.research_evidence.candidate_id != self.candidate_id
        ):
            raise ValueError("backend research evidence candidate mismatch")
        if not self.bundle.consent.analysis or not self.bundle.consent.retention:
            raise ValueError("backend snapshot lacks analysis or retention consent")
        asset_ids = [item.evidence_asset_id for item in self.bundle.evidence_assets]
        if len(asset_ids) != len(set(asset_ids)):
            raise ValueError("duplicate backend EventEvidence asset")
        keyframe_ids = [item.keyframe_id for item in self.derived_keyframes]
        if len(keyframe_ids) != len(set(keyframe_ids)):
            raise ValueError("duplicate backend derived keyframe")
        if any(item.parent_media_id not in asset_ids for item in self.derived_keyframes):
            raise ValueError("derived keyframe references an unknown parent media")
        attempt_ids = [item.attempt_id for item in self.localization_attempts]
        if len(attempt_ids) != len(set(attempt_ids)):
            raise ValueError("duplicate backend localization attempt")
        return self


class AzureBackendEventEvidenceConfig(StrictModel):
    base_url: str = Field(min_length=8, max_length=2_048)
    bearer_token: SecretStr = Field(min_length=32, max_length=4_096)
    timeout_seconds: float = Field(default=10, ge=1, le=30)
    max_response_bytes: int = Field(
        default=_DEFAULT_MAX_RESPONSE_BYTES,
        ge=64 * 1024,
        le=20 * 1024 * 1024,
    )

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, value: str) -> str:
        normalized = value.rstrip("/")
        parsed = urlsplit(normalized)
        if (
            parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or parsed.path not in {"", "/"}
            or parsed.hostname is None
        ):
            raise ValueError("backend base URL must be an origin without credentials or query")
        if parsed.scheme == "https":
            return normalized
        try:
            loopback = parsed.scheme == "http" and ip_address(parsed.hostname).is_loopback
        except ValueError:
            loopback = parsed.scheme == "http" and parsed.hostname == "localhost"
        if not loopback:
            raise ValueError("remote backend EventEvidence access requires HTTPS")
        return normalized


@dataclass(frozen=True, slots=True)
class BackendJsonResponse:
    payload: dict[str, Any]
    headers: Mapping[str, str]


@dataclass(frozen=True, slots=True)
class BackendBinaryResponse:
    content: bytes
    content_type: str
    headers: Mapping[str, str]


class BackendEventEvidenceTransport(Protocol):
    def get_json(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        timeout_seconds: float,
        max_response_bytes: int,
    ) -> BackendJsonResponse: ...


class BackendEvidenceMediaTransport(Protocol):
    def get_bytes(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        timeout_seconds: float,
        max_response_bytes: int,
    ) -> BackendBinaryResponse: ...


class BackendVisualEvidenceTransport(Protocol):
    def post_json(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        payload: Mapping[str, Any],
        timeout_seconds: float,
        max_response_bytes: int,
    ) -> BackendJsonResponse: ...


class BackendIncidentDayGeometryReviewTransport(
    BackendEventEvidenceTransport,
    BackendVisualEvidenceTransport,
    Protocol,
):
    pass


class BackendKeyframeEvidenceTransport(Protocol):
    def put_bytes(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        content: bytes,
        timeout_seconds: float,
        max_response_bytes: int,
    ) -> BackendJsonResponse: ...


class _NoRedirectHandler(HTTPRedirectHandler):
    def redirect_request(
        self,
        req: Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> None:
        return None


class UrllibBackendEventEvidenceTransport:
    """Small dependency-free HTTPS transport with bounded, no-redirect reads."""

    def get_json(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        timeout_seconds: float,
        max_response_bytes: int,
    ) -> BackendJsonResponse:
        request = Request(url, headers=dict(headers), method="GET")  # noqa: S310
        try:
            with build_opener(_NoRedirectHandler()).open(
                request,
                timeout=timeout_seconds,
            ) as response:
                content_type = response.headers.get_content_type()
                if content_type != "application/json":
                    raise BackendEventEvidenceError("backend response is not JSON")
                raw_length = response.headers.get("content-length")
                if raw_length is not None and int(raw_length) > max_response_bytes:
                    raise BackendEventEvidenceError("backend response exceeds the size limit")
                body = response.read(max_response_bytes + 1)
                if len(body) > max_response_bytes:
                    raise BackendEventEvidenceError("backend response exceeds the size limit")
                response_headers = {
                    key.casefold(): value for key, value in response.headers.items()
                }
        except HTTPError as exc:
            if exc.code == HTTPStatus.NOT_FOUND:
                raise BackendEventEvidenceNotFoundError(url) from exc
            raise BackendEventEvidenceError(f"backend returned HTTP {exc.code}") from exc
        except (OSError, URLError, ValueError) as exc:
            raise BackendEventEvidenceError("backend EventEvidence request failed") from exc
        try:
            payload = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise BackendEventEvidenceError("backend returned invalid JSON") from exc
        if not isinstance(payload, dict):
            raise BackendEventEvidenceError("backend response must be a JSON object")
        return BackendJsonResponse(payload=payload, headers=response_headers)

    def get_bytes(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        timeout_seconds: float,
        max_response_bytes: int,
    ) -> BackendBinaryResponse:
        request = Request(url, headers=dict(headers), method="GET")  # noqa: S310
        try:
            with build_opener(_NoRedirectHandler()).open(
                request,
                timeout=timeout_seconds,
            ) as response:
                raw_length = response.headers.get("content-length")
                if raw_length is not None and int(raw_length) > max_response_bytes:
                    raise BackendEventEvidenceError("backend media exceeds the size limit")
                content = response.read(max_response_bytes + 1)
                if len(content) > max_response_bytes:
                    raise BackendEventEvidenceError("backend media exceeds the size limit")
                response_headers = {
                    key.casefold(): value for key, value in response.headers.items()
                }
                content_type = response.headers.get_content_type()
        except HTTPError as exc:
            if exc.code == HTTPStatus.NOT_FOUND:
                raise BackendEventEvidenceNotFoundError(url) from exc
            raise BackendEventEvidenceError(f"backend returned HTTP {exc.code}") from exc
        except (OSError, URLError, ValueError) as exc:
            raise BackendEventEvidenceError("backend media request failed") from exc
        return BackendBinaryResponse(
            content=content,
            content_type=content_type,
            headers=response_headers,
        )

    def post_json(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        payload: Mapping[str, Any],
        timeout_seconds: float,
        max_response_bytes: int,
    ) -> BackendJsonResponse:
        request_headers = {**headers, "Content-Type": "application/json"}
        request = Request(  # noqa: S310
            url,
            data=json.dumps(
                payload,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8"),
            headers=request_headers,
            method="POST",
        )
        try:
            with build_opener(_NoRedirectHandler()).open(
                request,
                timeout=timeout_seconds,
            ) as response:
                if response.headers.get_content_type() != "application/json":
                    raise BackendEventEvidenceError("backend response is not JSON")
                raw_length = response.headers.get("content-length")
                if raw_length is not None and int(raw_length) > max_response_bytes:
                    raise BackendEventEvidenceError("backend response exceeds the size limit")
                body = response.read(max_response_bytes + 1)
                if len(body) > max_response_bytes:
                    raise BackendEventEvidenceError("backend response exceeds the size limit")
                response_headers = {
                    key.casefold(): value for key, value in response.headers.items()
                }
        except HTTPError as exc:
            raise BackendEventEvidenceError(f"backend returned HTTP {exc.code}") from exc
        except (OSError, URLError, ValueError) as exc:
            raise BackendEventEvidenceError("backend evidence write request failed") from exc
        try:
            decoded = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise BackendEventEvidenceError("backend returned invalid JSON") from exc
        if not isinstance(decoded, dict):
            raise BackendEventEvidenceError("backend response must be a JSON object")
        return BackendJsonResponse(payload=decoded, headers=response_headers)

    def put_bytes(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        content: bytes,
        timeout_seconds: float,
        max_response_bytes: int,
    ) -> BackendJsonResponse:
        request = Request(  # noqa: S310
            url,
            data=content,
            headers={**headers, "Content-Type": "image/png"},
            method="PUT",
        )
        try:
            with build_opener(_NoRedirectHandler()).open(
                request,
                timeout=timeout_seconds,
            ) as response:
                if response.headers.get_content_type() != "application/json":
                    raise BackendEventEvidenceError("backend response is not JSON")
                body = response.read(max_response_bytes + 1)
                if len(body) > max_response_bytes:
                    raise BackendEventEvidenceError("backend response exceeds the size limit")
                response_headers = {
                    key.casefold(): value for key, value in response.headers.items()
                }
        except HTTPError as exc:
            raise BackendEventEvidenceError(f"backend returned HTTP {exc.code}") from exc
        except (OSError, URLError, ValueError) as exc:
            raise BackendEventEvidenceError("backend keyframe write request failed") from exc
        try:
            decoded = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise BackendEventEvidenceError("backend returned invalid JSON") from exc
        if not isinstance(decoded, dict):
            raise BackendEventEvidenceError("backend response must be a JSON object")
        return BackendJsonResponse(payload=decoded, headers=response_headers)


@dataclass(frozen=True, slots=True)
class DurableResearchProgress:
    plan_id: str
    plan_revision: str
    wave_number: int
    wave_focus: tuple[str, ...]
    page_count: int
    completed: bool
    media_ticket_limit: int
    safety_limit_reached: bool
    converged: bool
    zero_yield_wave_streak: int
    coverage_ready: bool
    next_cursor: str | None


@dataclass(frozen=True, slots=True)
class DurableEventEvidence:
    event: EventEvidenceV1
    media_locations: tuple[BackendEvidenceMediaLocation, ...]
    vision_artifacts: tuple[DetectionResultV1, ...]
    upload_locations: tuple[UploadLocationEvidence, ...]
    prior_fire_states: tuple[PriorFireStateReference, ...]
    geospatial_checks: tuple[GeospatialConsistencyCheck, ...]
    geographic_references: tuple[GeographicReference, ...]
    source_revision_sha256: str
    terrain_reference: DurableTerrainReference | None = None
    research_progress: DurableResearchProgress | None = None
    research_journal: tuple[BackendResearchJournalEntry, ...] = ()
    incident_id: str | None = None
    viewpoint_label: str | None = None
    research_source_policies: dict[str, dict[str, Any]] | None = None
    research_search_templates: dict[str, str] | None = None
    research_target_kind: Literal["event_candidate", "incident_day"] = "event_candidate"
    incident_day_coverage: BackendIncidentDayCoverage | None = None
    satellite_artifact_tickets: tuple[BackendIncidentDaySatelliteArtifact, ...] = ()
    spatial_observation_tickets: tuple[BackendIncidentDaySpatialObservation, ...] = ()
    research_sources: tuple[BackendResearchSource, ...] = ()
    research_media_tickets: tuple[BackendResearchMedia, ...] = ()
    research_media_analysis_batches: tuple[BackendResearchMediaAnalysisBatch, ...] = ()
    satellite_analysis_batches: tuple[BackendSatelliteAnalysisBatch, ...] = ()
    satellite_observation_batches: tuple[BackendSatelliteObservationBatch, ...] = ()
    incident_day_episode_id: str | None = None
    incident_day_local_date: date | None = None
    incident_day_timezone: str | None = None
    incident_day_bbox: tuple[float, float, float, float] | None = None

    def checks_for(self, candidate_id: str) -> tuple[GeospatialConsistencyCheck, ...]:
        return tuple(
            check for check in self.geospatial_checks if candidate_id in check.evidence_ids
        )


class EventEvidenceRepository(Protocol):
    def read(self, event_id: str) -> DurableEventEvidence: ...


class DurableTerrainReference(StrictModel):
    terrain_id: SafeIdentifierV2
    package_id: SafeIdentifierV2
    sha256: Sha256HexV2
    size_bytes: int = Field(gt=0, le=1_073_741_824)
    media_type: Literal["application/vnd.fireviewer.terrain"]
    crs: str = Field(min_length=4, max_length=128)
    resolution_m: float = Field(gt=0, le=10_000)
    content_url: str = Field(min_length=8, max_length=2_048)


def _canonical_sha256(payload: object) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return sha256(encoded).hexdigest()


def _stable_id(prefix: str, value: str) -> str:
    return f"{prefix}-{sha256(value.encode('utf-8')).hexdigest()[:24]}"


def _point_coordinates(geometry: dict[str, Any] | None) -> tuple[float, float] | None:
    if geometry is None or geometry.get("type") != "Point":
        return None
    coordinates = geometry.get("coordinates")
    if (
        not isinstance(coordinates, list)
        or len(coordinates) < 2
        or not all(isinstance(value, (int, float)) for value in coordinates[:2])
    ):
        return None
    longitude, latitude = float(coordinates[0]), float(coordinates[1])
    if not -180 <= longitude <= 180 or not -90 <= latitude <= 90:
        return None
    return longitude, latitude


def _anchor_perception(attempt: BackendLocalizationAttempt) -> dict[str, Any] | None:
    perception = attempt.anchor.get("perception")
    return perception if isinstance(perception, dict) else None


class BackendEvidenceMediaLocation(StrictModel):
    media_id: SafeIdentifierV2
    working_file_url: str = Field(min_length=8, max_length=2_048)


class BackendVisualEvidenceReceipt(StrictModel):
    candidate_id: SafeIdentifierV2
    observation_count: int = Field(ge=0)
    replayed: bool
    source_revision_sha256: Sha256HexV2


class BackendDerivedKeyframeReceipt(StrictModel):
    candidate_id: SafeIdentifierV2
    keyframe_id: SafeIdentifierV2
    source_revision_sha256: Sha256HexV2
    receipt_sha256: Sha256HexV2
    replayed: bool


class BackendGeographicEvidenceReceipt(StrictModel):
    candidate_id: SafeIdentifierV2
    source_revision_sha256: Sha256HexV2
    request_sha256: Sha256HexV2
    hypothesis_count: int = Field(ge=0)
    abstention_count: int = Field(ge=0)
    replayed: bool


class BackendResearchEvidenceReceipt(StrictModel):
    candidate_id: SafeIdentifierV2
    plan_id: SafeIdentifierV2
    page_id: SafeIdentifierV2
    wave_number: int = Field(default=1, ge=1, le=16)
    wave_focus: tuple[SafeIdentifierV2, ...] = Field(
        default=("general",),
        min_length=1,
        max_length=32,
    )
    replayed: bool
    source_count: int = Field(ge=0)
    claim_count: int = Field(ge=0)
    media_count: int = Field(ge=0)
    duplicate_source_count: int = Field(ge=0)
    duplicate_claim_count: int = Field(ge=0)
    duplicate_media_count: int = Field(ge=0)
    completed: bool
    media_ticket_limit: int = Field(default=2_048, ge=1, le=2_048)
    safety_limit_reached: bool = False
    converged: bool = False
    zero_yield_wave_streak: int = Field(default=0, ge=0, le=100)
    coverage_ready: bool = False
    next_cursor: str | None = None
    source_revision_sha256: Sha256HexV2


class BackendResearchMediaAnalysisReceipt(StrictModel):
    candidate_id: SafeIdentifierV2
    batch_id: SafeIdentifierV2
    media_id: SafeIdentifierV2
    replayed: bool
    claim_count: int = Field(ge=0)
    keyframe_observation_count: int = Field(ge=0)
    transcription_receipt_count: int = Field(ge=0)
    journal_entry_count: int = Field(ge=1)
    source_revision_sha256: Sha256HexV2


class BackendIncidentDaySatelliteAnalysisReceipt(StrictModel):
    analysis_id: SafeIdentifierV2
    materialization_bundle_id: SafeIdentifierV2
    replayed: bool
    status: Literal["completed", "abstained"]
    claim_ids: tuple[SafeIdentifierV2, ...]
    source_revision_sha256: Sha256HexV2


class BackendIncidentDaySatelliteObservationReceipt(StrictModel):
    analysis_id: SafeIdentifierV2
    artifact_revision_id: SafeIdentifierV2
    result_id: SafeIdentifierV2
    replayed: bool
    status: Literal["completed", "no_observation", "unavailable"]
    claim_ids: tuple[SafeIdentifierV2, ...]
    source_revision_sha256: Sha256HexV2


class BackendPointAssessmentReceipt(StrictModel):
    schema_version: Literal["point-assessment-publication-receipt-1.0"]
    candidate_id: SafeIdentifierV2
    assessment_id: SafeIdentifierV2
    point_id: SafeIdentifierV2
    release_status: Literal[
        "eligible_for_automatic_publication",
        "held_for_review",
    ]
    publication_state: Literal["EDITOR_PUBLISHED", "HELD_FOR_REVIEW"]
    fire_activity_event_id: SafeIdentifierV2 | None = None
    localization_attempt_id: SafeIdentifierV2 | None = None
    publication_revision: int | None = Field(default=None, ge=1)
    competing_point_state: Literal["persisted_for_independent_assessment"] | None = None
    replayed: bool
    receipt_sha256: Sha256HexV2

    @model_validator(mode="after")
    def validate_publication_fields(self) -> BackendPointAssessmentReceipt:
        published = self.publication_state == "EDITOR_PUBLISHED"
        publication_fields = (
            self.fire_activity_event_id,
            self.localization_attempt_id,
            self.publication_revision,
        )
        if published != all(item is not None for item in publication_fields):
            raise ValueError("point publication receipt fields are inconsistent")
        if published != (self.release_status == "eligible_for_automatic_publication"):
            raise ValueError("point publication receipt release status is inconsistent")
        return self


def _snapshot_to_durable(
    snapshot: BackendEventEvidenceSnapshot,
    *,
    base_url: str,
) -> DurableEventEvidence:
    source_id = _stable_id("BACKEND-SOURCE", snapshot.candidate_id)
    source = EvidenceSource(
        source_id=source_id,
        origin_id=snapshot.candidate_id,
        publisher="FireViewer backend EventEvidence",
        retrieved_at=snapshot.updated_at,
        source_type="witness",
        independence_weight=1,
    )
    claims: list[Claim] = []
    if snapshot.bundle.message:
        claims.append(
            Claim(
                claim_id=_stable_id("BACKEND-CLAIM", snapshot.bundle.message),
                source_id=source_id,
                claim_type="contributor_observation",
                text=snapshot.bundle.message,
                observed_at=snapshot.bundle.observed_time.start_at,
                confidence=1,
            )
        )
    sources: list[EvidenceSource] = [source]
    media = list(
        EvidenceMedia(
            media_id=asset.evidence_asset_id,
            source_id=source_id,
            media_group_id=_stable_id("BACKEND-GROUP", asset.evidence_asset_id),
            origin_id=asset.evidence_asset_id,
            kind="photo" if asset.kind == "image" else "video",
            sha256=asset.sha256,
            captured_at=snapshot.bundle.observed_time.start_at,
        )
        for asset in snapshot.bundle.evidence_assets
    )
    parent_media = {item.media_id: item for item in media}
    for keyframe in snapshot.derived_keyframes:
        parent = parent_media[keyframe.parent_media_id]
        media.append(
            EvidenceMedia(
                media_id=keyframe.keyframe_id,
                source_id=parent.source_id,
                media_group_id=parent.media_group_id,
                origin_id=keyframe.keyframe_id,
                kind="keyframe",
                sha256=keyframe.sha256,
                captured_at=keyframe.captured_at,
                parent_media_id=keyframe.parent_media_id,
            )
        )
    if snapshot.research_evidence is not None:
        sources.extend(
            EvidenceSource.model_validate(item.model_dump(mode="json", exclude={"content_sha256"}))
            for item in snapshot.research_evidence.sources
        )
        claims.extend(snapshot.research_evidence.claims)
        media.extend(
            EvidenceMedia.model_validate(
                item.model_dump(
                    mode="json",
                    exclude={"source_url", "content_type", "size_bytes"},
                )
            )
            for item in snapshot.research_evidence.media
        )
    media_ids = {item.media_id for item in media}

    visual_observations: list[VisualObservation] = []
    vision_artifacts: list[DetectionResultV1] = []
    if snapshot.visual_evidence is not None:
        for item in snapshot.visual_evidence.observations:
            visual_observations.append(
                VisualObservation(
                    observation_id=item.observation_id,
                    media_id=item.media_id,
                    observation_type="detection",
                    result_reference=item.result_reference,
                    confidence=item.confidence,
                )
            )
            vision_artifacts.append(item.result)
    visual_observation_ids = {item.observation_id for item in visual_observations}
    location_candidates: list[LocationCandidate] = []
    geographic_references: list[GeographicReference] = []
    satellite_observations: list[SatelliteObservation] = []
    for raw in snapshot.bundle.external_observations:
        semantic_role = raw.get("semantic_role")
        geometry = raw.get("geometry_geojson")
        phenomenon = raw.get("phenomenon")
        if semantic_role not in {
            "raw_earth_observation",
            "sensor_detection",
            "interpreted_observation",
        } or not isinstance(geometry, dict):
            continue
        reference_id = raw.get("observation_id")
        artifact_revision = raw.get("artifact_revision_id")
        lineage_family_id = raw.get("lineage_family_id")
        if not isinstance(reference_id, str) or not isinstance(artifact_revision, str):
            continue
        observed_at = raw.get("observed_at")
        resolution = raw.get("resolution_m")
        confidence = raw.get("confidence")
        reference = GeographicReference.model_validate(
            {
                "reference_id": reference_id,
                "reference_kind": (
                    "satellite_hotspot"
                    if phenomenon == "thermal_hotspot"
                    or geometry.get("type") in {"Point", "MultiPoint"}
                    else "satellite_active_area"
                ),
                "geometry_geojson": geometry,
                "observed_at": observed_at,
                "horizontal_uncertainty_m": (
                    resolution if isinstance(resolution, (int, float)) else None
                ),
                "confidence": (confidence if isinstance(confidence, (int, float)) else None),
                "artifact_revision": artifact_revision,
                "lineage_family_id": (
                    lineage_family_id if isinstance(lineage_family_id, str) else None
                ),
            }
        )
        geographic_references.append(reference)
        if reference.observed_at is not None:
            satellite_observations.append(
                SatelliteObservation(
                    observation_id=reference.reference_id,
                    source_id=source_id,
                    observation_type=(
                        "hotspot" if reference.reference_kind == "satellite_hotspot" else "change"
                    ),
                    result_reference=artifact_revision,
                    acquired_at=reference.observed_at,
                    confidence=reference.confidence,
                )
            )

    def upload_location(asset: BackendEvidenceAsset) -> UploadLocationEvidence:
        capture_context = asset.capture_context
        viewpoint = (
            capture_context.viewpoint if capture_context is not None else snapshot.bundle.viewpoint
        )
        captured_at = (
            capture_context.captured_at
            if capture_context is not None
            else snapshot.bundle.observed_time.start_at
        )
        return UploadLocationEvidence(
            location_id=_stable_id("UPLOAD-LOCATION", asset.evidence_asset_id),
            media_id=asset.evidence_asset_id,
            longitude=viewpoint.longitude,
            latitude=viewpoint.latitude,
            accuracy_m=viewpoint.horizontal_accuracy_m,
            location_origin=("metadata" if viewpoint.origin == "DEVICE_GPS" else "user_declared"),
            captured_at=captured_at,
            heading_deg=viewpoint.yaw_deg,
            pitch_deg=viewpoint.pitch_deg,
            roll_deg=viewpoint.roll_deg,
            horizontal_fov_deg=viewpoint.fov_deg,
            vertical_fov_deg=viewpoint.vertical_fov_deg,
            image_width_px=viewpoint.image_width_px,
            image_height_px=viewpoint.image_height_px,
            heading_uncertainty_deg=(
                min(viewpoint.fov_deg / 2, 180)
                if viewpoint.yaw_deg is not None and viewpoint.fov_deg is not None
                else None
            ),
            altitude_m=viewpoint.altitude_m,
            altitude_uncertainty_m=(
                viewpoint.horizontal_accuracy_m if viewpoint.altitude_m is not None else None
            ),
            source_record_sha256=_canonical_sha256(
                {
                    "candidate_id": snapshot.candidate_id,
                    "capture_context": (
                        capture_context.model_dump(mode="json")
                        if capture_context is not None
                        else None
                    ),
                    "legacy_viewpoint": (
                        snapshot.bundle.viewpoint.model_dump(mode="json")
                        if capture_context is None
                        else None
                    ),
                    "captured_at": captured_at.isoformat(),
                    "asset_sha256": asset.sha256,
                }
            ),
        )

    upload_locations = tuple(upload_location(asset) for asset in snapshot.bundle.evidence_assets)

    for rank, attempt in enumerate(snapshot.localization_attempts, start=1):
        perception = _anchor_perception(attempt)
        media_id = None
        score = 0.0
        anchor_id = attempt.attempt_id
        if perception is not None:
            raw_media_id = perception.get("evidence_asset_id")
            if isinstance(raw_media_id, str) and raw_media_id in media_ids:
                media_id = raw_media_id
            raw_anchor_id = perception.get("anchor_id")
            if isinstance(raw_anchor_id, str):
                anchor_id = raw_anchor_id
            raw_score = perception.get("model_score")
            if isinstance(raw_score, (int, float)) and 0 <= raw_score <= 1:
                score = float(raw_score)
            if media_id is not None:
                observation_id = _stable_id("VISUAL", anchor_id)
                if observation_id not in visual_observation_ids:
                    visual_observations.append(
                        VisualObservation(
                            observation_id=observation_id,
                            media_id=media_id,
                            observation_type=(
                                "target_pixel"
                                if perception.get("source_point_normalized") is not None
                                else "detection"
                            ),
                            result_reference=anchor_id,
                            confidence=score,
                        )
                    )
                    visual_observation_ids.add(observation_id)
        coordinates = _point_coordinates(attempt.geometry)
        if coordinates is None or attempt.state not in {"PROPOSED", "REVIEWED", "SHADOW"}:
            continue
        longitude, latitude = coordinates
        location_candidates.append(
            LocationCandidate(
                candidate_id=attempt.attempt_id,
                longitude=longitude,
                latitude=latitude,
                radius_m=attempt.horizontal_uncertainty_m or 100_000,
                score=score,
                rank=rank,
                evidence_kind="geometric_verification",
                provider_id=_stable_id("BACKEND-MODEL", attempt.model_id or attempt.method),
                provider_version=attempt.model_revision or "unversioned-backend-result",
                source_id=source_id,
                media_id=media_id,
                reference_id=anchor_id,
            )
        )

    prior_fire_states = tuple(
        PriorFireStateReference(
            state_id=_stable_id("PRIOR-STATE", item.event_id),
            state_kind=(
                "active_points" if item.geometry.get("type") == "Point" else "situation_report"
            ),
            observed_at=item.observed_start_at,
            artifact_reference=(
                f"backend://event-evidence/{snapshot.candidate_id}/history/"
                f"{item.event_id}?revision={item.version}"
            ),
            artifact_sha256=_canonical_sha256(item.model_dump(mode="json")),
        )
        for item in snapshot.prior_fire_activity_events
    )
    for history_item in snapshot.prior_fire_activity_events:
        geometry_type = history_item.geometry.get("type")
        reference_kind: Literal["prior_active_point", "prior_fire_front", "prior_perimeter"]
        if history_item.phenomenon_kind in {"visible_front", "visible_fire_front"}:
            reference_kind = "prior_fire_front"
        elif geometry_type in {"Point", "MultiPoint"}:
            reference_kind = "prior_active_point"
        else:
            reference_kind = "prior_perimeter"
        geographic_references.append(
            GeographicReference(
                reference_id=_stable_id("HISTORY-GEO", history_item.event_id),
                reference_kind=reference_kind,
                geometry_geojson=history_item.geometry,
                observed_at=history_item.observed_start_at,
                artifact_revision=_canonical_sha256(history_item.model_dump(mode="json")),
            )
        )

    checks: list[GeospatialConsistencyCheck] = []
    for candidate in location_candidates:
        related_media_id = candidate.media_id
        camera_evidence = tuple(
            item for item in (candidate.candidate_id, related_media_id) if item is not None
        )
        if len(camera_evidence) >= 2:
            checks.extend(
                (
                    GeospatialConsistencyCheck(
                        check_id=_stable_id("CHECK-DISTANCE", candidate.candidate_id),
                        check_type="camera_distance",
                        status="unknown",
                        reason_code="camera_distance_pending_model_review",
                        evidence_ids=camera_evidence,
                    ),
                    GeospatialConsistencyCheck(
                        check_id=_stable_id("CHECK-BEARING", candidate.candidate_id),
                        check_type="camera_bearing",
                        status="unknown",
                        reason_code="camera_bearing_pending_model_review",
                        evidence_ids=camera_evidence,
                    ),
                )
            )
        for state in prior_fire_states:
            checks.append(
                GeospatialConsistencyCheck(
                    check_id=_stable_id(
                        "CHECK-HISTORY",
                        f"{candidate.candidate_id}:{state.state_id}",
                    ),
                    check_type="history_progression",
                    status="unknown",
                    reason_code="history_progression_pending_model_review",
                    evidence_ids=(candidate.candidate_id, state.state_id),
                )
            )

    event = EventEvidenceV1(
        event_id=snapshot.candidate_id,
        time_window=TimeWindow(
            from_at=snapshot.bundle.observed_time.start_at,
            to_at=snapshot.bundle.observed_time.end_at,
        ),
        sources=tuple(sources),
        claims=tuple(claims),
        media=tuple(media),
        visual_observations=tuple(visual_observations),
        satellite_observations=tuple(satellite_observations),
        location_candidates=tuple(location_candidates),
        needs_human_review=True,
    )
    terrain_reference = None
    if snapshot.terrain_reference is not None:
        terrain_reference = DurableTerrainReference(
            terrain_id=snapshot.terrain_reference.terrain_id,
            package_id=snapshot.terrain_reference.package_id,
            sha256=snapshot.terrain_reference.sha256,
            size_bytes=snapshot.terrain_reference.size_bytes,
            media_type=snapshot.terrain_reference.media_type,
            crs=snapshot.terrain_reference.crs,
            resolution_m=snapshot.terrain_reference.resolution_m,
            content_url=base_url + snapshot.terrain_reference.content_path,
        )
    return DurableEventEvidence(
        event=event,
        media_locations=(
            tuple(
                BackendEvidenceMediaLocation(
                    media_id=asset.evidence_asset_id,
                    working_file_url=base_url
                    + _ASSET_PATH.format(
                        candidate_id=quote(snapshot.candidate_id, safe=""),
                        asset_id=quote(asset.evidence_asset_id, safe=""),
                    ),
                )
                for asset in snapshot.bundle.evidence_assets
            )
            + tuple(
                BackendEvidenceMediaLocation(
                    media_id=keyframe.keyframe_id,
                    working_file_url=base_url + keyframe.content_path,
                )
                for keyframe in snapshot.derived_keyframes
            )
        ),
        vision_artifacts=tuple(vision_artifacts),
        upload_locations=upload_locations,
        prior_fire_states=prior_fire_states,
        geospatial_checks=tuple(checks),
        geographic_references=tuple(geographic_references),
        source_revision_sha256=snapshot.source_sha256,
        terrain_reference=terrain_reference,
        research_progress=(
            DurableResearchProgress(
                plan_id=snapshot.research_evidence.plan_id,
                plan_revision=snapshot.research_evidence.plan_revision,
                wave_number=snapshot.research_evidence.wave_number,
                wave_focus=snapshot.research_evidence.wave_focus,
                page_count=len(snapshot.research_evidence.pages),
                completed=snapshot.research_evidence.completed,
                media_ticket_limit=snapshot.research_evidence.media_ticket_limit,
                safety_limit_reached=snapshot.research_evidence.safety_limit_reached,
                converged=snapshot.research_evidence.converged,
                zero_yield_wave_streak=(snapshot.research_evidence.zero_yield_wave_streak),
                coverage_ready=snapshot.research_evidence.coverage_ready,
                next_cursor=snapshot.research_evidence.next_cursor,
            )
            if snapshot.research_evidence is not None
            else None
        ),
        research_journal=(
            snapshot.research_evidence.journal_entries
            if snapshot.research_evidence is not None
            else ()
        ),
        incident_id=snapshot.bundle.incident_id,
        viewpoint_label=snapshot.bundle.viewpoint.label,
        research_sources=(
            snapshot.research_evidence.sources if snapshot.research_evidence is not None else ()
        ),
    )


def _incident_day_to_durable(
    context: BackendIncidentDayResearchContext,
) -> DurableEventEvidence:
    research: BackendResearchEvidence | None = None
    if context.research_evidence is not None:
        normalized = dict(context.research_evidence)
        stored_analysis_id = normalized.pop("analysis_id", context.analysis_id)
        if stored_analysis_id != context.analysis_id:
            raise BackendEventEvidenceError("incident-day research target mismatch")
        normalized["candidate_id"] = context.analysis_id
        research = BackendResearchEvidence.model_validate(normalized)
    satellite_sources: list[EvidenceSource] = []
    satellite_claims: list[Claim] = []
    satellite_observations: list[SatelliteObservation] = []
    geographic_references: list[GeographicReference] = []
    source_ids = {item.source_id for item in research.sources} if research is not None else set()
    for item in context.spatial_observations:
        source_id = item.artifact_revision_id
        if source_id not in source_ids:
            satellite_sources.append(
                EvidenceSource.model_validate(
                    {
                        "source_id": source_id,
                        "origin_id": _stable_id("SATELLITE-ORIGIN", item.independent_family_key),
                        "source_url": item.source_url,
                        "publisher": item.attribution,
                        "published_at": item.observed_at,
                        "retrieved_at": item.retrieved_at,
                        "source_type": "satellite",
                        "independence_weight": 1.0,
                    }
                )
            )
            source_ids.add(source_id)
        metric_text = json.dumps(
            item.metrics,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        descriptors = [
            value
            for value in (
                item.processor,
                item.source_dataset,
                item.satellite,
                item.instrument,
            )
            if value is not None
        ]
        satellite_claims.append(
            Claim(
                claim_id=item.claim_id,
                source_id=source_id,
                claim_type=item.assertion_kind,
                text=(
                    f"Observation satellite {item.assertion_kind}; "
                    f"provenance={','.join(descriptors) or item.provider_key}; "
                    f"metrics={metric_text}."
                ),
                observed_at=item.observed_at,
                confidence=item.confidence if item.confidence is not None else 0.5,
            )
        )
        observation_id = _stable_id("SATELLITE-OBSERVATION", item.claim_id)
        hotspot = item.assertion_kind in {"thermal_hotspot", "thermal_footprint"}
        satellite_observations.append(
            SatelliteObservation(
                observation_id=observation_id,
                source_id=source_id,
                observation_type=(
                    "burn_scar"
                    if item.assertion_kind == "burned_area"
                    else "hotspot"
                    if hotspot
                    else "change"
                ),
                result_reference=item.claim_id,
                acquired_at=item.observed_at,
                confidence=item.confidence,
            )
        )
        geographic_references.append(
            GeographicReference(
                reference_id=observation_id,
                reference_kind=("satellite_hotspot" if hotspot else "satellite_active_area"),
                geometry_geojson=item.geometry_geojson,
                observed_at=item.observed_at,
                horizontal_uncertainty_m=(item.horizontal_accuracy_m or item.resolution_m),
                confidence=item.confidence,
                artifact_revision=item.artifact_revision_id,
                lineage_family_id=_stable_id(
                    "SATELLITE-FAMILY",
                    item.independent_family_key,
                ),
            )
        )
    event = EventEvidenceV1(
        event_id=context.analysis_id,
        time_window=TimeWindow(
            from_at=context.window_start_at,
            to_at=context.window_end_at,
        ),
        sources=(research.sources if research is not None else ()) + tuple(satellite_sources),
        claims=(research.claims if research is not None else ()) + tuple(satellite_claims),
        media=research.media if research is not None else (),
        satellite_observations=tuple(satellite_observations),
        needs_human_review=True,
    )
    return DurableEventEvidence(
        event=event,
        media_locations=(),
        vision_artifacts=(),
        upload_locations=(),
        prior_fire_states=(),
        geospatial_checks=(),
        geographic_references=tuple(geographic_references),
        source_revision_sha256=context.source_sha256,
        research_progress=(
            DurableResearchProgress(
                plan_id=research.plan_id,
                plan_revision=research.plan_revision,
                wave_number=research.wave_number,
                wave_focus=research.wave_focus,
                page_count=len(research.pages),
                completed=research.completed,
                media_ticket_limit=research.media_ticket_limit,
                safety_limit_reached=research.safety_limit_reached,
                converged=research.converged,
                zero_yield_wave_streak=research.zero_yield_wave_streak,
                coverage_ready=research.coverage_ready,
                next_cursor=research.next_cursor,
            )
            if research is not None
            else None
        ),
        research_journal=research.journal_entries if research is not None else (),
        incident_id=context.fire_id,
        viewpoint_label=context.incident_name,
        research_source_policies={
            domain: policy.model_dump(mode="json")
            for domain, policy in context.source_policies.items()
        },
        research_search_templates=dict(context.search_templates),
        research_target_kind="incident_day",
        incident_day_coverage=context.coverage,
        satellite_artifact_tickets=context.satellite_artifacts,
        spatial_observation_tickets=context.spatial_observations,
        research_sources=research.sources if research is not None else (),
        research_media_tickets=research.media if research is not None else (),
        research_media_analysis_batches=(
            research.media_analysis_batches if research is not None else ()
        ),
        satellite_analysis_batches=(
            research.satellite_analysis_batches if research is not None else ()
        ),
        satellite_observation_batches=(
            research.satellite_observation_batches if research is not None else ()
        ),
        incident_day_episode_id=context.episode_id,
        incident_day_local_date=context.local_date,
        incident_day_timezone=context.timezone,
        incident_day_bbox=context.incident_bbox,
    )


class AzureBackendEventEvidenceAdapter:
    """Read durable backend evidence; never indexes, writes, or caches it locally."""

    def __init__(
        self,
        config: AzureBackendEventEvidenceConfig,
        *,
        transport: BackendEventEvidenceTransport | None = None,
        media_transport: BackendEvidenceMediaTransport | None = None,
    ) -> None:
        self._config = config
        self._transport = transport or UrllibBackendEventEvidenceTransport()
        self._media_transport = media_transport or UrllibBackendEventEvidenceTransport()

    def read(self, event_id: str) -> DurableEventEvidence:
        if not event_id or len(event_id) > 128:
            raise ValueError("event_id is invalid")
        url = self._config.base_url + _SNAPSHOT_PATH.format(
            candidate_id=quote(event_id, safe=""),
        )
        response = self._transport.get_json(
            url,
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {self._config.bearer_token.get_secret_value()}",
            },
            timeout_seconds=self._config.timeout_seconds,
            max_response_bytes=self._config.max_response_bytes,
        )
        raw_payload = dict(response.payload)
        raw_checksum = raw_payload.pop("source_sha256", None)
        if not isinstance(raw_checksum, str) or _canonical_sha256(raw_payload) != raw_checksum:
            raise BackendEventEvidenceError("backend EventEvidence checksum mismatch")
        checksum_header = response.headers.get("x-checksum-sha256")
        etag = response.headers.get("etag")
        if checksum_header != raw_checksum or etag != f'"{raw_checksum}"':
            raise BackendEventEvidenceError("backend EventEvidence revision headers mismatch")
        snapshot = BackendEventEvidenceSnapshot.model_validate(response.payload)
        if snapshot.candidate_id != event_id:
            raise BackendEventEvidenceError("backend EventEvidence candidate mismatch")
        return _snapshot_to_durable(snapshot, base_url=self._config.base_url)

    def read_media(
        self,
        *,
        event_id: str,
        media_id: str,
        expected_sha256: str,
        maximum_bytes: int = 8 * 1_024 * 1_024,
    ) -> PointSupervisorInputImage:
        if not event_id or len(event_id) > 128 or not media_id or len(media_id) > 128:
            raise ValueError("backend media identity is invalid")
        if maximum_bytes <= 0 or maximum_bytes > 8 * 1_024 * 1_024:
            raise ValueError("backend media byte limit is invalid")
        if media_id.startswith("KF-"):
            path = _KEYFRAME_PATH.format(
                candidate_id=quote(event_id, safe=""),
                keyframe_id=quote(media_id, safe=""),
            )
        else:
            path = _ASSET_PATH.format(
                candidate_id=quote(event_id, safe=""),
                asset_id=quote(media_id, safe=""),
            )
        url = self._config.base_url + path
        response = self._media_transport.get_bytes(
            url,
            headers={
                "Accept": "image/jpeg,image/png,image/webp",
                "Authorization": f"Bearer {self._config.bearer_token.get_secret_value()}",
            },
            timeout_seconds=self._config.timeout_seconds,
            max_response_bytes=maximum_bytes,
        )
        digest = sha256(response.content).hexdigest()
        if digest != expected_sha256:
            raise BackendEventEvidenceError("backend media checksum mismatch")
        if (
            response.headers.get("x-checksum-sha256") != digest
            or response.headers.get("etag") != f'"{digest}"'
        ):
            raise BackendEventEvidenceError("backend media revision headers mismatch")
        if response.content_type not in {"image/jpeg", "image/png", "image/webp"}:
            raise BackendEventEvidenceError(
                "backend media type is not accepted by the point supervisor"
            )
        accepted_content_type = cast(
            Literal["image/jpeg", "image/png", "image/webp"],
            response.content_type,
        )
        try:
            return PointSupervisorInputImage(
                media_id=media_id,
                content_type=accepted_content_type,
                sha256=digest,
                content=response.content,
            )
        except ValueError as exc:
            raise BackendEventEvidenceError(
                "backend media type is not accepted by the point supervisor"
            ) from exc


class AzureBackendIncidentDayEvidenceAdapter:
    """Read the backend-created incident/day target without perimeter truth."""

    def __init__(
        self,
        config: AzureBackendEventEvidenceConfig,
        *,
        transport: BackendEventEvidenceTransport | None = None,
    ) -> None:
        self._config = config
        self._transport = transport or UrllibBackendEventEvidenceTransport()

    def read(self, event_id: str) -> DurableEventEvidence:
        if not event_id or len(event_id) > 128:
            raise ValueError("analysis_id is invalid")
        response = self._transport.get_json(
            self._config.base_url
            + _INCIDENT_DAY_RESEARCH_PATH.format(
                analysis_id=quote(event_id, safe=""),
            ),
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {self._config.bearer_token.get_secret_value()}",
            },
            timeout_seconds=self._config.timeout_seconds,
            max_response_bytes=self._config.max_response_bytes,
        )
        raw_payload = dict(response.payload)
        checksum = raw_payload.pop("source_sha256", None)
        if not isinstance(checksum, str) or _canonical_sha256(raw_payload) != checksum:
            raise BackendEventEvidenceError("incident-day evidence checksum mismatch")
        if (
            response.headers.get("x-checksum-sha256") != checksum
            or response.headers.get("etag") != f'"{checksum}"'
        ):
            raise BackendEventEvidenceError("incident-day evidence revision headers mismatch")
        context = BackendIncidentDayResearchContext.model_validate(response.payload)
        if context.analysis_id != event_id:
            raise BackendEventEvidenceError("incident-day evidence target mismatch")
        return _incident_day_to_durable(context)


class AzureBackendIncidentDayGeometryReviewAdapter:
    """Read the frozen deterministic perimeter without evaluation truth."""

    def __init__(
        self,
        config: AzureBackendEventEvidenceConfig,
        *,
        transport: BackendIncidentDayGeometryReviewTransport | None = None,
    ) -> None:
        self._config = config
        self._transport = transport or UrllibBackendEventEvidenceTransport()

    def read(self, analysis_id: str) -> BackendIncidentDayGeometryReviewContext:
        if not analysis_id or len(analysis_id) > 128:
            raise ValueError("analysis_id is invalid")
        response = self._transport.get_json(
            self._config.base_url
            + _INCIDENT_DAY_GEOMETRY_REVIEW_PATH.format(
                analysis_id=quote(analysis_id, safe=""),
            ),
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {self._config.bearer_token.get_secret_value()}",
            },
            timeout_seconds=self._config.timeout_seconds,
            max_response_bytes=self._config.max_response_bytes,
        )
        raw_payload = dict(response.payload)
        checksum = raw_payload.pop("source_sha256", None)
        if not isinstance(checksum, str) or _canonical_sha256(raw_payload) != checksum:
            raise BackendEventEvidenceError("incident-day geometry-review checksum mismatch")
        if (
            response.headers.get("x-checksum-sha256") != checksum
            or response.headers.get("etag") != f'"{checksum}"'
        ):
            raise BackendEventEvidenceError(
                "incident-day geometry-review revision headers mismatch"
            )
        context = BackendIncidentDayGeometryReviewContext.model_validate(response.payload)
        if context.analysis_id != analysis_id:
            raise BackendEventEvidenceError("incident-day geometry-review target mismatch")
        return context

    def publish(
        self,
        *,
        analysis_id: str,
        review: BackendIncidentDayGeometryReviewRequest,
    ) -> BackendIncidentDayGeometryReviewReceipt:
        if review.analysis_id != analysis_id:
            raise BackendEventEvidenceError("incident-day geometry-review target mismatch")
        response = self._transport.post_json(
            self._config.base_url
            + _INCIDENT_DAY_GEOMETRY_CORRECTION_PATH.format(
                analysis_id=quote(analysis_id, safe=""),
            ),
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {self._config.bearer_token.get_secret_value()}",
            },
            payload=review.model_dump(mode="json", by_alias=True),
            timeout_seconds=self._config.timeout_seconds,
            max_response_bytes=self._config.max_response_bytes,
        )
        receipt = BackendIncidentDayGeometryReviewReceipt.model_validate(response.payload)
        if receipt.analysis_id != analysis_id or receipt.review_id != review.review_id:
            raise BackendEventEvidenceError("incident-day geometry-review receipt mismatch")
        checksum = receipt.receipt_sha256
        if (
            response.headers.get("x-checksum-sha256") != checksum
            or response.headers.get("etag") != f'"{checksum}"'
        ):
            raise BackendEventEvidenceError(
                "incident-day geometry-review receipt headers mismatch"
            )
        return receipt


class BackendVisualEvidencePublisher:
    """Persist YOLO observations while keeping all geographic collections untouched."""

    def __init__(
        self,
        config: AzureBackendEventEvidenceConfig,
        *,
        transport: BackendVisualEvidenceTransport | None = None,
    ) -> None:
        self._config = config
        self._transport = transport or UrllibBackendEventEvidenceTransport()

    def publish(
        self,
        *,
        candidate_id: str,
        source_revision_sha256: str,
        observations: tuple[VisualObservation, ...],
        artifacts: Mapping[str, DetectionResultV1],
    ) -> BackendVisualEvidenceReceipt:
        payload_observations: list[dict[str, Any]] = []
        for observation in observations:
            if observation.observation_type != "detection":
                continue
            result = artifacts.get(observation.result_reference)
            if result is None or result.media_id != observation.media_id:
                raise BackendEventEvidenceError(
                    "visual observation has no matching detection artifact"
                )
            payload_observations.append(
                {
                    **observation.model_dump(mode="json"),
                    "result": result.model_dump(mode="json", by_alias=True),
                }
            )
        url = self._config.base_url + _VISUAL_EVIDENCE_PATH.format(
            candidate_id=quote(candidate_id, safe=""),
        )
        response = self._transport.post_json(
            url,
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {self._config.bearer_token.get_secret_value()}",
            },
            payload={
                "schema_version": "visual-evidence-1.0",
                "candidate_id": candidate_id,
                "source_revision_sha256": source_revision_sha256,
                "observations": payload_observations,
            },
            timeout_seconds=self._config.timeout_seconds,
            max_response_bytes=self._config.max_response_bytes,
        )
        receipt = BackendVisualEvidenceReceipt.model_validate(response.payload)
        if receipt.candidate_id != candidate_id:
            raise BackendEventEvidenceError("backend visual evidence candidate mismatch")
        checksum = receipt.source_revision_sha256
        if (
            response.headers.get("x-checksum-sha256") != checksum
            or response.headers.get("etag") != f'"{checksum}"'
        ):
            raise BackendEventEvidenceError("backend visual evidence revision headers mismatch")
        return receipt


class BackendKeyframeEvidencePublisher:
    """Persist one derived PNG and advance the immutable EventEvidence revision."""

    def __init__(
        self,
        config: AzureBackendEventEvidenceConfig,
        *,
        transport: BackendKeyframeEvidenceTransport | None = None,
    ) -> None:
        self._config = config
        self._transport = transport or UrllibBackendEventEvidenceTransport()

    def publish(
        self,
        *,
        candidate_id: str,
        source_revision_sha256: str,
        media: EvidenceMedia,
        frame_index: int,
        timestamp_seconds: float,
        content: bytes,
    ) -> BackendDerivedKeyframeReceipt:
        if media.kind != "keyframe" or media.parent_media_id is None:
            raise BackendEventEvidenceError("derived keyframe lineage is missing")
        digest = sha256(content).hexdigest()
        if digest != media.sha256:
            raise BackendEventEvidenceError("derived keyframe content hash mismatch")
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {self._config.bearer_token.get_secret_value()}",
            "X-Parent-Media-Id": str(media.parent_media_id),
            "X-Source-Revision-Sha256": source_revision_sha256,
            "X-Content-Sha256": digest,
            "X-Frame-Index": str(frame_index),
            "X-Timestamp-Seconds": format(timestamp_seconds, ".9g"),
        }
        if media.captured_at is not None:
            headers["X-Captured-At"] = media.captured_at.isoformat()
        response = self._transport.put_bytes(
            self._config.base_url
            + _KEYFRAME_UPLOAD_PATH.format(
                candidate_id=quote(candidate_id, safe=""),
                keyframe_id=quote(media.media_id, safe=""),
            ),
            headers=headers,
            content=content,
            timeout_seconds=self._config.timeout_seconds,
            max_response_bytes=self._config.max_response_bytes,
        )
        receipt = BackendDerivedKeyframeReceipt.model_validate(response.payload)
        if receipt.candidate_id != candidate_id or receipt.keyframe_id != media.media_id:
            raise BackendEventEvidenceError("backend keyframe receipt mismatch")
        checksum = receipt.source_revision_sha256
        if (
            response.headers.get("x-checksum-sha256") != checksum
            or response.headers.get("etag") != f'"{checksum}"'
        ):
            raise BackendEventEvidenceError("backend keyframe revision headers mismatch")
        return receipt


class BackendGeographicEvidencePublisher:
    """Persist deterministic hypotheses without any publication or map authority."""

    def __init__(
        self,
        config: AzureBackendEventEvidenceConfig,
        *,
        transport: BackendVisualEvidenceTransport | None = None,
    ) -> None:
        self._config = config
        self._transport = transport or UrllibBackendEventEvidenceTransport()

    def publish(
        self,
        *,
        candidate_id: str,
        payload: Mapping[str, Any],
    ) -> BackendGeographicEvidenceReceipt:
        if payload.get("event_id") != candidate_id:
            raise BackendEventEvidenceError("geographic evidence candidate mismatch")
        response = self._transport.post_json(
            self._config.base_url
            + _GEOGRAPHIC_EVIDENCE_PATH.format(
                candidate_id=quote(candidate_id, safe=""),
            ),
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {self._config.bearer_token.get_secret_value()}",
            },
            payload=payload,
            timeout_seconds=self._config.timeout_seconds,
            max_response_bytes=self._config.max_response_bytes,
        )
        receipt = BackendGeographicEvidenceReceipt.model_validate(response.payload)
        if receipt.candidate_id != candidate_id:
            raise BackendEventEvidenceError("backend geographic evidence receipt mismatch")
        checksum = receipt.source_revision_sha256
        if (
            response.headers.get("x-checksum-sha256") != checksum
            or response.headers.get("etag") != f'"{checksum}"'
        ):
            raise BackendEventEvidenceError("backend geographic revision headers mismatch")
        return receipt


class BackendResearchEvidencePublisher:
    """Append one deterministic research page to the durable backend graph."""

    def __init__(
        self,
        config: AzureBackendEventEvidenceConfig,
        *,
        transport: BackendVisualEvidenceTransport | None = None,
    ) -> None:
        self._config = config
        self._transport = transport or UrllibBackendEventEvidenceTransport()

    def publish(
        self,
        *,
        candidate_id: str,
        payload: Mapping[str, Any],
    ) -> BackendResearchEvidenceReceipt:
        if payload.get("candidate_id") != candidate_id:
            raise BackendEventEvidenceError("research evidence candidate mismatch")
        url = self._config.base_url + _RESEARCH_EVIDENCE_PATH.format(
            candidate_id=quote(candidate_id, safe=""),
        )
        response = self._transport.post_json(
            url,
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {self._config.bearer_token.get_secret_value()}",
            },
            payload=payload,
            timeout_seconds=self._config.timeout_seconds,
            max_response_bytes=self._config.max_response_bytes,
        )
        receipt = BackendResearchEvidenceReceipt.model_validate(response.payload)
        if receipt.candidate_id != candidate_id:
            raise BackendEventEvidenceError("backend research evidence candidate mismatch")
        checksum = receipt.source_revision_sha256
        if (
            response.headers.get("x-checksum-sha256") != checksum
            or response.headers.get("etag") != f'"{checksum}"'
        ):
            raise BackendEventEvidenceError("backend research evidence revision headers mismatch")
        return receipt


class BackendIncidentDayResearchPublisher:
    """Append one ticket-only page to a durable incident/day analysis window."""

    def __init__(
        self,
        config: AzureBackendEventEvidenceConfig,
        *,
        transport: BackendVisualEvidenceTransport | None = None,
    ) -> None:
        self._config = config
        self._transport = transport or UrllibBackendEventEvidenceTransport()

    def publish(
        self,
        *,
        candidate_id: str,
        payload: Mapping[str, Any],
    ) -> BackendResearchEvidenceReceipt:
        if payload.get("candidate_id") != candidate_id:
            raise BackendEventEvidenceError("incident-day research target mismatch")
        response = self._transport.post_json(
            self._config.base_url
            + _INCIDENT_DAY_RESEARCH_PAGE_PATH.format(
                analysis_id=quote(candidate_id, safe=""),
            ),
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {self._config.bearer_token.get_secret_value()}",
            },
            payload=payload,
            timeout_seconds=self._config.timeout_seconds,
            max_response_bytes=self._config.max_response_bytes,
        )
        receipt = BackendResearchEvidenceReceipt.model_validate(response.payload)
        if receipt.candidate_id != candidate_id:
            raise BackendEventEvidenceError("incident-day research receipt mismatch")
        checksum = receipt.source_revision_sha256
        if (
            response.headers.get("x-checksum-sha256") != checksum
            or response.headers.get("etag") != f'"{checksum}"'
        ):
            raise BackendEventEvidenceError("incident-day research revision headers mismatch")
        return receipt


class BackendIncidentDayMediaAnalysisPublisher:
    """Persist only derived public-media tickets after source collection closes."""

    def __init__(
        self,
        config: AzureBackendEventEvidenceConfig,
        *,
        transport: BackendVisualEvidenceTransport | None = None,
    ) -> None:
        self._config = config
        self._transport = transport or UrllibBackendEventEvidenceTransport()

    def publish(
        self,
        *,
        candidate_id: str,
        payload: Mapping[str, Any],
    ) -> BackendResearchMediaAnalysisReceipt:
        if payload.get("candidate_id") != candidate_id:
            raise BackendEventEvidenceError("incident-day media-analysis target mismatch")
        response = self._transport.post_json(
            self._config.base_url
            + _INCIDENT_DAY_MEDIA_ANALYSIS_PATH.format(
                analysis_id=quote(candidate_id, safe=""),
            ),
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {self._config.bearer_token.get_secret_value()}",
            },
            payload=payload,
            timeout_seconds=self._config.timeout_seconds,
            max_response_bytes=self._config.max_response_bytes,
        )
        receipt = BackendResearchMediaAnalysisReceipt.model_validate(response.payload)
        if receipt.candidate_id != candidate_id:
            raise BackendEventEvidenceError("incident-day media-analysis receipt mismatch")
        checksum = receipt.source_revision_sha256
        if (
            response.headers.get("x-checksum-sha256") != checksum
            or response.headers.get("etag") != f'"{checksum}"'
        ):
            raise BackendEventEvidenceError("incident-day media-analysis revision headers mismatch")
        return receipt


class BackendIncidentDaySatelliteAnalysisPublisher:
    """Persist only derived satellite geometry tickets or an explicit abstention."""

    def __init__(
        self,
        config: AzureBackendEventEvidenceConfig,
        *,
        transport: BackendVisualEvidenceTransport | None = None,
    ) -> None:
        self._config = config
        self._transport = transport or UrllibBackendEventEvidenceTransport()

    def publish(
        self,
        *,
        candidate_id: str,
        payload: Mapping[str, Any],
    ) -> BackendIncidentDaySatelliteAnalysisReceipt:
        if payload.get("analysis_id") != candidate_id:
            raise BackendEventEvidenceError("incident-day satellite target mismatch")
        response = self._transport.post_json(
            self._config.base_url
            + _INCIDENT_DAY_SATELLITE_ANALYSIS_PATH.format(
                analysis_id=quote(candidate_id, safe=""),
            ),
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {self._config.bearer_token.get_secret_value()}",
            },
            payload=payload,
            timeout_seconds=self._config.timeout_seconds,
            max_response_bytes=self._config.max_response_bytes,
        )
        receipt = BackendIncidentDaySatelliteAnalysisReceipt.model_validate(response.payload)
        if receipt.analysis_id != candidate_id:
            raise BackendEventEvidenceError("incident-day satellite-analysis receipt mismatch")
        checksum = receipt.source_revision_sha256
        if (
            response.headers.get("x-checksum-sha256") != checksum
            or response.headers.get("etag") != f'"{checksum}"'
        ):
            raise BackendEventEvidenceError(
                "incident-day satellite-analysis revision headers mismatch"
            )
        return receipt


class BackendIncidentDaySatelliteObservationPublisher:
    """Persist deterministic satellite observations without retaining source bytes."""

    def __init__(
        self,
        config: AzureBackendEventEvidenceConfig,
        *,
        transport: BackendVisualEvidenceTransport | None = None,
    ) -> None:
        self._config = config
        self._transport = transport or UrllibBackendEventEvidenceTransport()

    def publish(
        self,
        *,
        candidate_id: str,
        payload: Mapping[str, Any],
    ) -> BackendIncidentDaySatelliteObservationReceipt:
        if payload.get("analysis_id") != candidate_id:
            raise BackendEventEvidenceError("incident-day satellite target mismatch")
        response = self._transport.post_json(
            self._config.base_url
            + _INCIDENT_DAY_SATELLITE_OBSERVATION_PATH.format(
                analysis_id=quote(candidate_id, safe=""),
            ),
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {self._config.bearer_token.get_secret_value()}",
            },
            payload=payload,
            timeout_seconds=self._config.timeout_seconds,
            max_response_bytes=self._config.max_response_bytes,
        )
        receipt = BackendIncidentDaySatelliteObservationReceipt.model_validate(response.payload)
        if receipt.analysis_id != candidate_id:
            raise BackendEventEvidenceError("incident-day satellite-observation receipt mismatch")
        checksum = receipt.source_revision_sha256
        if (
            response.headers.get("x-checksum-sha256") != checksum
            or response.headers.get("etag") != f'"{checksum}"'
        ):
            raise BackendEventEvidenceError(
                "incident-day satellite-observation revision headers mismatch"
            )
        return receipt


class BackendPointAssessmentPublisher:
    """Persist the immutable point ticket and let event-2.0 enforce publication."""

    def __init__(
        self,
        config: AzureBackendEventEvidenceConfig,
        *,
        transport: BackendVisualEvidenceTransport | None = None,
    ) -> None:
        self._config = config
        self._transport = transport or UrllibBackendEventEvidenceTransport()

    def publish(
        self,
        *,
        candidate_id: str,
        point_bundle: PointEvidenceBundleV1,
        assessment: PointAssessmentV1,
    ) -> BackendPointAssessmentReceipt:
        if point_bundle.event_id != candidate_id or assessment.event_id != candidate_id:
            raise BackendEventEvidenceError("point assessment candidate mismatch")
        if assessment.point_id != point_bundle.point.point_id:
            raise BackendEventEvidenceError("point assessment source point mismatch")
        url = self._config.base_url + _POINT_ASSESSMENT_PATH.format(
            candidate_id=quote(candidate_id, safe=""),
        )
        response = self._transport.post_json(
            url,
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {self._config.bearer_token.get_secret_value()}",
            },
            payload={
                "schema_version": "point-assessment-publication-1.0",
                "point_bundle": point_bundle.model_dump(mode="json", by_alias=True),
                "assessment": assessment.model_dump(mode="json", by_alias=True),
            },
            timeout_seconds=self._config.timeout_seconds,
            max_response_bytes=self._config.max_response_bytes,
        )
        receipt = BackendPointAssessmentReceipt.model_validate(response.payload)
        if (
            receipt.candidate_id != candidate_id
            or receipt.assessment_id != assessment.assessment_id
            or receipt.point_id != assessment.point_id
            or receipt.release_status != assessment.release_status
        ):
            raise BackendEventEvidenceError("backend point assessment receipt mismatch")
        checksum = receipt.receipt_sha256
        if (
            response.headers.get("x-checksum-sha256") != checksum
            or response.headers.get("etag") != f'"{checksum}"'
        ):
            raise BackendEventEvidenceError("backend point assessment receipt headers mismatch")
        return receipt


__all__ = [
    "AzureBackendEventEvidenceAdapter",
    "AzureBackendEventEvidenceConfig",
    "AzureBackendIncidentDayEvidenceAdapter",
    "AzureBackendIncidentDayGeometryReviewAdapter",
    "BackendBinaryResponse",
    "BackendDerivedKeyframeReceipt",
    "BackendEventEvidenceError",
    "BackendEventEvidenceNotFoundError",
    "BackendEventEvidenceSnapshot",
    "BackendEventEvidenceTransport",
    "BackendEvidenceMediaLocation",
    "BackendEvidenceMediaTransport",
    "BackendGeographicEvidencePublisher",
    "BackendGeographicEvidenceReceipt",
    "BackendIncidentDayGeometryReviewContext",
    "BackendIncidentDayGeometryReviewReceipt",
    "BackendIncidentDayGeometryReviewRequest",
    "BackendIncidentDayMediaAnalysisPublisher",
    "BackendIncidentDayResearchContext",
    "BackendIncidentDayResearchPublisher",
    "BackendIncidentDaySatelliteAnalysisPublisher",
    "BackendIncidentDaySatelliteAnalysisReceipt",
    "BackendIncidentDaySatelliteObservationPublisher",
    "BackendIncidentDaySatelliteObservationReceipt",
    "BackendJsonResponse",
    "BackendKeyframeEvidencePublisher",
    "BackendKeyframeEvidenceTransport",
    "BackendPointAssessmentPublisher",
    "BackendPointAssessmentReceipt",
    "BackendResearchEvidence",
    "BackendResearchEvidencePublisher",
    "BackendResearchEvidenceReceipt",
    "BackendResearchMediaAnalysisReceipt",
    "BackendTerrainReference",
    "BackendVisualEvidencePublisher",
    "BackendVisualEvidenceReceipt",
    "BackendVisualEvidenceTransport",
    "DurableEventEvidence",
    "DurableResearchProgress",
    "DurableTerrainReference",
    "EventEvidenceRepository",
    "UrllibBackendEventEvidenceTransport",
]
