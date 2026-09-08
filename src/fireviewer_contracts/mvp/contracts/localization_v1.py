from __future__ import annotations

from math import isclose, sqrt
from typing import Literal

from pydantic import Field, model_validator

from fireviewer_contracts.contracts import SafeIdentifierV2, StrictModel
from fireviewer_contracts.mvp.contracts.common import CandidateCluster, SchemaContractModel


class CameraIntrinsics(StrictModel):
    width_px: int = Field(gt=0, le=200_000)
    height_px: int = Field(gt=0, le=200_000)
    fx_px: float = Field(gt=0, allow_inf_nan=False)
    fy_px: float = Field(gt=0, allow_inf_nan=False)
    cx_px: float = Field(ge=0, allow_inf_nan=False)
    cy_px: float = Field(ge=0, allow_inf_nan=False)

    @model_validator(mode="after")
    def validate_principal_point(self) -> CameraIntrinsics:
        if self.cx_px > self.width_px or self.cy_px > self.height_px:
            raise ValueError("camera principal point must lie inside the image")
        return self


class CameraPose(StrictModel):
    latitude: float = Field(ge=-90, le=90, allow_inf_nan=False)
    longitude: float = Field(ge=-180, le=180, allow_inf_nan=False)
    altitude_m: float = Field(allow_inf_nan=False)
    heading_deg: float = Field(ge=0, lt=360, allow_inf_nan=False)
    pitch_deg: float = Field(ge=-90, le=90, allow_inf_nan=False)
    roll_deg: float = Field(ge=-180, le=180, allow_inf_nan=False)
    coordinate_reference: str = Field(min_length=3, max_length=128)
    intrinsics: CameraIntrinsics


class CameraEvidence(StrictModel):
    panoramax_ids: tuple[SafeIdentifierV2, ...] = Field(default=(), max_length=512)
    retrieval_scores: tuple[float, ...] = Field(default=(), max_length=512)
    inliers: int | None = Field(default=None, ge=0)
    reprojection_error_px: float | None = Field(default=None, ge=0, allow_inf_nan=False)

    @model_validator(mode="after")
    def validate_retrieval(self) -> CameraEvidence:
        if len(self.panoramax_ids) != len(self.retrieval_scores):
            raise ValueError("Panoramax references and retrieval scores must align")
        if len(self.panoramax_ids) != len(set(self.panoramax_ids)):
            raise ValueError("Panoramax references must be unique")
        if any(score < 0 or score > 1 for score in self.retrieval_scores):
            raise ValueError("retrieval scores must be normalized")
        return self


class PoseUncertainty(StrictModel):
    horizontal_m: float = Field(gt=0, le=100_000, allow_inf_nan=False)
    vertical_m: float | None = Field(default=None, gt=0, le=100_000, allow_inf_nan=False)
    orientation_deg: float | None = Field(default=None, gt=0, le=180, allow_inf_nan=False)
    codes: tuple[SafeIdentifierV2, ...] = Field(default=(), max_length=32)


class CameraGroup(StrictModel):
    camera_id: SafeIdentifierV2
    media_ids: tuple[SafeIdentifierV2, ...] = Field(min_length=1, max_length=512)
    camera: CameraPose
    evidence: CameraEvidence
    uncertainty: PoseUncertainty

    @model_validator(mode="after")
    def validate_media(self) -> CameraGroup:
        if len(self.media_ids) != len(set(self.media_ids)):
            raise ValueError("camera group media references must be unique")
        return self


class RayUncertainty(StrictModel):
    angular_deg: float = Field(gt=0, le=180, allow_inf_nan=False)
    origin_horizontal_m: float = Field(gt=0, le=100_000, allow_inf_nan=False)
    origin_vertical_m: float | None = Field(
        default=None,
        gt=0,
        le=100_000,
        allow_inf_nan=False,
    )
    codes: tuple[SafeIdentifierV2, ...] = Field(default=(), max_length=32)


class TargetRay(StrictModel):
    ray_id: SafeIdentifierV2
    camera_id: SafeIdentifierV2
    media_id: SafeIdentifierV2
    target_pixel: tuple[float, float]
    target_point_type: Literal["bbox_center", "smoke_base", "fire_base", "manual", "custom"]
    ray_origin: tuple[float, float, float]
    ray_direction: tuple[float, float, float]
    coordinate_reference: str = Field(min_length=3, max_length=128)
    uncertainty: RayUncertainty

    @model_validator(mode="after")
    def validate_ray(self) -> TargetRay:
        if any(value < 0 for value in self.target_pixel):
            raise ValueError("target pixel coordinates must be non-negative")
        direction_norm = sqrt(sum(value * value for value in self.ray_direction))
        if not isclose(direction_norm, 1.0, rel_tol=1e-6, abs_tol=1e-6):
            raise ValueError("ray direction must be normalized")
        return self


class LocalizationResultV1(SchemaContractModel):
    schema_name: Literal["fireviewer.localization.v1"] = Field(
        default="fireviewer.localization.v1",
        alias="schema",
    )
    event_id: SafeIdentifierV2
    candidate_cluster: CandidateCluster
    camera_groups: tuple[CameraGroup, ...] = Field(default=(), max_length=128)
    target_rays: tuple[TargetRay, ...] = Field(default=(), max_length=1_024)
    status: Literal["candidate_only", "poses_available", "rays_available", "abstained"]
    uncertainty_codes: tuple[SafeIdentifierV2, ...] = Field(default=(), max_length=64)
    needs_human_review: bool = True

    @model_validator(mode="after")
    def validate_localization(self) -> LocalizationResultV1:
        camera_ids = [item.camera_id for item in self.camera_groups]
        ray_ids = [item.ray_id for item in self.target_rays]
        if len(camera_ids) != len(set(camera_ids)):
            raise ValueError("camera group identifiers must be unique")
        if len(ray_ids) != len(set(ray_ids)):
            raise ValueError("target ray identifiers must be unique")
        media_to_camera: dict[str, str] = {}
        cameras = {item.camera_id: item for item in self.camera_groups}
        for camera in self.camera_groups:
            for media_id in camera.media_ids:
                if media_id in media_to_camera:
                    raise ValueError("one media item cannot belong to multiple camera groups")
                media_to_camera[media_id] = camera.camera_id
        for ray in self.target_rays:
            ray_camera = cameras.get(ray.camera_id)
            if ray_camera is None or ray.media_id not in ray_camera.media_ids:
                raise ValueError("target ray must reference media from its camera group")
            width = ray_camera.camera.intrinsics.width_px
            height = ray_camera.camera.intrinsics.height_px
            if ray.target_pixel[0] >= width or ray.target_pixel[1] >= height:
                raise ValueError("target ray pixel must lie inside the camera image")
            if ray.coordinate_reference != ray_camera.camera.coordinate_reference:
                raise ValueError("target ray and camera pose must share a coordinate reference")
        expected = {
            "candidate_only": (False, False),
            "poses_available": (True, False),
            "rays_available": (True, True),
        }
        if self.status in expected:
            expect_cameras, expect_rays = expected[self.status]
            if bool(self.camera_groups) != expect_cameras or bool(self.target_rays) != expect_rays:
                raise ValueError("localization status does not match poses and rays")
        elif not self.uncertainty_codes:
            raise ValueError("localization abstention requires an uncertainty code")
        return self
