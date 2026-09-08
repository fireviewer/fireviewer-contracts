from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from fireviewer_contracts.mvp.contracts import (
    CameraEvidence,
    CameraGroup,
    CameraIntrinsics,
    CameraPose,
    CandidateCluster,
    DetectionResultV1,
    EventEvidenceV1,
    LocalizationResultV1,
    PoseUncertainty,
    ProviderRun,
    RayUncertainty,
    SatelliteResultV1,
    ScoreBreakdown,
    TargetRay,
)

NOW = datetime(2026, 8, 21, 10, 0, tzinfo=UTC)


def _provider_run(*, provider_id: str = "grounding-dino") -> ProviderRun:
    return ProviderRun(
        provider_id=provider_id,
        provider_version="mvp-1",
        model_id="fixture/model",
        model_version="immutable-revision",
        config={},
        input_hash="a" * 64,
        runtime_ms=12,
        cost_usd=0,
        generated_at=NOW,
    )


def _event_payload() -> dict[str, object]:
    return {
        "schema": "fireviewer.event-evidence.v1",
        "event_id": "EVENT-1",
        "time_window": {
            "from_at": "2026-08-20T12:00:00+02:00",
            "to_at": "2026-08-20T14:00:00+02:00",
        },
        "candidate_area": {
            "center": [5.37, 44.75],
            "radius_km": 15,
            "confidence": 0.71,
            "name": "Die / Romeyer / Justin",
            "supporting_source_ids": ["SOURCE-1"],
        },
        "sources": [
            {
                "source_id": "SOURCE-1",
                "origin_id": "ORIGIN-ARTICLE-1",
                "source_url": "https://example.test/article",
                "publisher": "Fixture press",
                "published_at": "2026-08-20T12:30:00+02:00",
                "retrieved_at": "2026-08-20T13:00:00+02:00",
                "source_type": "press",
                "independence_weight": 1,
            },
            {
                "source_id": "SOURCE-2",
                "origin_id": "ORIGIN-VIDEO-1",
                "publisher": "Fixture witness",
                "retrieved_at": "2026-08-20T13:05:00+02:00",
                "source_type": "witness",
                "independence_weight": 1,
            },
        ],
        "claims": [
            {
                "claim_id": "CLAIM-1",
                "source_id": "SOURCE-1",
                "claim_type": "place_hint",
                "text": "Smoke is visible from Die.",
                "observed_at": "2026-08-20T12:15:00+02:00",
                "confidence": 0.8,
            }
        ],
        "media": [
            {
                "media_id": "VIDEO-1",
                "source_id": "SOURCE-2",
                "media_group_id": "GROUP-VIDEO-1",
                "origin_id": "ORIGIN-VIDEO-1",
                "kind": "video",
                "sha256": "b" * 64,
            },
            {
                "media_id": "FRAME-1",
                "source_id": "SOURCE-2",
                "media_group_id": "GROUP-VIDEO-1",
                "origin_id": "ORIGIN-VIDEO-1",
                "kind": "keyframe",
                "sha256": "c" * 64,
                "parent_media_id": "VIDEO-1",
            },
        ],
        "visual_observations": [
            {
                "observation_id": "VISUAL-1",
                "media_id": "FRAME-1",
                "observation_type": "place_candidate",
                "result_reference": "RETRIEVAL-1",
                "confidence": 0.83,
            }
        ],
        "location_candidates": [
            {
                "candidate_id": "CANDIDATE-1",
                "longitude": 5.37,
                "latitude": 44.75,
                "radius_m": 100,
                "score": 0.83,
                "rank": 1,
                "evidence_kind": "visual_retrieval",
                "provider_id": "megaloc",
                "provider_version": "mvp-1",
                "media_id": "FRAME-1",
                "reference_id": "PANORAMAX-1",
            }
        ],
        "needs_human_review": False,
    }


def _cluster() -> CandidateCluster:
    return CandidateCluster(
        cluster_id="CLUSTER-1",
        center=(5.37, 44.75),
        radius_m=1_200,
        score=0.88,
        score_breakdown=ScoreBreakdown(retrieval=0.88, source_independence=0.5),
        supporting_candidate_ids=("CANDIDATE-1",),
        supporting_source_ids=("SOURCE-2",),
        supporting_media_ids=("FRAME-1",),
        independent_source_count=1,
        independent_media_count=1,
    )


def test_event_evidence_contract_preserves_source_and_media_independence() -> None:
    evidence = EventEvidenceV1.model_validate(_event_payload())

    assert evidence.media[0].media_group_id == evidence.media[1].media_group_id
    assert evidence.media[1].parent_media_id == "VIDEO-1"
    assert evidence.model_dump(mode="json")["schema"] == "fireviewer.event-evidence.v1"
    assert "schema_name" not in evidence.model_dump(mode="json")


def test_event_evidence_rejects_unknown_references_and_split_keyframe_groups() -> None:
    payload = _event_payload()
    payload["claims"][0]["source_id"] = "UNKNOWN"
    with pytest.raises(ValidationError, match="claim references an unknown source"):
        EventEvidenceV1.model_validate(payload)

    payload = _event_payload()
    payload["media"][1]["media_group_id"] = "GROUP-OTHER"
    with pytest.raises(ValidationError, match="share media_group_id"):
        EventEvidenceV1.model_validate(payload)


def test_detection_contract_uses_closed_states_and_normalized_boxes() -> None:
    result = DetectionResultV1.model_validate(
        {
            "schema": "fireviewer.detection.v1",
            "media_id": "FRAME-1",
            "provider_run": _provider_run().model_dump(mode="json"),
            "detections": [
                {
                    "detection_id": "DETECTION-1",
                    "detection_class": "smoke",
                    "bbox": [0.31, 0.21, 0.57, 0.63],
                    "score": 0.78,
                    "prompt": "smoke plume",
                }
            ],
            "status": "smoke",
        }
    )

    assert result.model_dump(mode="json")["schema"] == "fireviewer.detection.v1"

    invalid = result.model_dump(mode="json")
    invalid["status"] = "none"
    with pytest.raises(ValidationError, match="must match"):
        DetectionResultV1.model_validate(invalid)


def test_satellite_contract_preserves_scene_metadata_and_mask_provenance() -> None:
    result = SatelliteResultV1.model_validate(
        {
            "schema": "fireviewer.satellite.v1",
            "media_id": "SATELLITE-1",
            "scene": {
                "scene_id": "SCENE-1",
                "source": "HLS",
                "product_id": "PRODUCT-1",
                "acquired_at": "2026-08-19T10:00:00Z",
                "bands": ["BLUE", "GREEN", "RED", "NIR_NARROW", "SWIR_1", "SWIR_2"],
                "resolution_m": 30,
                "crs": "EPSG:32631",
                "aoi_bbox_wgs84": [5.2, 44.6, 5.5, 44.9],
                "processing_steps": ["signed-six-band-input", "prithvi-burnscars"],
                "cloud_cover_percent": 12,
            },
            "provider_run": _provider_run(provider_id="prithvi-eo").model_dump(mode="json"),
            "status": "observed",
            "masks": [
                {
                    "mask_id": "MASK-1",
                    "mask_class": "burned_area",
                    "confidence": 0.74,
                    "geometry_geojson": {
                        "type": "Polygon",
                        "coordinates": [
                            [[5.35, 44.73], [5.36, 44.73], [5.36, 44.74], [5.35, 44.73]]
                        ],
                    },
                    "raster_sha256": "d" * 64,
                    "pixel_count": 128,
                }
            ],
        }
    )

    assert result.scene.bands[-2:] == ("SWIR_1", "SWIR_2")
    assert result.model_dump(mode="json")["schema"] == "fireviewer.satellite.v1"


def test_localization_contract_stops_at_pose_and_normalized_ray() -> None:
    camera = CameraGroup(
        camera_id="CAMERA-1",
        media_ids=("FRAME-1",),
        camera=CameraPose(
            latitude=44.75,
            longitude=5.37,
            altitude_m=510,
            heading_deg=214,
            pitch_deg=-3,
            roll_deg=1,
            coordinate_reference="EPSG:4978",
            intrinsics=CameraIntrinsics(
                width_px=2_048,
                height_px=1_024,
                fx_px=1_000,
                fy_px=1_000,
                cx_px=1_024,
                cy_px=512,
            ),
        ),
        evidence=CameraEvidence(
            panoramax_ids=("PANORAMAX-1",),
            retrieval_scores=(0.88,),
            inliers=148,
            reprojection_error_px=2.1,
        ),
        uncertainty=PoseUncertainty(horizontal_m=12, vertical_m=8, orientation_deg=2),
    )
    result = LocalizationResultV1(
        event_id="EVENT-1",
        candidate_cluster=_cluster(),
        camera_groups=(camera,),
        target_rays=(
            TargetRay(
                ray_id="RAY-1",
                camera_id="CAMERA-1",
                media_id="FRAME-1",
                target_pixel=(1_432, 844),
                target_point_type="smoke_base",
                ray_origin=(4_448_000, 418_000, 4_540_000),
                ray_direction=(0.0, 0.6, 0.8),
                coordinate_reference="EPSG:4978",
                uncertainty=RayUncertainty(
                    angular_deg=2,
                    origin_horizontal_m=12,
                    origin_vertical_m=8,
                ),
            ),
        ),
        status="rays_available",
    )

    payload = result.model_dump(mode="json")
    assert payload["schema"] == "fireviewer.localization.v1"
    assert payload["target_rays"][0]["ray_direction"] == [0.0, 0.6, 0.8]
    assert "terrain_intersection" not in payload
    assert "perimeter" not in payload


def test_localization_rejects_non_normalized_ray_and_cross_camera_media() -> None:
    with pytest.raises(ValidationError, match="direction must be normalized"):
        TargetRay(
            ray_id="RAY-1",
            camera_id="CAMERA-1",
            media_id="FRAME-1",
            target_pixel=(1, 1),
            target_point_type="manual",
            ray_origin=(0, 0, 0),
            ray_direction=(1, 1, 1),
            coordinate_reference="EPSG:4978",
            uncertainty=RayUncertainty(angular_deg=2, origin_horizontal_m=12),
        )
