from __future__ import annotations

import math
from datetime import date
from typing import Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from fireviewer_contracts.backend.geometry_contract import validate_geojson_geometry
from fireviewer_contracts.backend.part4_framing_schemas import (
    FramingArtifactReference,
    Part4FramingReceiptV1,
    Part4SpatialContextV1,
)


class FireStateStrictModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        populate_by_name=True,
    )


SpatialObservationKind = Literal[
    "thermal_footprint",
    "burned_probability",
    "active_probability",
    "valid_negative",
    "camera_ray",
    "camera_ground_intersection",
    "official_perimeter",
    "modelled_perimeter",
]
FireStateTarget = Literal["affected", "active", "both"]
DailyFireQuality = Literal["observed", "fused", "interpolated", "insufficient"]
SpatialProvenanceBandName = Literal[
    "observed_support",
    "multi_source_support",
    "prior_interpolated_support",
    "uncertainty_support",
]


class FusionProfileIdentityV1(FireStateStrictModel):
    schema_name: Literal["fireviewer.part4-fusion-profile-identity.v1"] = Field(
        default="fireviewer.part4-fusion-profile-identity.v1",
        alias="schema",
    )
    profile_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
    profile_version: str = Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$")
    profile_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    calibration_state: Literal["uncalibrated", "calibrated"]


class FusionGridProfileV1(FireStateStrictModel):
    resolutions_m: tuple[float, ...] = Field(min_length=1, max_length=16)
    max_cells: int = Field(ge=1, le=4_000_000)
    aoi_margin_m: float = Field(ge=0, le=100_000, allow_inf_nan=False)

    @model_validator(mode="after")
    def validate_grid_profile(self) -> FusionGridProfileV1:
        if any(
            value <= 0 or value > 250 or not math.isfinite(value) for value in self.resolutions_m
        ):
            raise ValueError("fusion profile grid resolutions must be finite and within bounds")
        if tuple(sorted(set(self.resolutions_m))) != self.resolutions_m:
            raise ValueError("fusion profile grid resolutions must be unique and increasing")
        return self


class FusionThresholdProfileV1(FireStateStrictModel):
    affected: float = Field(gt=0, lt=1, allow_inf_nan=False)
    active: float = Field(gt=0, lt=1, allow_inf_nan=False)
    uncertainty_low: float = Field(gt=0, lt=1, allow_inf_nan=False)
    uncertainty_high: float = Field(gt=0, lt=1, allow_inf_nan=False)

    @model_validator(mode="after")
    def validate_thresholds(self) -> FusionThresholdProfileV1:
        if self.uncertainty_low >= self.uncertainty_high:
            raise ValueError("fusion profile uncertainty thresholds must be increasing")
        return self


class FusionTemporalProfileV1(FireStateStrictModel):
    active_half_life_hours: float = Field(gt=0, le=720, allow_inf_nan=False)
    propagation_rate_m_per_hour: float = Field(ge=0, le=20_000, allow_inf_nan=False)
    max_propagation_m: float = Field(ge=0, le=100_000, allow_inf_nan=False)


class FusionPriorProfileV1(FireStateStrictModel):
    background_probability: float = Field(gt=0, lt=0.5, allow_inf_nan=False)
    affected_probability: float = Field(gt=0.5, lt=1, allow_inf_nan=False)
    active_probability: float = Field(gt=0.5, lt=1, allow_inf_nan=False)
    propagated_active_probability: float = Field(gt=0, lt=0.5, allow_inf_nan=False)


class FusionUncertaintyProfileV1(FireStateStrictModel):
    halo_base_probability: float = Field(gt=0, lt=0.5, allow_inf_nan=False)
    halo_probability_scale: float = Field(ge=0, lt=0.5, allow_inf_nan=False)


class FusionTransferProfileV1(FireStateStrictModel):
    profile_probability: float = Field(gt=0.5, lt=1, allow_inf_nan=False)
    weight: float = Field(gt=0, le=20, allow_inf_nan=False)


class FusionNegativeProfileV1(FireStateStrictModel):
    minimum_reduction_factor: float = Field(gt=0, le=1, allow_inf_nan=False)
    probability_floor: float = Field(gt=0, lt=0.5, allow_inf_nan=False)


class SensorFusionProfileV1(FireStateStrictModel):
    profile_probability: float = Field(gt=0.5, lt=1, allow_inf_nan=False)
    weight: float = Field(gt=0, le=20, allow_inf_nan=False)


class FusionProfileV1(FireStateStrictModel):
    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        populate_by_name=True,
        frozen=True,
    )

    schema_name: Literal["fireviewer.part4-fusion-profile.v1"] = Field(
        default="fireviewer.part4-fusion-profile.v1",
        alias="schema",
    )
    profile_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
    profile_version: str = Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$")
    algorithm_id: Literal["fireviewer.part4.spatiotemporal-probability-fusion"]
    algorithm_versions: tuple[str, ...] = Field(min_length=1, max_length=32)
    calibration_state: Literal["uncalibrated", "calibrated"]
    grid: FusionGridProfileV1
    thresholds: FusionThresholdProfileV1
    temporal: FusionTemporalProfileV1
    priors: FusionPriorProfileV1
    uncertainty: FusionUncertaintyProfileV1
    active_to_affected: FusionTransferProfileV1
    valid_negative: FusionNegativeProfileV1
    sensors: dict[SpatialObservationKind, SensorFusionProfileV1]
    profile_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_profile(self) -> FusionProfileV1:
        expected = {
            "thermal_footprint",
            "burned_probability",
            "active_probability",
            "valid_negative",
            "camera_ray",
            "camera_ground_intersection",
            "official_perimeter",
            "modelled_perimeter",
        }
        if set(self.sensors) != expected:
            raise ValueError("fusion profile must define every spatial observation kind")
        if len(self.algorithm_versions) != len(set(self.algorithm_versions)):
            raise ValueError("fusion profile algorithm versions must be unique")
        return self

    def identity(self) -> FusionProfileIdentityV1:
        return FusionProfileIdentityV1(
            profile_id=self.profile_id,
            profile_version=self.profile_version,
            profile_sha256=self.profile_sha256,
            calibration_state=self.calibration_state,
        )


class SensorContributionV1(FireStateStrictModel):
    source_family_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$")
    observation_ids: tuple[str, ...] = Field(min_length=1, max_length=2_048)
    observation_kinds: tuple[SpatialObservationKind, ...] = Field(min_length=1, max_length=16)
    lineage_count: int = Field(ge=1, le=2_048)
    supported_fraction: float = Field(ge=0, le=1)
    maximum_raw_probability: float = Field(ge=0, le=1)
    maximum_observability_probability: float = Field(ge=0, le=1)
    calibration_state: Literal["uncalibrated", "calibrated"]

    @model_validator(mode="after")
    def validate_contribution(self) -> SensorContributionV1:
        if len(self.observation_ids) != len(set(self.observation_ids)):
            raise ValueError("sensor contribution observation identifiers must be unique")
        if len(self.observation_kinds) != len(set(self.observation_kinds)):
            raise ValueError("sensor contribution observation kinds must be unique")
        return self


class SpatialObservationV2(FireStateStrictModel):
    schema_name: Literal["fireviewer.spatial-observation.v2"] = Field(
        default="fireviewer.spatial-observation.v2",
        alias="schema",
    )
    observation_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$")
    observation_kind: SpatialObservationKind
    target_state: FireStateTarget
    observed_at: AwareDatetime
    observed_end_at: AwareDatetime | None = None
    available_at: AwareDatetime | None = None
    geometry_geojson: dict[str, Any] | None = None
    raster_uri: str | None = Field(default=None, min_length=8, max_length=2_048)
    coverage_geojson: dict[str, Any] | None = None
    probability: float = Field(ge=0, le=1, allow_inf_nan=False)
    observability_probability: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)
    resolution_m: float | None = Field(default=None, gt=0, le=100_000)
    horizontal_accuracy_m: float | None = Field(default=None, gt=0, le=1_000_000)
    source_family_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$")
    lineage_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$")
    upstream_product_id: str = Field(min_length=1, max_length=512)
    source_revision_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    processor_revision: str = Field(min_length=1, max_length=128)
    calibration_state: Literal["uncalibrated", "calibrated"] = "uncalibrated"
    evidence_refs: tuple[str, ...] = Field(default=(), max_length=512)
    published_reference_accessed: Literal[False] = False

    @model_validator(mode="after")
    def validate_observation(self) -> SpatialObservationV2:
        if self.observed_end_at is not None and self.observed_end_at < self.observed_at:
            raise ValueError("observed_end_at must not precede observed_at")
        if (
            self.geometry_geojson is None
            and self.raster_uri is None
            and self.coverage_geojson is None
        ):
            raise ValueError("a spatial observation requires geometry, coverage or a raster URI")
        if self.geometry_geojson is not None:
            self.geometry_geojson = validate_geojson_geometry(self.geometry_geojson)
        if self.coverage_geojson is not None:
            self.coverage_geojson = validate_geojson_geometry(self.coverage_geojson)
        if self.observation_kind in {"thermal_footprint", "valid_negative"} and (
            self.target_state != "active"
        ):
            raise ValueError(f"{self.observation_kind} observations target active fire only")
        if self.observation_kind == "burned_probability" and self.target_state != "affected":
            raise ValueError("burned probability observations target affected area only")
        if self.observation_kind == "thermal_footprint":
            if self.geometry_geojson is None:
                raise ValueError("thermal footprint observations require a geometry")
            if self.horizontal_accuracy_m is None and self.resolution_m is None:
                raise ValueError("thermal footprint observations require spatial accuracy")
        if self.observation_kind == "valid_negative":
            if self.coverage_geojson is None or self.observability_probability is None:
                raise ValueError("valid negative observations require observable coverage")
            if self.observability_probability <= 0:
                raise ValueError("valid negative observability must be positive")
        if self.observation_kind in {"camera_ray", "camera_ground_intersection"}:
            if self.target_state != "active":
                raise ValueError("camera observations target active fire only")
            if self.geometry_geojson is None or self.horizontal_accuracy_m is None:
                raise ValueError("camera observations require projected geometry and accuracy")
        if self.observation_kind in {"official_perimeter", "modelled_perimeter"}:
            geometry_type = (
                self.geometry_geojson.get("type") if self.geometry_geojson is not None else None
            )
            if geometry_type not in {"Polygon", "MultiPolygon"}:
                raise ValueError("perimeter observations require polygonal geometry")
        if len(self.evidence_refs) != len(set(self.evidence_refs)):
            raise ValueError("spatial observation evidence references must be unique")
        return self


class FireProbabilityGridReference(FireStateStrictModel):
    schema_name: Literal["fireviewer.fire-probability-grid.v1"] = Field(
        default="fireviewer.fire-probability-grid.v1",
        alias="schema",
    )
    cog_uri: str = Field(min_length=8, max_length=2_048)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    byte_count: int = Field(gt=0)
    crs: Literal["EPSG:2154"] = "EPSG:2154"
    width: int = Field(gt=0, le=4_000_000)
    height: int = Field(gt=0, le=4_000_000)
    resolution_m: float = Field(gt=0, le=250)
    transform: tuple[float, float, float, float, float, float]
    band_mapping: dict[Literal["affected", "active", "observable"], int]
    persistence: Literal["durable_immutable"] = "durable_immutable"

    @model_validator(mode="after")
    def validate_grid(self) -> FireProbabilityGridReference:
        if set(self.band_mapping) != {"affected", "active", "observable"}:
            raise ValueError("all three fire probability bands are required")
        if len(set(self.band_mapping.values())) != 3:
            raise ValueError("fire probability bands must be distinct")
        if self.width * self.height > 4_000_000:
            raise ValueError("fire probability grid exceeds the cell budget")
        return self


class FireSpatialProvenanceGridReference(FireStateStrictModel):
    schema_name: Literal["fireviewer.fire-spatial-provenance-grid.v1"] = Field(
        default="fireviewer.fire-spatial-provenance-grid.v1",
        alias="schema",
    )
    cog_uri: str = Field(min_length=8, max_length=2_048)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    byte_count: int = Field(gt=0)
    crs: Literal["EPSG:2154"] = "EPSG:2154"
    width: int = Field(gt=0, le=4_000_000)
    height: int = Field(gt=0, le=4_000_000)
    resolution_m: float = Field(gt=0, le=250)
    transform: tuple[float, float, float, float, float, float]
    band_mapping: dict[SpatialProvenanceBandName, int]
    encoding: Literal["support_probability_and_masks_v1"] = "support_probability_and_masks_v1"
    persistence: Literal["durable_immutable"] = "durable_immutable"

    @model_validator(mode="after")
    def validate_provenance_grid(self) -> FireSpatialProvenanceGridReference:
        expected = {
            "observed_support",
            "multi_source_support",
            "prior_interpolated_support",
            "uncertainty_support",
        }
        if set(self.band_mapping) != expected:
            raise ValueError("all four spatial provenance bands are required")
        if set(self.band_mapping.values()) != {1, 2, 3, 4}:
            raise ValueError("spatial provenance bands must map exactly to bands 1 through 4")
        if self.width * self.height > 4_000_000:
            raise ValueError("spatial provenance grid exceeds the cell budget")
        return self


class PerimeterEstimateV3(FireStateStrictModel):
    schema_name: Literal["fireviewer.perimeter-estimate.v3"] = Field(
        default="fireviewer.perimeter-estimate.v3",
        alias="schema",
    )
    incident_id: str = Field(min_length=3, max_length=128)
    local_date: date
    status: DailyFireQuality
    affected: dict[str, Any] | None = None
    active: dict[str, Any] | None = None
    uncertainty_band: dict[str, Any] | None = None
    resolution_m: float | None = Field(default=None, gt=0, le=250)
    observed_fraction: float = Field(ge=0, le=1)
    fused_fraction: float = Field(ge=0, le=1)
    interpolated_fraction: float = Field(ge=0, le=1)
    observable_fraction: float | None = Field(default=None, ge=0, le=1)
    uncertainty_fraction: float | None = Field(default=None, ge=0, le=1)
    evidence_strength: float = Field(ge=0, le=1)
    confidence_calibrated: float | None = Field(default=None, ge=0, le=1)
    calibration_state: Literal["uncalibrated", "calibrated"]
    fusion_profile: FusionProfileIdentityV1 | None = None
    source_family_ids: tuple[str, ...] = Field(default=(), max_length=512)
    contradiction_codes: tuple[str, ...] = Field(default=(), max_length=128)
    evidence_refs: tuple[str, ...] = Field(default=(), max_length=20_000)
    eligible_for_automatic_publication: bool = False
    needs_human_review: bool = True
    geometry_mutation_allowed: Literal[False] = False
    published_reference_accessed: Literal[False] = False
    framing: Part4FramingReceiptV1 | None = None

    @model_validator(mode="after")
    def validate_release_policy(self) -> PerimeterEstimateV3:
        for field_name in ("affected", "active", "uncertainty_band"):
            value = getattr(self, field_name)
            if value is not None:
                setattr(self, field_name, validate_geojson_geometry(value))
        if self.status == "insufficient" and self.active is not None:
            raise ValueError("insufficient estimates cannot claim an active perimeter")
        if self.calibration_state == "uncalibrated" and self.confidence_calibrated is not None:
            raise ValueError("uncalibrated estimates cannot expose calibrated confidence")
        if (
            self.fusion_profile is not None
            and self.fusion_profile.calibration_state != self.calibration_state
        ):
            raise ValueError("perimeter estimate and fusion profile calibration states differ")
        if self.eligible_for_automatic_publication:
            eligible = (
                self.status in {"observed", "fused"}
                and self.calibration_state == "calibrated"
                and self.confidence_calibrated is not None
                and self.confidence_calibrated > 0.85
                and not self.contradiction_codes
                and not self.needs_human_review
            )
            if not eligible:
                raise ValueError("automatic publication policy is not satisfied")
        collections = (self.source_family_ids, self.contradiction_codes, self.evidence_refs)
        if any(len(values) != len(set(values)) for values in collections):
            raise ValueError("perimeter estimate references must be unique")
        return self


class DailyFireStateV2(FireStateStrictModel):
    schema_name: Literal["fireviewer.daily-fire-state.v2"] = Field(
        default="fireviewer.daily-fire-state.v2",
        alias="schema",
    )
    state_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$")
    incident_id: str = Field(min_length=3, max_length=128)
    episode_id: str | None = Field(default=None, min_length=3, max_length=128)
    local_date: date
    status: DailyFireQuality
    grid: FireProbabilityGridReference | None = None
    provenance_grid: FireSpatialProvenanceGridReference | None = None
    perimeter: PerimeterEstimateV3
    prior_state_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    latest_observation_at: AwareDatetime | None = None
    latest_observation_age_seconds: int | None = Field(default=None, ge=0)
    source_observation_ids: tuple[str, ...] = Field(default=(), max_length=10_000)
    source_family_ids: tuple[str, ...] = Field(default=(), max_length=512)
    contributions: tuple[SensorContributionV1, ...] = Field(default=(), max_length=512)
    contradiction_codes: tuple[str, ...] = Field(default=(), max_length=128)
    algorithm_id: str = Field(min_length=3, max_length=128)
    algorithm_version: str = Field(min_length=1, max_length=64)
    fusion_profile: FusionProfileIdentityV1 | None = None
    source_input_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    published_reference_accessed: Literal[False] = False
    state_valid_at: AwareDatetime | None = None
    spatial_context: Part4SpatialContextV1 | None = None
    framing: Part4FramingReceiptV1 | None = None
    lineage_artifact: FramingArtifactReference | None = None

    @model_validator(mode="after")
    def validate_state(self) -> DailyFireStateV2:
        if self.perimeter.incident_id != self.incident_id:
            raise ValueError("daily state and perimeter incident identifiers differ")
        if self.perimeter.local_date != self.local_date or self.perimeter.status != self.status:
            raise ValueError("daily state and perimeter status/date differ")
        version_parts = self.algorithm_version.split(".")
        profile_required = (
            self.algorithm_id == "fireviewer.part4.spatiotemporal-probability-fusion"
            and len(version_parts) == 3
            and all(item.isdigit() for item in version_parts)
            and tuple(int(item) for item in version_parts) >= (3, 1, 0)
        )
        if profile_required and self.fusion_profile is None:
            raise ValueError("Part.4 3.1 and later daily states require a fusion profile")
        if self.fusion_profile != self.perimeter.fusion_profile:
            raise ValueError("daily state and perimeter fusion profile identities differ")
        if self.framing != self.perimeter.framing:
            raise ValueError("daily state and perimeter framing receipts differ")
        if self.algorithm_version.startswith("3.3."):
            if self.spatial_context is None or self.state_valid_at is None:
                raise ValueError("Part.4 3.3 requires a dated spatial context")
            if self.spatial_context.seed.incident_id != self.incident_id:
                raise ValueError("spatial seed belongs to another incident")
            if self.prior_state_sha256 != self.spatial_context.parent_state_sha256:
                raise ValueError("state parent differs from the frozen context")
        if (self.grid is None) != (self.provenance_grid is None):
            version = tuple(int(item) for item in version_parts) if profile_required else (0, 0, 0)
            if version >= (3, 2, 0):
                raise ValueError(
                    "Part.4 3.2 persisted states require probability and provenance grids together"
                )
        if (
            self.grid is not None
            and self.provenance_grid is not None
            and (
                self.grid.crs != self.provenance_grid.crs
                or self.grid.width != self.provenance_grid.width
                or self.grid.height != self.provenance_grid.height
                or self.grid.resolution_m != self.provenance_grid.resolution_m
                or self.grid.transform != self.provenance_grid.transform
            )
        ):
            raise ValueError("probability and spatial provenance grids are not aligned")
        if len(self.source_observation_ids) != len(set(self.source_observation_ids)):
            raise ValueError("daily state observation identifiers must be unique")
        if len(self.source_family_ids) != len(set(self.source_family_ids)):
            raise ValueError("daily state source families must be unique")
        if self.contributions and (
            tuple(item.source_family_id for item in self.contributions) != self.source_family_ids
        ):
            raise ValueError("daily state contributions must match ordered source families")
        if self.status != "insufficient" and self.source_family_ids and not self.contributions:
            raise ValueError("non-insufficient daily states require sensor contributions")
        if len(self.contradiction_codes) != len(set(self.contradiction_codes)):
            raise ValueError("daily state contradiction codes must be unique")
        return self


class GeometryCorrectionProposalV1(FireStateStrictModel):
    schema_name: Literal["fireviewer.geometry-correction-proposal.v1"] = Field(
        default="fireviewer.geometry-correction-proposal.v1",
        alias="schema",
    )
    correction_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$")
    incident_id: str = Field(min_length=3, max_length=128)
    local_date: date
    source_perimeter_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    competing_geometry_geojson: dict[str, Any]
    component: Literal["affected", "active"] = "affected"
    reason_codes: tuple[str, ...] = Field(min_length=1, max_length=128)
    evidence_refs: tuple[str, ...] = Field(default=(), max_length=512)
    relationship: Literal["competes_with_source"] = "competes_with_source"
    state: Literal["proposed"] = "proposed"
    source_mutation_allowed: Literal[False] = False

    @model_validator(mode="after")
    def validate_proposal(self) -> GeometryCorrectionProposalV1:
        self.competing_geometry_geojson = validate_geojson_geometry(self.competing_geometry_geojson)
        if len(self.reason_codes) != len(set(self.reason_codes)):
            raise ValueError("geometry correction reason codes must be unique")
        if len(self.evidence_refs) != len(set(self.evidence_refs)):
            raise ValueError("geometry correction evidence references must be unique")
        return self


__all__ = [
    "DailyFireQuality",
    "DailyFireStateV2",
    "FireProbabilityGridReference",
    "FireSpatialProvenanceGridReference",
    "FusionProfileIdentityV1",
    "FusionProfileV1",
    "GeometryCorrectionProposalV1",
    "PerimeterEstimateV3",
    "SensorContributionV1",
    "SpatialObservationKind",
    "SpatialObservationV2",
    "SpatialProvenanceBandName",
]
