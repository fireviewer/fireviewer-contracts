from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field, model_validator

from fireviewer_contracts.contracts import SafeIdentifierV2, Sha256HexV2, StrictModel
from fireviewer_contracts.geometry_contract import validate_geojson_geometry
from fireviewer_contracts.mvp.contracts.common import (
    ProviderRun,
    SchemaContractModel,
    is_timezone_aware,
    validate_lon_lat,
)


class SatelliteScene(StrictModel):
    scene_id: SafeIdentifierV2
    source: str = Field(min_length=1, max_length=255)
    product_id: SafeIdentifierV2
    acquired_at: datetime
    bands: tuple[str, ...] = Field(min_length=1, max_length=32)
    resolution_m: float = Field(gt=0, le=100_000, allow_inf_nan=False)
    crs: str = Field(min_length=3, max_length=128)
    aoi_bbox_wgs84: tuple[float, float, float, float]
    processing_steps: tuple[str, ...] = Field(default=(), max_length=64)
    cloud_cover_percent: float | None = Field(default=None, ge=0, le=100)

    @model_validator(mode="after")
    def validate_scene(self) -> SatelliteScene:
        if not is_timezone_aware(self.acquired_at):
            raise ValueError("satellite acquisition time must include a timezone")
        if len(self.bands) != len(set(self.bands)):
            raise ValueError("satellite bands must be unique")
        min_lon, min_lat, max_lon, max_lat = self.aoi_bbox_wgs84
        validate_lon_lat((min_lon, min_lat), label="satellite minimum bbox corner")
        validate_lon_lat((max_lon, max_lat), label="satellite maximum bbox corner")
        if min_lon >= max_lon or min_lat >= max_lat:
            raise ValueError("satellite AOI bbox must be ordered")
        return self


class SatelliteMask(StrictModel):
    mask_id: SafeIdentifierV2
    mask_class: Literal["burned_area", "change"]
    confidence: float | None = Field(default=None, ge=0, le=1)
    geometry_geojson: dict[str, object] | None = None
    raster_sha256: Sha256HexV2 | None = None
    pixel_count: int | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def validate_mask(self) -> SatelliteMask:
        if self.geometry_geojson is None and self.raster_sha256 is None:
            raise ValueError("satellite masks require geometry or a raster digest")
        if self.geometry_geojson is not None:
            validate_geojson_geometry(
                self.geometry_geojson,
                allowed_types={"Polygon", "MultiPolygon"},
            )
        return self


class SatelliteResultV1(SchemaContractModel):
    schema_name: Literal["fireviewer.satellite.v1"] = Field(
        default="fireviewer.satellite.v1",
        alias="schema",
    )
    media_id: SafeIdentifierV2
    scene: SatelliteScene
    provider_run: ProviderRun
    status: Literal["observed", "none", "uncertain", "not_applicable"]
    masks: tuple[SatelliteMask, ...] = Field(default=(), max_length=256)
    uncertainty_codes: tuple[SafeIdentifierV2, ...] = Field(default=(), max_length=64)
    needs_human_review: bool = False

    @model_validator(mode="after")
    def validate_result(self) -> SatelliteResultV1:
        mask_ids = [item.mask_id for item in self.masks]
        if len(mask_ids) != len(set(mask_ids)):
            raise ValueError("satellite mask identifiers must be unique")
        if self.status == "observed" and not self.masks:
            raise ValueError("observed satellite results require at least one mask")
        if self.status in {"none", "not_applicable"} and self.masks:
            raise ValueError("empty satellite statuses cannot contain masks")
        if self.status == "uncertain" and not self.uncertainty_codes:
            raise ValueError("uncertain satellite results require an uncertainty code")
        if self.status == "uncertain" and not self.needs_human_review:
            raise ValueError("uncertain satellite results require human review")
        return self
