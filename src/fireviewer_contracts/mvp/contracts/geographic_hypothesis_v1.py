from __future__ import annotations

from datetime import datetime
from math import isfinite
from typing import Any, Literal

from pydantic import Field, model_validator

from fireviewer_contracts.contracts import SafeIdentifierV2, Sha256HexV2, StrictModel
from fireviewer_contracts.mvp.contracts.common import (
    ProviderRun,
    SchemaContractModel,
    is_timezone_aware,
    validate_lon_lat,
)


def _coordinate_pairs(value: object) -> tuple[tuple[float, float], ...]:
    pairs: list[tuple[float, float]] = []

    def visit(item: object) -> None:
        if not isinstance(item, (list, tuple)) or not item:
            raise ValueError("GeoJSON coordinates must be non-empty arrays")
        if len(item) >= 2 and all(isinstance(part, (int, float)) for part in item[:2]):
            longitude, latitude = float(item[0]), float(item[1])
            validate_lon_lat((longitude, latitude), label="geographic reference")
            pairs.append((longitude, latitude))
            return
        for child in item:
            visit(child)

    visit(value)
    return tuple(pairs)


class GeographicReference(StrictModel):
    reference_id: SafeIdentifierV2
    reference_kind: Literal[
        "satellite_hotspot",
        "satellite_active_area",
        "prior_active_point",
        "prior_fire_front",
        "prior_perimeter",
    ]
    geometry_geojson: dict[str, Any]
    observed_at: datetime | None = None
    horizontal_uncertainty_m: float | None = Field(
        default=None,
        gt=0,
        le=100_000,
        allow_inf_nan=False,
    )
    confidence: float | None = Field(default=None, ge=0, le=1)
    artifact_revision: str = Field(min_length=1, max_length=255)
    lineage_family_id: SafeIdentifierV2 | None = None

    @model_validator(mode="after")
    def validate_reference(self) -> GeographicReference:
        geometry_type = self.geometry_geojson.get("type")
        if geometry_type not in {
            "Point",
            "MultiPoint",
            "LineString",
            "MultiLineString",
            "Polygon",
            "MultiPolygon",
        }:
            raise ValueError("geographic references require supported WGS84 GeoJSON")
        _coordinate_pairs(self.geometry_geojson.get("coordinates"))
        if self.observed_at is not None and not is_timezone_aware(self.observed_at):
            raise ValueError("geographic reference time must include a timezone")
        return self


class GeographicScoreBreakdown(StrictModel):
    visual: float = Field(ge=0, le=1)
    camera_bearing: float = Field(ge=0, le=1)
    terrain_visibility: float = Field(ge=0, le=1)
    satellite: float = Field(ge=0, le=1)
    temporal_alignment: float | None = Field(default=None, ge=0, le=1)
    history_progression: float | None = Field(default=None, ge=0, le=1)


class GeographicHypothesis(StrictModel):
    hypothesis_id: SafeIdentifierV2
    observation_id: SafeIdentifierV2
    detection_id: SafeIdentifierV2
    media_id: SafeIdentifierV2
    phenomenon: Literal["active_fire_point", "smoke_origin"]
    longitude: float = Field(ge=-180, le=180, allow_inf_nan=False)
    latitude: float = Field(ge=-90, le=90, allow_inf_nan=False)
    geometry_geojson: dict[str, Any]
    horizontal_uncertainty_m: float = Field(gt=0, le=100_000, allow_inf_nan=False)
    camera_bearing_deg: float = Field(ge=0, lt=360, allow_inf_nan=False)
    camera_distance_m: float = Field(gt=0, le=200_000, allow_inf_nan=False)
    source_point_normalized: tuple[float, float]
    score: float = Field(ge=0, le=1)
    score_breakdown: GeographicScoreBreakdown
    supporting_reference_ids: tuple[SafeIdentifierV2, ...] = Field(
        min_length=1,
        max_length=128,
    )
    reason_codes: tuple[SafeIdentifierV2, ...] = Field(min_length=1, max_length=64)
    needs_human_review: Literal[True] = True
    geometry_mutation_allowed: Literal[False] = False

    @model_validator(mode="after")
    def validate_hypothesis(self) -> GeographicHypothesis:
        validate_lon_lat((self.longitude, self.latitude), label="geographic hypothesis")
        if self.geometry_geojson.get("type") != "Point":
            raise ValueError("geographic hypothesis geometry must be a GeoJSON Point")
        coordinates = self.geometry_geojson.get("coordinates")
        if (
            not isinstance(coordinates, (list, tuple))
            or len(coordinates) != 2
            or not all(isinstance(value, (int, float)) for value in coordinates)
            or (float(coordinates[0]), float(coordinates[1]))
            != (self.longitude, self.latitude)
        ):
            raise ValueError("geographic hypothesis GeoJSON must match its coordinates")
        x, y = self.source_point_normalized
        if not all(isfinite(value) and 0 <= value <= 1 for value in (x, y)):
            raise ValueError("geographic hypothesis source point must be normalized")
        if len(self.supporting_reference_ids) != len(set(self.supporting_reference_ids)):
            raise ValueError("geographic hypothesis reference ids must be unique")
        if len(self.reason_codes) != len(set(self.reason_codes)):
            raise ValueError("geographic hypothesis reason codes must be unique")
        return self


class GeographicAbstention(StrictModel):
    observation_id: SafeIdentifierV2 | None = None
    detection_id: SafeIdentifierV2 | None = None
    media_id: SafeIdentifierV2 | None = None
    reason_codes: tuple[SafeIdentifierV2, ...] = Field(min_length=1, max_length=64)

    @model_validator(mode="after")
    def validate_reason_codes(self) -> GeographicAbstention:
        if len(self.reason_codes) != len(set(self.reason_codes)):
            raise ValueError("geographic abstention reason codes must be unique")
        return self


class GeographicHypothesisResultV1(SchemaContractModel):
    schema_name: Literal["fireviewer.geographic-hypotheses.v1"] = Field(
        default="fireviewer.geographic-hypotheses.v1",
        alias="schema",
    )
    event_id: SafeIdentifierV2
    source_event_evidence_sha256: Sha256HexV2
    status: Literal["hypotheses", "abstained"]
    hypotheses: tuple[GeographicHypothesis, ...] = Field(default=(), max_length=2_048)
    abstentions: tuple[GeographicAbstention, ...] = Field(default=(), max_length=2_048)
    provider_run: ProviderRun
    needs_human_review: Literal[True] = True
    geometry_mutation_allowed: Literal[False] = False

    @model_validator(mode="after")
    def validate_result(self) -> GeographicHypothesisResultV1:
        hypothesis_ids = [item.hypothesis_id for item in self.hypotheses]
        if len(hypothesis_ids) != len(set(hypothesis_ids)):
            raise ValueError("geographic hypothesis identifiers must be unique")
        hypothesis_detection_ids = {item.detection_id for item in self.hypotheses}
        abstention_detection_ids = {
            item.detection_id for item in self.abstentions if item.detection_id is not None
        }
        if hypothesis_detection_ids & abstention_detection_ids:
            raise ValueError("one detection cannot be both localized and abstained")
        if self.status == "hypotheses" and not self.hypotheses:
            raise ValueError("hypotheses status requires at least one GPS hypothesis")
        if self.status == "abstained" and self.hypotheses:
            raise ValueError("abstained geographic results cannot contain coordinates")
        if not self.hypotheses and not self.abstentions:
            raise ValueError("geographic result must account for at least one detection")
        return self


__all__ = [
    "GeographicAbstention",
    "GeographicHypothesis",
    "GeographicHypothesisResultV1",
    "GeographicReference",
    "GeographicScoreBreakdown",
]
