from __future__ import annotations

import json
from datetime import date, datetime
from enum import StrEnum
from hashlib import sha256
from math import isfinite
from typing import Annotated, Literal

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, JsonValue, model_validator

from fireviewer_contracts.geometry_contract import validate_geojson_geometry


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class BatchType(StrEnum):
    USER_MEDIA = "user_media"
    EXTERNAL_MEDIA = "external_media"
    SATELLITE_MEDIA = "satellite_media"


class Priority(StrEnum):
    USER_DEADLINE = "user_deadline"
    SCHEDULED_COMBINED = "scheduled_combined"
    SCHEDULED = "scheduled"


class MediaType(StrEnum):
    IMAGE = "image"
    VIDEO = "video"
    AUDIO = "audio"
    ARTICLE = "article"
    SATELLITE_IMAGE = "satellite_image"
    SATELLITE_DATA = "satellite_data"


class LocationOrigin(StrEnum):
    METADATA = "METADATA"
    USER_DECLARED = "USER_DECLARED"
    EXPLICIT_SOURCE_GEOMETRY = "EXPLICIT_SOURCE_GEOMETRY"
    HUMAN_CONFIRMED = "HUMAN_CONFIRMED"


class LocationStatus(StrEnum):
    NO_LOCATION = "NO_LOCATION"
    CAPTURE_LOCATION_ONLY = "CAPTURE_LOCATION_ONLY"
    USER_DECLARED_OBSERVATION_LOCATION = "USER_DECLARED_OBSERVATION_LOCATION"
    EXPLICIT_SOURCE_GEOMETRY = "EXPLICIT_SOURCE_GEOMETRY"
    HUMAN_CONFIRMED_OBSERVATION_LOCATION = "HUMAN_CONFIRMED_OBSERVATION_LOCATION"


class InputMetadata(StrictModel):
    captured_at: datetime | None = None
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    gps_accuracy_m: float | None = Field(default=None, gt=0, le=100_000)
    location_origin: LocationOrigin | None = None

    @model_validator(mode="after")
    def coordinates_are_complete_and_sourced(self) -> InputMetadata:
        coordinates = (self.latitude, self.longitude)
        if (coordinates[0] is None) != (coordinates[1] is None):
            raise ValueError("latitude and longitude must be provided together")
        if coordinates[0] is not None and self.location_origin is None:
            raise ValueError("coordinates require an explicit location_origin")
        if coordinates[0] is None and (self.gps_accuracy_m is not None or self.location_origin):
            raise ValueError("location metadata requires coordinates")
        return self


class FrameInput(StrictModel):
    frame_id: Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")]
    timestamp_s: float = Field(ge=0)
    working_file_url: AnyHttpUrl


class DeclaredObservationV2(StrictModel):
    observed_at: datetime
    observation_type: str = Field(min_length=2, max_length=128)
    direct_observation: bool
    description: str = Field(min_length=20, max_length=4_000)
    location_mode: Literal["place", "device", "manual"]
    location_label: str | None = Field(default=None, max_length=500)
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    uncertainty_m: float | None = Field(default=None, gt=0, le=100_000)
    media_captured_at: datetime | None = None
    media_direction: str | None = Field(default=None, max_length=128)

    @model_validator(mode="after")
    def validate_declared_observation(self) -> DeclaredObservationV2:
        if self.observed_at.tzinfo is None or self.observed_at.utcoffset() is None:
            raise ValueError("declared observation time must include a timezone")
        if self.media_captured_at is not None and (
            self.media_captured_at.tzinfo is None or self.media_captured_at.utcoffset() is None
        ):
            raise ValueError("declared media capture time must include a timezone")
        if (self.latitude is None) != (self.longitude is None):
            raise ValueError("declared location coordinates must be supplied together")
        if self.uncertainty_m is not None and self.latitude is None:
            raise ValueError("declared location uncertainty requires coordinates")
        return self


class SourceContext(StrictModel):
    source_reference_url: AnyHttpUrl | None = None
    attribution: str | None = Field(default=None, max_length=500)
    trust: Literal["unverified", "partner", "institutional", "operator"] | None = None
    source_kind: str | None = Field(default=None, max_length=64)
    source_confidence: Literal["A+", "A", "B", "lead"] | None = None
    publication_policy: str | None = Field(default=None, max_length=64)
    claim_types: tuple[str, ...] = Field(default=(), max_length=32)
    declared_observation: DeclaredObservationV2 | None = None


class BatchItem(StrictModel):
    input_id: Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")]
    media_type: MediaType
    working_file_url: AnyHttpUrl | None = None
    metadata: InputMetadata = Field(default_factory=InputMetadata)
    frames: tuple[FrameInput, ...] = Field(default=(), max_length=64)
    audio_url: AnyHttpUrl | None = None
    article_text: str | None = Field(default=None, max_length=100_000)
    source_context: SourceContext | None = None

    @model_validator(mode="after")
    def has_processable_content(self) -> BatchItem:
        if not any((self.working_file_url, self.frames, self.audio_url, self.article_text)):
            raise ValueError("an item must contain at least one processable input")
        if self.media_type == MediaType.AUDIO and self.audio_url is None:
            raise ValueError("audio items require audio_url")
        return self


class WorkerInput(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    batch_id: Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")]
    batch_type: BatchType
    priority: Priority
    deadline_at: datetime | None = None
    items: tuple[BatchItem, ...] = Field(min_length=1, max_length=32)

    @model_validator(mode="after")
    def input_ids_are_unique(self) -> WorkerInput:
        ids = [item.input_id for item in self.items]
        if len(ids) != len(set(ids)):
            raise ValueError("input_id values must be unique inside a batch")
        if sum(len(item.frames) for item in self.items) > 256:
            raise ValueError("a batch may contain at most 256 extracted frames")
        return self


class TranscriptSegment(StrictModel):
    segment_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
    start_s: float = Field(ge=0)
    end_s: float = Field(gt=0)
    text: str = Field(min_length=1, max_length=10_000)
    uncertain: bool = False

    @model_validator(mode="after")
    def end_follows_start(self) -> TranscriptSegment:
        if self.end_s <= self.start_s:
            raise ValueError("transcript segment end_s must be after start_s")
        return self


class Transcript(StrictModel):
    language: str | None = Field(default=None, max_length=16)
    segments: tuple[TranscriptSegment, ...] = Field(default=(), max_length=10_000)


class PixelRegion(StrictModel):
    region_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
    evidence_id: str
    label: str = Field(min_length=1, max_length=128)
    bbox_normalized: tuple[float, float, float, float]
    task: Literal["fire_detection", "phrase_grounding", "ocr"]
    model_score: float | None = Field(default=None, ge=0, le=1)

    @model_validator(mode="after")
    def valid_bbox(self) -> PixelRegion:
        x1, y1, x2, y2 = self.bbox_normalized
        if not all(0 <= coordinate <= 1 for coordinate in self.bbox_normalized):
            raise ValueError("bbox_normalized coordinates must be between 0 and 1")
        if x2 <= x1 or y2 <= y1:
            raise ValueError("bbox_normalized must have a positive area")
        return self


class VisualEvidenceSelection(StrictModel):
    evidence_id: str = Field(min_length=1, max_length=128)
    selected_for_grounding: bool
    selection_reason: Literal[
        "single_image",
        "target_detection",
        "temporal_coverage",
        "detector_fallback",
        "capacity_limit",
    ]
    max_detection_score: float | None = Field(default=None, ge=0, le=1)


EvidenceKind = Literal["frame", "image", "transcript_segment", "article_text", "metadata"]


class FactualObservation(StrictModel):
    type: str = Field(min_length=1, max_length=128)
    evidence_kind: EvidenceKind
    evidence_id: str = Field(min_length=1, max_length=128)
    region_id: str | None = Field(default=None, max_length=128)
    description: str = Field(min_length=1, max_length=1_000)
    certainty: Literal["directly_visible", "explicitly_written", "explicitly_spoken"]


class ExplicitLiteral(StrictModel):
    literal: str = Field(min_length=1, max_length=500)
    evidence_kind: EvidenceKind
    evidence_id: str = Field(min_length=1, max_length=128)


class MetadataResult(StrictModel):
    capture_location_available: bool
    capture_location_origin: LocationOrigin | None = None


class GeographicMarkerCandidate(StrictModel):
    type: Literal["media_capture"]
    geometry_origin: LocationOrigin


class ItemResult(StrictModel):
    input_id: str
    metadata_result: MetadataResult
    transcript: Transcript = Field(default_factory=Transcript)
    pixel_regions: tuple[PixelRegion, ...] = ()
    visual_evidence_selection: tuple[VisualEvidenceSelection, ...] = ()
    factual_observations: tuple[FactualObservation, ...] = ()
    explicit_places: tuple[ExplicitLiteral, ...] = ()
    explicit_times: tuple[ExplicitLiteral, ...] = ()
    location_status: LocationStatus
    geographic_marker_candidate: GeographicMarkerCandidate | None = None
    observed_phenomenon_marker: None = None
    requires_human_review: Literal[True] = True


class ModelRun(StrictModel):
    model_role: Literal["asr", "fire_detection", "visual_grounding", "multimodal_extraction"]
    model_id: str
    revision: str
    status: Literal["succeeded", "failed", "skipped"]
    started_at: datetime
    finished_at: datetime
    load_ms: int = Field(ge=0)
    inference_ms: int = Field(ge=0)
    peak_vram_bytes: int | None = Field(default=None, ge=0)
    error_code: str | None = None


class WorkerOutput(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    batch_id: str
    status: Literal["succeeded", "partial_failure", "failed"]
    retryable: bool
    model_runs: tuple[ModelRun, ...]
    items: tuple[ItemResult, ...]
    validation_errors: tuple[str, ...] = ()
    boot_ms: int = Field(ge=0)


SafeIdentifierV2 = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")]
Sha256HexV2 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


def _is_timezone_aware_v2(value: datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None


def _json_sha256_v2(value: JsonValue) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return sha256(encoded).hexdigest()


class AnalysisWindowV2(StrictModel):
    analysis_id: SafeIdentifierV2
    fire_id: str = Field(pattern=r"^FR-[0-9A-Z]{2,3}-[0-9]{5}$")
    episode_id: SafeIdentifierV2
    window_start_at: datetime
    window_end_at: datetime
    local_date: date
    timezone: str = Field(min_length=3, max_length=64)

    @model_validator(mode="after")
    def validate_window(self) -> AnalysisWindowV2:
        if not all(
            _is_timezone_aware_v2(value) for value in (self.window_start_at, self.window_end_at)
        ):
            raise ValueError("analysis window datetimes must include a timezone")
        if self.window_end_at <= self.window_start_at:
            raise ValueError("analysis window end must follow its start")
        return self


class SourceProvenanceV2(StrictModel):
    source_key: SafeIdentifierV2
    source_reference_url: AnyHttpUrl | None = None
    license_identifier: str = Field(min_length=1, max_length=128)
    attribution: str | None = Field(default=None, max_length=500)
    trust: Literal["unverified", "partner", "institutional", "operator"]
    source_registry_version: str | None = Field(default=None, min_length=3, max_length=64)
    source_policy_domain: str | None = Field(default=None, min_length=3, max_length=253)
    source_kind: (
        Literal[
            "authority",
            "emergency_service",
            "satellite",
            "weather",
            "air_quality",
            "context",
            "directory",
            "press",
        ]
        | None
    ) = None
    source_confidence: Literal["A+", "A", "B", "lead"] | None = None
    publication_policy: (
        Literal[
            "facts_with_attribution",
            "dataset_license_required",
            "per_item_license_check",
            "private_analysis_only",
        ]
        | None
    ) = None
    claim_types: tuple[str, ...] = Field(default=(), max_length=32)
    declared_observation: DeclaredObservationV2 | None = None


class CameraMetadataV2(StrictModel):
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    orthometric_height_m: float | None = Field(default=None, allow_inf_nan=False)
    horizontal_accuracy_m: float | None = Field(default=None, gt=0, le=100_000)
    yaw_deg: float | None = Field(default=None, ge=0, lt=360)
    pitch_deg: float | None = Field(default=None, ge=-90, le=90)
    roll_deg: float | None = Field(default=None, ge=-180, le=180)
    horizontal_fov_deg: float | None = Field(default=None, gt=0, lt=180)
    image_width_px: int | None = Field(default=None, gt=0, le=200_000)
    image_height_px: int | None = Field(default=None, gt=0, le=200_000)
    pose_origin: (
        Literal["METADATA", "USER_DECLARED", "CROSS_VIEW_ESTIMATE", "HUMAN_CONFIRMED"] | None
    ) = None

    @model_validator(mode="after")
    def validate_camera(self) -> CameraMetadataV2:
        if (self.latitude is None) != (self.longitude is None):
            raise ValueError("camera latitude and longitude must be supplied together")
        position_details = (
            self.orthometric_height_m,
            self.horizontal_accuracy_m,
            self.pose_origin,
        )
        if self.latitude is None and any(value is not None for value in position_details):
            raise ValueError("camera position details require coordinates")
        if self.latitude is not None and self.pose_origin is None:
            raise ValueError("camera coordinates require pose_origin")
        orientation = (self.yaw_deg, self.pitch_deg, self.roll_deg)
        if any(value is not None for value in orientation) and not all(
            value is not None for value in orientation
        ):
            raise ValueError("camera orientation must provide yaw, pitch, and roll together")
        intrinsics = (self.horizontal_fov_deg, self.image_width_px, self.image_height_px)
        if any(value is not None for value in intrinsics) and not all(
            value is not None for value in intrinsics
        ):
            raise ValueError("camera intrinsics require field of view and image dimensions")
        return self


class SatelliteMetadataV2(StrictModel):
    product_id: SafeIdentifierV2
    provider: str = Field(min_length=1, max_length=128)
    acquired_at: datetime
    crs: str = Field(min_length=3, max_length=128)
    raster_width_px: int = Field(gt=0, le=500_000)
    raster_height_px: int = Field(gt=0, le=500_000)
    geotransform: tuple[float, float, float, float, float, float]
    bbox_wgs84: tuple[float, float, float, float]
    resolution_m: float = Field(gt=0, le=100_000)
    bands: tuple[str, ...] = Field(min_length=1, max_length=32)
    cloud_cover_percent: float | None = Field(default=None, ge=0, le=100)

    @model_validator(mode="after")
    def validate_bbox(self) -> SatelliteMetadataV2:
        if not _is_timezone_aware_v2(self.acquired_at):
            raise ValueError("satellite acquisition time must include a timezone")
        if not all(isfinite(value) for value in self.geotransform):
            raise ValueError("satellite geotransform values must be finite")
        min_lon, min_lat, max_lon, max_lat = self.bbox_wgs84
        if not (-180 <= min_lon < max_lon <= 180 and -90 <= min_lat < max_lat <= 90):
            raise ValueError("satellite bbox must be an ordered WGS84 extent")
        if len(self.bands) != len(set(self.bands)):
            raise ValueError("satellite band names must be unique")
        return self


class HotspotMetadataV2(StrictModel):
    product_id: SafeIdentifierV2
    provider: str = Field(min_length=1, max_length=128)
    acquired_at: datetime
    sensor_names: tuple[str, ...] = Field(min_length=1, max_length=16)
    resolution_m: float = Field(gt=0, le=100_000)
    bbox_wgs84: tuple[float, float, float, float]

    @model_validator(mode="after")
    def validate_hotspots(self) -> HotspotMetadataV2:
        if not _is_timezone_aware_v2(self.acquired_at):
            raise ValueError("hotspot acquisition time must include a timezone")
        if len(self.sensor_names) != len(set(self.sensor_names)):
            raise ValueError("hotspot sensor names must be unique")
        min_lon, min_lat, max_lon, max_lat = self.bbox_wgs84
        if not (-180 <= min_lon < max_lon <= 180 and -90 <= min_lat < max_lat <= 90):
            raise ValueError("hotspot bbox must be an ordered WGS84 extent")
        return self


class SpatialReferenceAssetV2(StrictModel):
    kind: Literal[
        "terrain_mnt",
        "surface_dsm",
        "orthophoto",
        "scene_catalog",
        "source_manifest",
    ]
    working_file_url: AnyHttpUrl
    sha256: Sha256HexV2
    crs: str = Field(min_length=3, max_length=128)
    resolution_m: float | None = Field(default=None, gt=0, le=100_000)


class SpatialReferenceBundleV2(StrictModel):
    reference_id: SafeIdentifierV2
    manifest_sha256: Sha256HexV2
    assets: tuple[SpatialReferenceAssetV2, ...] = Field(min_length=1, max_length=8)

    @model_validator(mode="after")
    def validate_assets(self) -> SpatialReferenceBundleV2:
        kinds = [asset.kind for asset in self.assets]
        if len(kinds) != len(set(kinds)):
            raise ValueError("spatial reference asset kinds must be unique")
        return self


class WorkerBatchItemV2(StrictModel):
    input_id: SafeIdentifierV2
    media_type: MediaType
    working_file_url: AnyHttpUrl | None = None
    provenance: SourceProvenanceV2
    captured_at: datetime | None = None
    camera: CameraMetadataV2 | None = None
    satellite: SatelliteMetadataV2 | None = None
    hotspot: HotspotMetadataV2 | None = None
    frames: tuple[FrameInput, ...] = Field(default=(), max_length=64)
    audio_url: AnyHttpUrl | None = None
    article_text: str | None = Field(default=None, max_length=100_000)

    @model_validator(mode="after")
    def validate_media_shape(self) -> WorkerBatchItemV2:
        if self.captured_at is not None and not _is_timezone_aware_v2(self.captured_at):
            raise ValueError("media capture time must include a timezone")
        if not any((self.working_file_url, self.frames, self.audio_url, self.article_text)):
            raise ValueError("a v2 media item requires processable content")
        if self.media_type == MediaType.AUDIO and self.audio_url is None:
            raise ValueError("audio items require audio_url")
        if self.media_type == MediaType.SATELLITE_IMAGE:
            if self.satellite is None:
                raise ValueError("satellite images require satellite metadata")
            if self.camera is not None:
                raise ValueError("satellite images cannot carry terrestrial camera metadata")
            if self.hotspot is not None:
                raise ValueError("satellite images cannot carry hotspot metadata")
        elif self.media_type == MediaType.SATELLITE_DATA:
            if self.hotspot is None or self.article_text is None:
                raise ValueError("satellite data require hotspot metadata and GeoJSON text")
            if self.camera is not None or self.satellite is not None:
                raise ValueError("satellite data cannot carry image or camera metadata")
        elif self.satellite is not None:
            raise ValueError("satellite metadata is reserved for satellite images")
        elif self.hotspot is not None:
            raise ValueError("hotspot metadata is reserved for satellite data")
        if self.camera is not None and self.media_type not in {MediaType.IMAGE, MediaType.VIDEO}:
            raise ValueError("camera metadata is reserved for images and videos")
        return self


class WorkerInputV2(StrictModel):
    schema_version: Literal["2.0"] = "2.0"
    batch_id: SafeIdentifierV2
    batch_type: BatchType
    priority: Priority
    analysis_window: AnalysisWindowV2
    deadline_at: datetime | None = None
    reference_bundle: SpatialReferenceBundleV2 | None = None
    items: tuple[WorkerBatchItemV2, ...] = Field(min_length=1, max_length=32)

    @model_validator(mode="after")
    def validate_items(self) -> WorkerInputV2:
        if self.deadline_at is not None and not _is_timezone_aware_v2(self.deadline_at):
            raise ValueError("deadline_at must include a timezone")
        input_ids = [item.input_id for item in self.items]
        if len(input_ids) != len(set(input_ids)):
            raise ValueError("input_id values must be unique")
        if sum(len(item.frames) for item in self.items) > 256:
            raise ValueError("a batch may contain at most 256 frames")
        satellite_types = {MediaType.SATELLITE_IMAGE, MediaType.SATELLITE_DATA}
        has_satellite = any(item.media_type in satellite_types for item in self.items)
        if self.batch_type == BatchType.SATELLITE_MEDIA and not all(
            item.media_type in satellite_types for item in self.items
        ):
            raise ValueError("satellite batches may contain only satellite images or data")
        if self.batch_type != BatchType.SATELLITE_MEDIA and has_satellite:
            raise ValueError("satellite inputs require a satellite batch")
        return self


SourceSemanticAnchorV2 = Literal[
    "active_fire_point",
    "visible_fire_front_point",
    "visible_fire_front",
    "smoke_column_base",
    "smoke_origin_point",
    "burned_area_polygon",
]
SpatialProposalKindV2 = Literal[
    "active_fire_point",
    "smoke_origin_point",
    "visible_fire_front",
    "probable_activity_envelope",
    "burned_area_polygon",
    "legacy_ground_point",
]


class SourceAnnotationV2(StrictModel):
    annotation_id: SafeIdentifierV2
    evidence_id: SafeIdentifierV2
    evidence_kind: Literal["image", "frame", "satellite_image"]
    semantic_anchor: SourceSemanticAnchorV2
    source_point_normalized: tuple[float, float] | None = None
    source_geometry_normalized: dict[str, object] | None = None
    model_score: float | None = Field(default=None, ge=0, le=1)

    @model_validator(mode="after")
    def validate_source_geometry(self) -> SourceAnnotationV2:
        point_semantics = {
            "active_fire_point",
            "visible_fire_front_point",
            "smoke_column_base",
            "smoke_origin_point",
        }
        allowed_types = (
            {"Point"}
            if self.semantic_anchor in point_semantics
            else (
                {"LineString", "MultiLineString"}
                if self.semantic_anchor == "visible_fire_front"
                else {"Polygon", "MultiPolygon"}
            )
        )
        geometry = self.source_geometry_normalized
        if geometry is None:
            if self.source_point_normalized is None:
                raise ValueError("source annotation requires normalized source geometry")
            geometry = {
                "type": "Point",
                "coordinates": list(self.source_point_normalized),
            }
            object.__setattr__(self, "source_geometry_normalized", geometry)
        validated = validate_geojson_geometry(
            geometry,
            allowed_types=allowed_types,
            normalized=True,
        )
        if self.source_point_normalized is not None:
            if validated["type"] != "Point":
                raise ValueError("source_point_normalized is only compatible with Point geometry")
            coordinates = validated["coordinates"]
            assert isinstance(coordinates, list | tuple)
            if tuple(float(value) for value in coordinates[:2]) != self.source_point_normalized:
                raise ValueError(
                    "source point and source geometry must reference the same position"
                )
        elif validated["type"] == "Point":
            coordinates = validated["coordinates"]
            assert isinstance(coordinates, list | tuple)
            object.__setattr__(
                self,
                "source_point_normalized",
                (float(coordinates[0]), float(coordinates[1])),
            )
        return self


class SpatialProposalV2(StrictModel):
    proposal_id: SafeIdentifierV2
    annotation_id: SafeIdentifierV2 | None = None
    status: Literal["ground_point", "projected_geometry", "insufficient_geometry"]
    proposal_kind: SpatialProposalKindV2 | None = None
    observed_at: datetime | None = None
    geometry_origin: (
        Literal[
            "SATELLITE_GEOTRANSFORM",
            "CAMERA_RAYCAST",
            "CROSS_VIEW_RAYCAST",
            "EXPLICIT_SOURCE_GEOMETRY",
        ]
        | None
    ) = None
    longitude: float | None = Field(default=None, ge=-180, le=180)
    latitude: float | None = Field(default=None, ge=-90, le=90)
    altitude_m: float | None = Field(default=None, allow_inf_nan=False)
    geometry_geojson: dict[str, object] | None = None
    horizontal_accuracy_m: float | None = Field(default=None, gt=0, le=100_000)
    reference_bundle_sha256: Sha256HexV2 | None = None
    uncertainty_codes: tuple[SafeIdentifierV2, ...] = Field(default=(), max_length=12)

    @model_validator(mode="after")
    def validate_projection(self) -> SpatialProposalV2:
        if self.observed_at is not None and not _is_timezone_aware_v2(self.observed_at):
            raise ValueError("spatial observation time must include a timezone")
        if self.status == "ground_point":
            projected = (
                self.geometry_origin,
                self.longitude,
                self.latitude,
                self.horizontal_accuracy_m,
                self.reference_bundle_sha256,
            )
            if self.annotation_id is None or not all(value is not None for value in projected):
                raise ValueError("ground_point requires sourced coordinates and accuracy")
            if self.proposal_kind not in {None, "legacy_ground_point"}:
                raise ValueError("ground_point is reserved for the legacy point contract")
            object.__setattr__(self, "proposal_kind", "legacy_ground_point")
            geometry = self.geometry_geojson or {
                "type": "Point",
                "coordinates": [self.longitude, self.latitude],
            }
            validate_geojson_geometry(geometry, allowed_types={"Point"})
            object.__setattr__(self, "geometry_geojson", geometry)
        elif self.status == "projected_geometry":
            if (
                self.proposal_kind is None
                or self.proposal_kind == "legacy_ground_point"
                or self.geometry_origin is None
                or self.horizontal_accuracy_m is None
                or self.reference_bundle_sha256 is None
                or self.observed_at is None
                or self.geometry_geojson is None
            ):
                raise ValueError(
                    "projected_geometry requires kind, geometry, observation time, "
                    "origin, accuracy and reference"
                )
            if self.geometry_origin != "EXPLICIT_SOURCE_GEOMETRY" and self.annotation_id is None:
                raise ValueError("projected media geometry requires a source annotation")
            allowed_types = {
                "active_fire_point": {"Point"},
                "smoke_origin_point": {"Point"},
                "visible_fire_front": {"LineString", "MultiLineString"},
                "probable_activity_envelope": {"Polygon", "MultiPolygon"},
                "burned_area_polygon": {"Polygon", "MultiPolygon"},
            }[self.proposal_kind]
            geometry = validate_geojson_geometry(
                self.geometry_geojson,
                allowed_types=allowed_types,
            )
            if geometry["type"] == "Point":
                coordinates = geometry["coordinates"]
                assert isinstance(coordinates, list | tuple)
                point_longitude = float(coordinates[0])
                point_latitude = float(coordinates[1])
                if self.longitude is not None and self.longitude != point_longitude:
                    raise ValueError("point longitude must match geometry_geojson")
                if self.latitude is not None and self.latitude != point_latitude:
                    raise ValueError("point latitude must match geometry_geojson")
                object.__setattr__(self, "longitude", point_longitude)
                object.__setattr__(self, "latitude", point_latitude)
            elif any(
                value is not None for value in (self.longitude, self.latitude, self.altitude_m)
            ):
                raise ValueError("non-point geometries cannot use legacy point coordinates")
        else:
            if any(
                value is not None
                for value in (
                    self.proposal_kind,
                    self.geometry_origin,
                    self.longitude,
                    self.latitude,
                    self.altitude_m,
                    self.geometry_geojson,
                    self.horizontal_accuracy_m,
                )
            ):
                raise ValueError("insufficient_geometry cannot contain projected coordinates")
            if not self.uncertainty_codes:
                raise ValueError("insufficient_geometry requires an uncertainty code")
        return self


class FactProposalV2(StrictModel):
    fact_id: SafeIdentifierV2
    input_id: SafeIdentifierV2
    category: Literal[
        "fire_activity",
        "burned_area",
        "resources",
        "evacuation",
        "access",
        "infrastructure",
        "weather",
        "other",
    ]
    fact_key: SafeIdentifierV2
    as_of: datetime
    evidence_kind: Literal[
        "frame", "image", "satellite_image", "transcript_segment", "article_text", "metadata"
    ]
    evidence_id: SafeIdentifierV2
    certainty: Literal["directly_visible", "explicitly_written", "explicitly_spoken"]
    value_number: float | None = Field(default=None, allow_inf_nan=False)
    value_text: str | None = Field(default=None, min_length=1, max_length=2_000)
    value_boolean: bool | None = None
    unit: str | None = Field(default=None, min_length=1, max_length=64)
    summary: str = Field(min_length=1, max_length=1_000)
    conflict_group_id: SafeIdentifierV2 | None = None

    @model_validator(mode="after")
    def validate_value(self) -> FactProposalV2:
        if not _is_timezone_aware_v2(self.as_of):
            raise ValueError("fact as_of must include a timezone")
        supplied = sum(
            value is not None for value in (self.value_number, self.value_text, self.value_boolean)
        )
        if supplied != 1:
            raise ValueError("a fact requires exactly one typed value")
        if self.unit is not None and self.value_number is None:
            raise ValueError("fact units are reserved for numeric values")
        return self


class ReportSectionV2(StrictModel):
    key: Literal[
        "situation",
        "observed_activity",
        "probable_activity_zone",
        "resources",
        "impacts",
        "sources_and_freshness",
        "limitations",
    ]
    heading: str = Field(min_length=1, max_length=120)
    body: str = Field(min_length=1, max_length=5_000)
    fact_ids: tuple[SafeIdentifierV2, ...] = Field(default=(), max_length=200)
    basis_codes: tuple[SafeIdentifierV2, ...] = Field(default=(), max_length=50)

    @model_validator(mode="after")
    def validate_basis(self) -> ReportSectionV2:
        if not self.fact_ids and not self.basis_codes:
            raise ValueError("report sections require a fact or an explicit basis code")
        return self


class SituationReportDraftV2(StrictModel):
    title: str = Field(min_length=1, max_length=255)
    body_markdown: str = Field(min_length=1, max_length=30_000)
    sections: tuple[ReportSectionV2, ...] = Field(min_length=1, max_length=12)

    @model_validator(mode="after")
    def validate_sections(self) -> SituationReportDraftV2:
        keys = [section.key for section in self.sections]
        if len(keys) != len(set(keys)):
            raise ValueError("report section keys must be unique")
        for section in self.sections:
            if len(section.fact_ids) != len(set(section.fact_ids)):
                raise ValueError("report section fact references must be unique")
        return self


class WorkerItemResultV2(StrictModel):
    input_id: SafeIdentifierV2
    transcript: Transcript = Field(default_factory=Transcript)
    pixel_regions: tuple[PixelRegion, ...] = Field(default=(), max_length=512)
    visual_evidence_selection: tuple[VisualEvidenceSelection, ...] = Field(
        default=(), max_length=256
    )
    source_annotations: tuple[SourceAnnotationV2, ...] = Field(default=(), max_length=512)
    spatial_proposals: tuple[SpatialProposalV2, ...] = Field(default=(), max_length=512)
    fact_proposals: tuple[FactProposalV2, ...] = Field(default=(), max_length=512)
    explicit_places: tuple[ExplicitLiteral, ...] = Field(default=(), max_length=512)
    explicit_times: tuple[ExplicitLiteral, ...] = Field(default=(), max_length=512)
    requires_human_review: Literal[True] = True

    @model_validator(mode="after")
    def validate_references(self) -> WorkerItemResultV2:
        annotation_ids = [item.annotation_id for item in self.source_annotations]
        proposal_ids = [item.proposal_id for item in self.spatial_proposals]
        fact_ids = [item.fact_id for item in self.fact_proposals]
        for label, values in (
            ("annotation", annotation_ids),
            ("spatial proposal", proposal_ids),
            ("fact", fact_ids),
        ):
            if len(values) != len(set(values)):
                raise ValueError(f"duplicate {label} identifier")
        known_annotations = set(annotation_ids)
        if any(
            item.annotation_id is not None and item.annotation_id not in known_annotations
            for item in self.spatial_proposals
        ):
            raise ValueError("spatial proposal references an unknown annotation")
        if any(item.input_id != self.input_id for item in self.fact_proposals):
            raise ValueError("fact proposal input_id must match its item result")
        return self


StageRoleV2 = Literal[
    "source_research",
    "asr",
    "fire_detection",
    "visual_grounding",
    "multimodal_extraction",
    "fire_pointing",
    "burned_area",
    "cross_view_registration",
    "spatial_projection",
    "evidence_fusion",
    "situation_report",
]
WorkerModelRoleV2 = Literal[
    "asr",
    "visual_filtering",
    "visual_grounding",
    "multimodal_extraction",
    "fire_pointing",
    "burned_area",
    "cross_view_registration",
    "consensus_judge",
]


class WorkerModelRunV2(StrictModel):
    model_role: WorkerModelRoleV2
    model_id: str
    revision: str
    status: Literal["succeeded", "failed", "skipped"]
    started_at: datetime
    finished_at: datetime
    load_ms: int = Field(ge=0)
    inference_ms: int = Field(ge=0)
    peak_vram_bytes: int | None = Field(default=None, ge=0)
    error_code: str | None = None

    @model_validator(mode="after")
    def validate_timing(self) -> WorkerModelRunV2:
        if not all(_is_timezone_aware_v2(value) for value in (self.started_at, self.finished_at)):
            raise ValueError("model run datetimes must include a timezone")
        if self.finished_at < self.started_at:
            raise ValueError("model run finish must not precede its start")
        return self


class WorkerModelCandidateRunV2(StrictModel):
    candidate_id: SafeIdentifierV2
    candidate_rank: int = Field(ge=1, le=8)
    stage_role: StageRoleV2
    model_role: WorkerModelRoleV2
    model_id: str = Field(min_length=1, max_length=512)
    revision: str = Field(min_length=1, max_length=128)
    status: Literal["succeeded", "failed", "skipped"]
    started_at: datetime
    finished_at: datetime
    load_ms: int = Field(ge=0)
    inference_ms: int = Field(ge=0)
    peak_vram_bytes: int | None = Field(default=None, ge=0)
    repaired: bool = False
    output_digest: Sha256HexV2 | None = None
    output_payload: dict[str, JsonValue] | None = None
    error_code: SafeIdentifierV2 | None = None

    @model_validator(mode="after")
    def validate_candidate_run(self) -> WorkerModelCandidateRunV2:
        if not all(_is_timezone_aware_v2(value) for value in (self.started_at, self.finished_at)):
            raise ValueError("candidate run datetimes must include a timezone")
        if self.finished_at < self.started_at:
            raise ValueError("candidate run finish must not precede its start")
        if self.status == "succeeded":
            if self.output_payload is None or self.output_digest is None:
                raise ValueError("a successful candidate run requires its private output")
            if _json_sha256_v2(self.output_payload) != self.output_digest:
                raise ValueError("candidate output digest does not match its payload")
            if self.error_code is not None:
                raise ValueError("a successful candidate run cannot contain an error")
        elif self.output_payload is not None or self.output_digest is not None:
            raise ValueError("an unsuccessful candidate run cannot publish an output")
        if self.repaired and self.status != "succeeded":
            raise ValueError("only a successful candidate run can be repaired")
        if self.status == "failed" and self.error_code is None:
            raise ValueError("a failed candidate run requires an error code")
        return self


class WorkerConsensusResultV2(StrictModel):
    consensus_id: SafeIdentifierV2
    stage_role: StageRoleV2
    strategy: Literal["single_with_rules", "cascade", "quorum"]
    decision: Literal["pass", "repair", "adjudicated", "abstain", "human_review"]
    candidate_ids: tuple[SafeIdentifierV2, ...] = Field(min_length=1, max_length=8)
    selected_candidate_id: SafeIdentifierV2 | None = None
    adjudicator_candidate_id: SafeIdentifierV2 | None = None
    reason_codes: tuple[SafeIdentifierV2, ...] = Field(min_length=1, max_length=16)
    successful_candidates: int = Field(ge=0, le=8)
    required_successful: int = Field(ge=1, le=8)
    agreement_score: float | None = Field(default=None, ge=0, le=1)
    agreement_threshold: float = Field(ge=0, le=1)
    downstream_allowed: bool
    comparison_digest: Sha256HexV2
    comparison_payload: dict[str, JsonValue]
    evaluated_at: datetime

    @model_validator(mode="after")
    def validate_consensus(self) -> WorkerConsensusResultV2:
        if not _is_timezone_aware_v2(self.evaluated_at):
            raise ValueError("consensus evaluation time must include a timezone")
        if len(self.candidate_ids) != len(set(self.candidate_ids)):
            raise ValueError("consensus candidate ids must be unique")
        if self.adjudicator_candidate_id in self.candidate_ids:
            raise ValueError("the adjudicator cannot be a stage output candidate")
        if len(self.reason_codes) != len(set(self.reason_codes)):
            raise ValueError("consensus reason codes must be unique")
        if self.successful_candidates > len(self.candidate_ids):
            raise ValueError("successful candidate count exceeds evaluated candidates")
        if self.required_successful > len(self.candidate_ids):
            raise ValueError("required candidate count exceeds evaluated candidates")
        if _json_sha256_v2(self.comparison_payload) != self.comparison_digest:
            raise ValueError("consensus comparison digest does not match its payload")
        if self.decision in {"pass", "repair", "adjudicated"}:
            if self.selected_candidate_id not in self.candidate_ids:
                raise ValueError("an accepted consensus requires a selected candidate")
            if not self.downstream_allowed:
                raise ValueError("an accepted consensus must allow downstream execution")
            if self.decision == "adjudicated" and self.adjudicator_candidate_id is None:
                raise ValueError("an adjudicated consensus requires its judge run")
            if self.decision != "adjudicated" and self.adjudicator_candidate_id is not None:
                raise ValueError("a direct consensus cannot reference a judge run")
        elif self.selected_candidate_id is not None or self.downstream_allowed:
            raise ValueError("a blocked consensus cannot select or release a candidate")
        return self


class WorkerStageGateV2(StrictModel):
    phase: Literal["preflight", "postflight"]
    decision: Literal[
        "pass",
        "not_applicable",
        "abstain",
        "human_review",
        "failed_retryable",
        "failed_terminal",
    ]
    reason_codes: tuple[SafeIdentifierV2, ...] = Field(min_length=1, max_length=16)
    available_capabilities: tuple[SafeIdentifierV2, ...] = Field(default=(), max_length=32)
    missing_capabilities: tuple[SafeIdentifierV2, ...] = Field(default=(), max_length=32)
    downstream_possible: bool

    @model_validator(mode="after")
    def validate_unique_values(self) -> WorkerStageGateV2:
        for values in (
            self.reason_codes,
            self.available_capabilities,
            self.missing_capabilities,
        ):
            if len(values) != len(set(values)):
                raise ValueError("stage gate values must be unique")
        return self


class WorkerStageAttemptV2(StrictModel):
    attempt: int = Field(ge=1, le=2)
    kind: Literal["initial", "repair"]
    status: Literal["succeeded", "failed"]
    started_at: datetime
    finished_at: datetime
    inference_ms: int = Field(ge=0)
    peak_vram_bytes: int | None = Field(default=None, ge=0)
    error_code: SafeIdentifierV2 | None = None

    @model_validator(mode="after")
    def validate_attempt(self) -> WorkerStageAttemptV2:
        if not all(_is_timezone_aware_v2(value) for value in (self.started_at, self.finished_at)):
            raise ValueError("stage attempt datetimes must include a timezone")
        if self.finished_at < self.started_at:
            raise ValueError("stage attempt finish must not precede its start")
        if self.kind == "initial" and self.attempt != 1:
            raise ValueError("the initial stage attempt must be attempt 1")
        if self.kind == "repair" and self.attempt != 2:
            raise ValueError("a stage repair must be attempt 2")
        if self.status == "failed" and self.error_code is None:
            raise ValueError("a failed stage attempt requires an error_code")
        if self.status == "succeeded" and self.error_code is not None:
            raise ValueError("a succeeded stage attempt cannot contain an error_code")
        return self


class WorkerStageTraceV2(StrictModel):
    stage_role: StageRoleV2
    contract_id: Annotated[str, Field(pattern=r"^stage\.[a-z0-9_]+\.v[0-9]+$")]
    sequence: int = Field(ge=1, le=10)
    status: Literal["succeeded", "failed", "skipped"]
    retryable: bool
    preflight: WorkerStageGateV2
    postflight: WorkerStageGateV2 | None = None
    attempts: tuple[WorkerStageAttemptV2, ...] = Field(default=(), max_length=2)

    @model_validator(mode="after")
    def validate_trace(self) -> WorkerStageTraceV2:
        if not self.contract_id.startswith(f"stage.{self.stage_role}.v"):
            raise ValueError("stage trace contract_id must match its stage_role")
        if self.preflight.phase != "preflight":
            raise ValueError("stage trace preflight must use the preflight phase")
        if self.postflight is not None and self.postflight.phase != "postflight":
            raise ValueError("stage trace postflight must use the postflight phase")
        if [attempt.attempt for attempt in self.attempts] != list(range(1, len(self.attempts) + 1)):
            raise ValueError("stage attempts must be consecutive and ordered")
        if len(self.attempts) == 2:
            first, repair = self.attempts
            if first.status != "failed" or repair.kind != "repair":
                raise ValueError("a repair requires one failed initial attempt")
        if self.status == "skipped" and self.attempts:
            raise ValueError("a skipped stage cannot contain inference attempts")
        if self.status == "succeeded" and (
            not self.attempts or self.attempts[-1].status != "succeeded"
        ):
            raise ValueError("a succeeded stage requires a final successful attempt")
        if self.status == "failed" and self.attempts and self.attempts[-1].status != "failed":
            raise ValueError("a failed stage cannot end with a successful attempt")
        expected_retryable = self.preflight.decision == "failed_retryable" or (
            self.postflight is not None and self.postflight.decision == "failed_retryable"
        )
        if self.retryable != expected_retryable:
            raise ValueError("stage retryable must match its gate decisions")
        return self


class WorkerOutputV2(StrictModel):
    schema_version: Literal["2.0"] = "2.0"
    batch_id: SafeIdentifierV2
    analysis_id: SafeIdentifierV2
    status: Literal["succeeded", "partial_failure", "failed"]
    retryable: bool
    orchestration_contract_digest: Sha256HexV2
    stage_traces: tuple[WorkerStageTraceV2, ...] = Field(default=(), max_length=10)
    model_runs: tuple[WorkerModelRunV2, ...] = Field(max_length=8)
    candidate_runs: tuple[WorkerModelCandidateRunV2, ...] = Field(default=(), max_length=32)
    consensus_results: tuple[WorkerConsensusResultV2, ...] = Field(default=(), max_length=10)
    items: tuple[WorkerItemResultV2, ...] = Field(min_length=1, max_length=32)
    report_draft: SituationReportDraftV2 | None = None
    validation_errors: tuple[str, ...] = Field(default=(), max_length=64)
    boot_ms: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_output_references(self) -> WorkerOutputV2:
        if self.status != "failed" and not self.stage_traces:
            raise ValueError("a non-failed worker v2 output requires stage traces")
        stage_roles = [trace.stage_role for trace in self.stage_traces]
        stage_sequences = [trace.sequence for trace in self.stage_traces]
        if len(stage_roles) != len(set(stage_roles)):
            raise ValueError("worker v2 output contains duplicate stage roles")
        if len(stage_sequences) != len(set(stage_sequences)):
            raise ValueError("worker v2 output contains duplicate stage sequences")
        if stage_sequences != list(range(1, len(stage_sequences) + 1)):
            raise ValueError("worker v2 stage traces must be consecutive and ordered")
        candidate_ids = [run.candidate_id for run in self.candidate_runs]
        consensus_ids = [result.consensus_id for result in self.consensus_results]
        consensus_stages = [result.stage_role for result in self.consensus_results]
        if len(candidate_ids) != len(set(candidate_ids)):
            raise ValueError("worker v2 output contains duplicate candidate ids")
        if len(consensus_ids) != len(set(consensus_ids)):
            raise ValueError("worker v2 output contains duplicate consensus ids")
        if len(consensus_stages) != len(set(consensus_stages)):
            raise ValueError("worker v2 output contains duplicate consensus stages")
        if bool(self.candidate_runs) != bool(self.consensus_results):
            raise ValueError("candidate runs and consensus results must be supplied together")
        candidates_by_id = {run.candidate_id: run for run in self.candidate_runs}
        covered_candidates: set[str] = set()
        traces_by_stage = {trace.stage_role: trace for trace in self.stage_traces}
        model_runs_by_role = {run.model_role: run for run in self.model_runs}
        if len(model_runs_by_role) != len(self.model_runs):
            raise ValueError("worker v2 output contains duplicate selected model roles")
        model_role_by_stage: dict[StageRoleV2, WorkerModelRoleV2] = {
            "asr": "asr",
            "fire_detection": "visual_filtering",
            "visual_grounding": "visual_grounding",
            "multimodal_extraction": "multimodal_extraction",
            "fire_pointing": "fire_pointing",
            "burned_area": "burned_area",
            "cross_view_registration": "cross_view_registration",
        }
        for result in self.consensus_results:
            candidates = [
                candidates_by_id.get(candidate_id) for candidate_id in result.candidate_ids
            ]
            if any(candidate is None for candidate in candidates):
                raise ValueError("consensus references an unknown candidate run")
            if any(
                candidate.stage_role != result.stage_role for candidate in candidates if candidate
            ):
                raise ValueError("consensus candidate belongs to another stage")
            adjudicator = (
                candidates_by_id.get(result.adjudicator_candidate_id)
                if result.adjudicator_candidate_id is not None
                else None
            )
            if result.adjudicator_candidate_id is not None and adjudicator is None:
                raise ValueError("consensus references an unknown adjudicator run")
            if adjudicator is not None and (
                adjudicator.stage_role != result.stage_role
                or adjudicator.model_role != "consensus_judge"
            ):
                raise ValueError("consensus adjudicator has an invalid role")
            if (
                result.decision == "adjudicated"
                and adjudicator is not None
                and adjudicator.status != "succeeded"
            ):
                raise ValueError("an adjudicated consensus requires a successful judge")
            covered_ids = set(result.candidate_ids)
            if result.adjudicator_candidate_id is not None:
                covered_ids.add(result.adjudicator_candidate_id)
            if covered_candidates.intersection(covered_ids):
                raise ValueError("a candidate run cannot belong to multiple consensus results")
            covered_candidates.update(covered_ids)
            trace = traces_by_stage.get(result.stage_role)
            if trace is None:
                raise ValueError("consensus result has no matching stage trace")
            if result.downstream_allowed and trace.status != "succeeded":
                raise ValueError("accepted consensus requires a successful stage trace")
            if not result.downstream_allowed and trace.status == "succeeded":
                raise ValueError("blocked consensus cannot have a successful stage trace")
            if result.selected_candidate_id is not None:
                selected = candidates_by_id[result.selected_candidate_id]
                if selected.status != "succeeded":
                    raise ValueError("consensus selected an unsuccessful candidate")
                model_role = model_role_by_stage.get(result.stage_role)
                selected_run = model_runs_by_role.get(model_role) if model_role else None
                if (
                    selected_run is None
                    or selected_run.model_id != selected.model_id
                    or selected_run.revision != selected.revision
                ):
                    raise ValueError("selected model run does not match consensus candidate")
        if covered_candidates != set(candidate_ids):
            raise ValueError("every candidate run must be covered by one consensus result")
        input_ids = [item.input_id for item in self.items]
        if len(input_ids) != len(set(input_ids)):
            raise ValueError("worker v2 output contains duplicate input_id values")
        all_fact_ids = [fact.fact_id for item in self.items for fact in item.fact_proposals]
        if len(all_fact_ids) != len(set(all_fact_ids)):
            raise ValueError("worker v2 output contains duplicate fact identifiers")
        if self.report_draft is not None:
            referenced = {
                fact_id for section in self.report_draft.sections for fact_id in section.fact_ids
            }
            unknown = referenced - set(all_fact_ids)
            if unknown:
                raise ValueError(f"report references an unknown fact: {sorted(unknown)[0]}")
        return self


class ResearchUploadV1(StrictModel):
    pathname_prefix: str = Field(min_length=3, max_length=512)
    upload_grant: str = Field(min_length=64, max_length=4_096)
    token_endpoint: AnyHttpUrl
    resource_id: SafeIdentifierV2
    maximum_file_size_bytes: int = Field(gt=0)
    allowed_content_types: tuple[str, ...] = Field(min_length=1, max_length=32)


class ResearchSourcePolicyV1(StrictModel):
    source_name: str = Field(min_length=1, max_length=255)
    kind: Literal[
        "authority",
        "emergency_service",
        "satellite",
        "weather",
        "air_quality",
        "context",
        "directory",
        "press",
    ]
    scope: Literal["national", "regional", "departmental", "local", "global"]
    confidence_level: Literal["A+", "A", "B", "lead"]
    claim_types: tuple[str, ...] = Field(min_length=1, max_length=32)
    publication_policy: Literal[
        "facts_with_attribution",
        "dataset_license_required",
        "per_item_license_check",
        "private_analysis_only",
    ]
    minimum_refresh_minutes: int = Field(ge=1, le=43_200)


class ResearchInputV1(StrictModel):
    schema_version: Literal["research-1.0"] = "research-1.0"
    operation: Literal["source_research"] = "source_research"
    research_id: SafeIdentifierV2
    analysis_window: AnalysisWindowV2
    incident_name: str | None = Field(default=None, max_length=255)
    incident_reference: tuple[float, float]
    cutoff_at: datetime
    location_hint: str | None = Field(default=None, max_length=500)
    source_registry_version: str = Field(min_length=3, max_length=64)
    allowed_domains: tuple[str, ...] = Field(min_length=1, max_length=200)
    source_policies: dict[str, ResearchSourcePolicyV1]
    search_templates: dict[str, AnyHttpUrl]
    max_fetch_bytes: int = Field(gt=0, le=104_857_600)
    request_timeout_seconds: int = Field(ge=2, le=120)
    private_upload: ResearchUploadV1

    @model_validator(mode="after")
    def validate_research(self) -> ResearchInputV1:
        if not _is_timezone_aware_v2(self.cutoff_at):
            raise ValueError("research cutoff_at must include a timezone")
        if self.cutoff_at != self.analysis_window.window_end_at:
            raise ValueError("research cutoff must equal the analysis window end")
        if len(self.allowed_domains) != len(set(self.allowed_domains)):
            raise ValueError("research domains must be unique")
        if set(self.source_policies) != set(self.allowed_domains):
            raise ValueError("every research domain requires one source policy")
        if not self.search_templates:
            raise ValueError("at least one search provider is required")
        if set(self.search_templates) & set(self.allowed_domains):
            raise ValueError("search providers must be separate from source domains")
        longitude, latitude = self.incident_reference
        if not (-180 <= longitude <= 180 and -90 <= latitude <= 90):
            raise ValueError("incident reference must be WGS84")
        return self


class ResearchCandidateV1(StrictModel):
    candidate_id: SafeIdentifierV2
    canonical_url: AnyHttpUrl
    source_domain: str = Field(min_length=1, max_length=255)
    title: str | None = Field(default=None, max_length=500)
    published_at: datetime | None = None
    acquired_at: datetime | None = None
    media_type: MediaType | None = None
    blob_pathname: str | None = Field(default=None, min_length=3, max_length=1_024)
    media_sha256: Sha256HexV2 | None = None
    size_bytes: int | None = Field(default=None, gt=0, le=1_073_741_824)
    excerpt: str | None = Field(default=None, max_length=100_000)
    license_identifier: str | None = Field(default=None, max_length=128)
    attribution: str | None = Field(default=None, max_length=500)
    provenance: dict[str, object] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_candidate(self) -> ResearchCandidateV1:
        for timestamp in (self.published_at, self.acquired_at):
            if timestamp is not None and not _is_timezone_aware_v2(timestamp):
                raise ValueError("candidate timestamps must include a timezone")
        stored_fields = (self.blob_pathname, self.media_sha256, self.size_bytes)
        if any(value is not None for value in stored_fields) and not all(
            value is not None for value in stored_fields
        ):
            raise ValueError("stored candidate media requires path, hash, and size")
        if self.media_type == MediaType.ARTICLE and not self.excerpt:
            raise ValueError("article candidates require extracted text")
        if self.media_type not in {None, MediaType.ARTICLE} and self.blob_pathname is None:
            raise ValueError("media candidates require a private uploaded object")
        return self


class ResearchModelRunV1(StrictModel):
    model_role: Literal["source_research"] = "source_research"
    model_id: str
    revision: str
    status: Literal["succeeded", "failed", "skipped"]
    started_at: datetime
    finished_at: datetime
    load_ms: int = Field(ge=0)
    inference_ms: int = Field(ge=0)
    peak_vram_bytes: int | None = Field(default=None, ge=0)
    error_code: str | None = None


class ResearchOutputV1(StrictModel):
    schema_version: Literal["research-1.0"] = "research-1.0"
    research_id: SafeIdentifierV2
    status: Literal["succeeded", "partial_failure", "failed"]
    retryable: bool
    model_run: ResearchModelRunV1
    queries: tuple[str, ...] = Field(default=(), max_length=100)
    candidates: tuple[ResearchCandidateV1, ...] = Field(default=(), max_length=500)
    validation_errors: tuple[str, ...] = Field(default=(), max_length=100)

    @model_validator(mode="after")
    def validate_output(self) -> ResearchOutputV1:
        ids = [candidate.candidate_id for candidate in self.candidates]
        if len(ids) != len(set(ids)):
            raise ValueError("research candidate ids must be unique")
        return self
