"""Strict contracts for isolated Part.4 calibration and component qualification."""

from __future__ import annotations

import math
from datetime import datetime
from typing import Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from fireviewer_contracts.backend.fire_state_schemas import SpatialObservationV2
from fireviewer_contracts.backend.geometry_contract import validate_geojson_geometry


class Part4CalibrationStrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, populate_by_name=True)


class HuggingFaceArtifactRefV1(Part4CalibrationStrictModel):
    """Remote artifact identity without requiring a user-managed digest."""

    schema_name: Literal["fireviewer.hugging-face-artifact-ref.v1"] = Field(
        default="fireviewer.hugging-face-artifact-ref.v1",
        alias="schema",
    )
    repo_id: str = Field(pattern=r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
    repo_type: Literal["dataset", "model"]
    revision: str = Field(min_length=7, max_length=128)
    path: str = Field(min_length=1, max_length=1_024)
    byte_count: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_remote_identity(self) -> HuggingFaceArtifactRefV1:
        if self.revision.casefold() in {"main", "master", "latest"}:
            raise ValueError("Hugging Face artifacts require an immutable revision")
        normalized = self.path.replace("\\", "/")
        if normalized.startswith("/") or ".." in normalized.split("/"):
            raise ValueError("Hugging Face artifact paths must be repository-relative")
        self.path = normalized
        return self


ReferenceComponent = Literal["affected", "active"]
ReferenceGrade = Literal["A", "B", "C"]


class Part4ReferenceSnapshotV1(Part4CalibrationStrictModel):
    schema_name: Literal["fireviewer.part4-reference-snapshot.v1"] = Field(
        default="fireviewer.part4-reference-snapshot.v1",
        alias="schema",
    )
    reference_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$")
    incident_id: str = Field(min_length=3, max_length=128)
    episode_id: str | None = Field(default=None, min_length=3, max_length=128)
    component: ReferenceComponent
    geometry_geojson: dict[str, Any] | None = None
    raster: HuggingFaceArtifactRefV1 | None = None
    valid_at: AwareDatetime
    temporal_accuracy_seconds: int = Field(gt=0, le=31_536_000)
    spatial_accuracy_m: float = Field(gt=0, le=100_000, allow_inf_nan=False)
    resolution_m: float = Field(gt=0, le=100_000, allow_inf_nan=False)
    provider: str = Field(min_length=2, max_length=128)
    product: str = Field(min_length=2, max_length=256)
    licence: str = Field(min_length=2, max_length=256)
    source_revision: str = Field(min_length=1, max_length=256)
    grade: ReferenceGrade
    forbidden_input_refs: tuple[str, ...] = Field(min_length=1, max_length=2_048)

    @model_validator(mode="after")
    def validate_reference(self) -> Part4ReferenceSnapshotV1:
        if (self.geometry_geojson is None) == (self.raster is None):
            raise ValueError("a reference snapshot requires exactly one geometry or raster")
        if self.geometry_geojson is not None:
            self.geometry_geojson = validate_geojson_geometry(self.geometry_geojson)
        if len(self.forbidden_input_refs) != len(set(self.forbidden_input_refs)):
            raise ValueError("forbidden reference identifiers must be unique")
        if self.grade == "A" and (
            self.temporal_accuracy_seconds > 3_600 or self.spatial_accuracy_m > 100
        ):
            raise ValueError("grade A requires temporal accuracy <= 1 h and spatial <= 100 m")
        if self.grade == "B" and (
            self.component != "affected"
            or self.temporal_accuracy_seconds > 86_400
            or self.resolution_m > 300
        ):
            raise ValueError("grade B is affected-only with <= 24 h and <= 300 m resolution")
        return self


class Part4CalibrationCaseV1(Part4CalibrationStrictModel):
    schema_name: Literal["fireviewer.part4-calibration-case.v1"] = Field(
        default="fireviewer.part4-calibration-case.v1",
        alias="schema",
    )
    case_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$")
    incident_id: str = Field(min_length=3, max_length=128)
    episode_id: str | None = Field(default=None, min_length=3, max_length=128)
    evaluation_cutoff_at: AwareDatetime
    latest_input_at: AwareDatetime
    observations: HuggingFaceArtifactRefV1
    split: Literal["calibration", "validation", "holdout", "smoke"]
    split_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
    reference_ids: tuple[str, ...] = Field(min_length=1, max_length=32)
    forbidden_input_refs: tuple[str, ...] = Field(default=(), max_length=2_048)

    @model_validator(mode="after")
    def validate_case(self) -> Part4CalibrationCaseV1:
        if self.latest_input_at > self.evaluation_cutoff_at:
            raise ValueError("calibration cases cannot contain inputs newer than the cutoff")
        for values in (self.reference_ids, self.forbidden_input_refs):
            if len(values) != len(set(values)):
                raise ValueError("calibration case references must be unique")
        return self


class Part4ObservationBundleV1(Part4CalibrationStrictModel):
    """Normalized replay input stored before any reference geometry is opened."""

    schema_name: Literal["fireviewer.part4-observation-bundle.v1"] = Field(
        default="fireviewer.part4-observation-bundle.v1",
        alias="schema",
    )
    case_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$")
    incident_id: str = Field(min_length=3, max_length=128)
    episode_id: str | None = Field(default=None, min_length=3, max_length=128)
    evaluation_cutoff_at: AwareDatetime
    location_source_refs: tuple[str, ...] = Field(min_length=1, max_length=32)
    observations: tuple[SpatialObservationV2, ...] = Field(min_length=1, max_length=10_000)
    raw_provider_content_stored: Literal[False] = False

    @model_validator(mode="after")
    def validate_observation_bundle(self) -> Part4ObservationBundleV1:
        identifiers = [item.observation_id for item in self.observations]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("observation bundle identifiers must be unique")
        if any(item.observed_at > self.evaluation_cutoff_at for item in self.observations):
            raise ValueError("observation bundle contains future evidence")
        if len(self.location_source_refs) != len(set(self.location_source_refs)):
            raise ValueError("location source references must be unique")
        return self


class Part4CorpusCatalogRowV1(Part4CalibrationStrictModel):
    """One immutable case plus the separately committed reference artifact."""

    schema_name: Literal["fireviewer.part4-corpus-catalog-row.v1"] = Field(
        default="fireviewer.part4-corpus-catalog-row.v1",
        alias="schema",
    )
    case: Part4CalibrationCaseV1
    reference_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$")
    reference: HuggingFaceArtifactRefV1
    location_source_refs: tuple[str, ...] = Field(min_length=1, max_length=32)

    @model_validator(mode="after")
    def validate_catalog_row(self) -> Part4CorpusCatalogRowV1:
        if self.reference_id not in self.case.reference_ids:
            raise ValueError("catalog reference is not declared by its calibration case")
        if self.reference.repo_type != "dataset":
            raise ValueError("calibration references must live in a dataset repository")
        if not self.reference.path.startswith("references/"):
            raise ValueError("calibration reference path is outside the reference namespace")
        if self.location_source_refs != tuple(dict.fromkeys(self.location_source_refs)):
            raise ValueError("catalog location references must be unique")
        return self


class Part4CalibrationCampaignV1(Part4CalibrationStrictModel):
    schema_name: Literal["fireviewer.part4-calibration-campaign.v1"] = Field(
        default="fireviewer.part4-calibration-campaign.v1",
        alias="schema",
    )
    campaign_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$")
    created_at: AwareDatetime
    code_revision: str = Field(min_length=7, max_length=128)
    part4_algorithm_version: str = Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$")
    source_profile_id: str = Field(min_length=3, max_length=128)
    calibration_dataset_repo: str = Field(pattern=r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
    calibration_dataset_revision: str = Field(min_length=7, max_length=128)
    holdout_dataset_repo: str = Field(pattern=r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
    holdout_dataset_revision: str = Field(min_length=7, max_length=128)
    split_id: str = Field(min_length=3, max_length=128)
    parameter_space: dict[str, tuple[float, ...]]
    selected_parameters: dict[str, float] = Field(default_factory=dict)
    incident_count: int = Field(ge=0)
    snapshot_count: int = Field(ge=0)
    cpu_seconds: float = Field(ge=0, allow_inf_nan=False)
    estimated_cost_eur: float = Field(ge=0, allow_inf_nan=False)
    status: Literal["planned", "running", "completed", "failed"]

    @model_validator(mode="after")
    def validate_campaign(self) -> Part4CalibrationCampaignV1:
        if self.calibration_dataset_revision.casefold() in {"main", "master", "latest"}:
            raise ValueError("calibration dataset revision must be immutable")
        if self.holdout_dataset_revision.casefold() in {"main", "master", "latest"}:
            raise ValueError("holdout dataset revision must be immutable")
        for values in self.parameter_space.values():
            if not values or any(not math.isfinite(value) for value in values):
                raise ValueError("campaign parameter ranges must contain finite values")
        return self


class Part4ConfidenceCalibratorV1(Part4CalibrationStrictModel):
    schema_name: Literal["fireviewer.part4-confidence-calibrator.v1"] = Field(
        default="fireviewer.part4-confidence-calibrator.v1",
        alias="schema",
    )
    calibrator_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$")
    trained_at: AwareDatetime
    feature_names: tuple[str, ...] = Field(min_length=1, max_length=32)
    feature_means: tuple[float, ...]
    feature_scales: tuple[float, ...]
    coefficients: tuple[float, ...]
    intercept: float = Field(allow_inf_nan=False)
    regularization_l2: float = Field(gt=0, allow_inf_nan=False)
    fit_incident_count: int = Field(gt=0)
    fit_snapshot_count: int = Field(gt=0)

    @model_validator(mode="after")
    def validate_calibrator(self) -> Part4ConfidenceCalibratorV1:
        size = len(self.feature_names)
        if len(set(self.feature_names)) != size:
            raise ValueError("confidence feature names must be unique")
        if not (
            len(self.feature_means) == len(self.feature_scales) == len(self.coefficients) == size
        ):
            raise ValueError("confidence calibrator vectors must have identical dimensions")
        numeric = (*self.feature_means, *self.feature_scales, *self.coefficients)
        if any(not math.isfinite(value) for value in numeric):
            raise ValueError("confidence calibrator vectors must be finite")
        if any(value <= 0 for value in self.feature_scales):
            raise ValueError("confidence feature scales must be positive")
        return self


class Part4QualificationReceiptV1(Part4CalibrationStrictModel):
    schema_name: Literal["fireviewer.part4-qualification-receipt.v1"] = Field(
        default="fireviewer.part4-qualification-receipt.v1",
        alias="schema",
    )
    qualification_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$")
    evaluated_at: AwareDatetime
    component: Literal["affected"]
    fusion_profile_id: str = Field(min_length=3, max_length=128)
    calibrator_id: str = Field(min_length=3, max_length=160)
    campaign_id: str = Field(min_length=3, max_length=160)
    holdout_dataset_repo: str = Field(pattern=r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
    holdout_dataset_revision: str = Field(min_length=7, max_length=128)
    campaign_incident_count: int = Field(ge=0)
    campaign_snapshot_count: int = Field(ge=0)
    holdout_incident_count: int = Field(ge=0)
    holdout_snapshot_count: int = Field(ge=0)
    metrics: dict[str, float | int]
    gates: dict[str, bool]
    qualified: bool
    reference_leakage_detected: bool = False
    simulated_contribution_detected: bool = False

    @model_validator(mode="after")
    def validate_qualification(self) -> Part4QualificationReceiptV1:
        if self.holdout_dataset_revision.casefold() in {"main", "master", "latest"}:
            raise ValueError("qualification requires an immutable holdout revision")
        expected = bool(self.gates) and all(self.gates.values())
        expected = expected and not (
            self.reference_leakage_detected or self.simulated_contribution_detected
        )
        if self.qualified != expected:
            raise ValueError("qualification state must match every recorded gate")
        return self


class Part4PreHoldoutGateReceiptV1(Part4CalibrationStrictModel):
    """Records why a holdout stayed sealed before final qualification."""

    schema_name: Literal["fireviewer.part4-pre-holdout-gate-receipt.v1"] = Field(
        default="fireviewer.part4-pre-holdout-gate-receipt.v1",
        alias="schema",
    )
    receipt_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$")
    evaluated_at: AwareDatetime
    campaign_id: str = Field(min_length=3, max_length=160)
    calibration_dataset_repo: str = Field(pattern=r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
    calibration_dataset_revision: str = Field(min_length=7, max_length=128)
    split_id: str = Field(min_length=3, max_length=128)
    selected_fusion_profile_id: str = Field(min_length=3, max_length=128)
    selected_fusion_profile_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    incident_count: int = Field(ge=0)
    snapshot_count: int = Field(ge=0)
    calibration_incident_count: int = Field(ge=0)
    validation_incident_count: int = Field(ge=0)
    confidence_calibrator_fitted: bool
    metrics: dict[str, float | int]
    gates: dict[str, bool]
    holdout_opened: Literal[False] = False
    qualified: Literal[False] = False

    @model_validator(mode="after")
    def validate_pre_holdout_receipt(self) -> Part4PreHoldoutGateReceiptV1:
        if self.calibration_dataset_revision.casefold() in {"main", "master", "latest"}:
            raise ValueError("pre-holdout receipt requires an immutable dataset revision")
        if self.incident_count < self.calibration_incident_count + self.validation_incident_count:
            raise ValueError("split incident counts exceed the corpus incident count")
        if self.gates and all(self.gates.values()):
            raise ValueError("a passing pre-holdout receipt must continue in the holdout job")
        return self


class Part4ComponentCalibrationProfileV1(Part4CalibrationStrictModel):
    schema_name: Literal["fireviewer.part4-component-calibration-profile.v1"] = Field(
        default="fireviewer.part4-component-calibration-profile.v1",
        alias="schema",
    )
    profile_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$")
    profile_version: str = Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$")
    component: Literal["affected"]
    fusion_profile_id: str = Field(min_length=3, max_length=128)
    calibrator: Part4ConfidenceCalibratorV1
    campaign_id: str = Field(min_length=3, max_length=160)
    holdout_dataset_repo: str = Field(pattern=r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
    holdout_dataset_revision: str = Field(min_length=7, max_length=128)
    qualification: Part4QualificationReceiptV1

    @model_validator(mode="after")
    def validate_profile(self) -> Part4ComponentCalibrationProfileV1:
        receipt = self.qualification
        if not receipt.qualified or receipt.component != self.component:
            raise ValueError("component profiles require a qualified matching receipt")
        if receipt.fusion_profile_id != self.fusion_profile_id:
            raise ValueError("component profile and qualification fusion profiles differ")
        if receipt.calibrator_id != self.calibrator.calibrator_id:
            raise ValueError("component profile and qualification calibrators differ")
        if receipt.campaign_id != self.campaign_id:
            raise ValueError("component profile and qualification campaigns differ")
        if (
            receipt.holdout_dataset_repo != self.holdout_dataset_repo
            or receipt.holdout_dataset_revision != self.holdout_dataset_revision
        ):
            raise ValueError("component profile and qualification holdout identities differ")
        return self


class Part4ComponentReleaseDecisionV1(Part4CalibrationStrictModel):
    schema_name: Literal["fireviewer.part4-component-release-decision.v1"] = Field(
        default="fireviewer.part4-component-release-decision.v1",
        alias="schema",
    )
    decision_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$")
    decided_at: AwareDatetime
    perimeter_receipt_id: str = Field(min_length=3, max_length=256)
    incident_id: str = Field(min_length=3, max_length=128)
    episode_id: str | None = Field(default=None, min_length=3, max_length=128)
    component: Literal["affected"]
    qualification_revision: str = Field(min_length=7, max_length=256)
    confidence_calibrated: float = Field(ge=0, le=1, allow_inf_nan=False)
    reconstruction_status: Literal["observed", "fused", "interpolated", "insufficient"]
    reason_codes: tuple[str, ...] = Field(min_length=1, max_length=64)
    contradiction_codes: tuple[str, ...] = Field(default=(), max_length=128)
    simulated_contribution_detected: bool = False
    eligible_for_automatic_publication: bool = False

    @model_validator(mode="after")
    def validate_release(self) -> Part4ComponentReleaseDecisionV1:
        for values in (self.reason_codes, self.contradiction_codes):
            if len(values) != len(set(values)):
                raise ValueError("component release reasons and contradictions must be unique")
        expected = (
            self.confidence_calibrated > 0.85
            and self.reconstruction_status in {"observed", "fused"}
            and not self.contradiction_codes
            and not self.simulated_contribution_detected
        )
        if self.eligible_for_automatic_publication != expected:
            raise ValueError("component automatic-publication eligibility is inconsistent")
        return self


def ensure_aware(value: datetime) -> datetime:
    """Keep date parsing helpers fail-closed when used outside Pydantic."""

    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timezone-aware datetime required")
    return value


__all__ = [
    "HuggingFaceArtifactRefV1",
    "Part4CalibrationCampaignV1",
    "Part4CalibrationCaseV1",
    "Part4ComponentCalibrationProfileV1",
    "Part4ComponentReleaseDecisionV1",
    "Part4ConfidenceCalibratorV1",
    "Part4CorpusCatalogRowV1",
    "Part4ObservationBundleV1",
    "Part4PreHoldoutGateReceiptV1",
    "Part4QualificationReceiptV1",
    "Part4ReferenceSnapshotV1",
]
