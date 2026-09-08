from __future__ import annotations

from datetime import datetime
from math import isfinite
from typing import Literal

from pydantic import ConfigDict, Field, JsonValue, model_validator

from fireviewer_contracts.contracts import SafeIdentifierV2, Sha256HexV2, StrictModel


class SchemaContractModel(StrictModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        validate_by_alias=True,
        validate_by_name=True,
        serialize_by_alias=True,
    )


def is_timezone_aware(value: datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None


def validate_lon_lat(value: tuple[float, float], *, label: str) -> tuple[float, float]:
    longitude, latitude = value
    if not all(isfinite(coordinate) for coordinate in value):
        raise ValueError(f"{label} coordinates must be finite")
    if not -180 <= longitude <= 180 or not -90 <= latitude <= 90:
        raise ValueError(f"{label} must be an ordered longitude/latitude pair")
    return value


class TimeWindow(StrictModel):
    from_at: datetime | None = None
    to_at: datetime | None = None

    @model_validator(mode="after")
    def validate_window(self) -> TimeWindow:
        values = tuple(value for value in (self.from_at, self.to_at) if value is not None)
        if any(not is_timezone_aware(value) for value in values):
            raise ValueError("event time window values must include a timezone")
        if self.from_at is not None and self.to_at is not None and self.to_at < self.from_at:
            raise ValueError("event time window end must not precede its start")
        return self


class CandidateArea(StrictModel):
    center: tuple[float, float]
    radius_km: float = Field(gt=0, le=1_000)
    confidence: float = Field(ge=0, le=1)
    name: str | None = Field(default=None, min_length=1, max_length=500)
    supporting_source_ids: tuple[SafeIdentifierV2, ...] = Field(default=(), max_length=128)

    @model_validator(mode="after")
    def validate_area(self) -> CandidateArea:
        validate_lon_lat(self.center, label="candidate area center")
        if len(self.supporting_source_ids) != len(set(self.supporting_source_ids)):
            raise ValueError("candidate area source references must be unique")
        return self


class ProviderRun(StrictModel):
    provider_id: SafeIdentifierV2
    provider_version: str = Field(min_length=1, max_length=255)
    model_id: str | None = Field(default=None, min_length=1, max_length=500)
    model_version: str | None = Field(default=None, min_length=1, max_length=255)
    config: dict[str, JsonValue] = Field(default_factory=dict)
    input_hash: Sha256HexV2
    runtime_ms: int = Field(ge=0)
    cost_usd: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    generated_at: datetime

    @model_validator(mode="after")
    def validate_timestamp(self) -> ProviderRun:
        if not is_timezone_aware(self.generated_at):
            raise ValueError("provider generated_at must include a timezone")
        if (self.model_id is None) != (self.model_version is None):
            raise ValueError("provider model identity and version must be supplied together")
        return self


class LocationCandidate(StrictModel):
    candidate_id: SafeIdentifierV2
    longitude: float = Field(ge=-180, le=180, allow_inf_nan=False)
    latitude: float = Field(ge=-90, le=90, allow_inf_nan=False)
    radius_m: float = Field(default=50, gt=0, le=1_000_000, allow_inf_nan=False)
    score: float = Field(ge=0, le=1)
    rank: int = Field(ge=1)
    evidence_kind: Literal[
        "visual_retrieval",
        "research_prior",
        "metadata",
        "manual",
        "geometric_verification",
    ]
    provider_id: SafeIdentifierV2
    provider_version: str = Field(min_length=1, max_length=255)
    source_id: SafeIdentifierV2 | None = None
    media_id: SafeIdentifierV2 | None = None
    reference_id: SafeIdentifierV2 | None = None
    raw_score: float | None = Field(default=None, ge=-1, le=1, allow_inf_nan=False)
    temporal_consistency: float | None = Field(default=None, ge=0, le=1)
    geometric_consistency: float | None = Field(default=None, ge=0, le=1)

    @model_validator(mode="after")
    def validate_support(self) -> LocationCandidate:
        if self.source_id is None and self.media_id is None:
            raise ValueError("location candidates require a source_id or media_id")
        return self


class ScoreBreakdown(StrictModel):
    retrieval: float = Field(default=0, ge=0, le=1)
    source_independence: float = Field(default=0, ge=0, le=1)
    geographic_prior: float = Field(default=0, ge=0, le=1)
    metadata: float = Field(default=0, ge=0, le=1)
    independent_media: float = Field(default=0, ge=0, le=1)
    temporal_consistency: float = Field(default=0, ge=0, le=1)
    geometric_verification: float = Field(default=0, ge=0, le=1)


class CandidateCluster(StrictModel):
    cluster_id: SafeIdentifierV2
    center: tuple[float, float]
    radius_m: float = Field(gt=0, le=1_000_000, allow_inf_nan=False)
    score: float = Field(ge=0, le=1)
    score_breakdown: ScoreBreakdown
    supporting_candidate_ids: tuple[SafeIdentifierV2, ...] = Field(min_length=1, max_length=512)
    supporting_source_ids: tuple[SafeIdentifierV2, ...] = Field(default=(), max_length=256)
    supporting_media_ids: tuple[SafeIdentifierV2, ...] = Field(default=(), max_length=512)
    independent_source_count: int = Field(default=0, ge=0)
    independent_media_count: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def validate_cluster(self) -> CandidateCluster:
        validate_lon_lat(self.center, label="candidate cluster center")
        for label, values in (
            ("candidate", self.supporting_candidate_ids),
            ("source", self.supporting_source_ids),
            ("media", self.supporting_media_ids),
        ):
            if len(values) != len(set(values)):
                raise ValueError(f"candidate cluster {label} references must be unique")
        if self.independent_source_count > len(self.supporting_source_ids):
            raise ValueError("independent source count exceeds supporting source count")
        if self.independent_media_count > len(self.supporting_media_ids):
            raise ValueError("independent media count exceeds supporting media count")
        return self
