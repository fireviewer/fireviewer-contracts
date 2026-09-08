from __future__ import annotations

import json
from hashlib import sha256

from fireviewer_contracts.mvp.supervision.backend_event_evidence import (
    AzureBackendEventEvidenceConfig,
    AzureBackendIncidentDayEvidenceAdapter,
    BackendIncidentDayMediaAnalysisPublisher,
    BackendIncidentDayResearchPublisher,
    BackendIncidentDaySatelliteObservationPublisher,
    BackendJsonResponse,
    BackendSatelliteObservationBatch,
)


def _digest(payload: object) -> str:
    return sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
    ).hexdigest()


class _Transport:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload
        self.urls: list[str] = []

    def get_json(self, url: str, **_kwargs: object) -> BackendJsonResponse:
        self.urls.append(url)
        checksum = str(self.payload["source_sha256"])
        return BackendJsonResponse(
            payload=self.payload,
            headers={"etag": f'"{checksum}"', "x-checksum-sha256": checksum},
        )

    def post_json(
        self,
        url: str,
        *,
        payload: object,
        **_kwargs: object,
    ) -> BackendJsonResponse:
        self.urls.append(url)
        assert isinstance(payload, dict)
        checksum = "b" * 64
        if url.endswith("/satellite-observations"):
            response = {
                "analysis_id": payload["analysis_id"],
                "artifact_revision_id": payload["artifact_revision_id"],
                "result_id": payload["result_id"],
                "replayed": False,
                "status": payload["status"],
                "claim_ids": [],
                "source_revision_sha256": checksum,
            }
            return BackendJsonResponse(
                payload=response,
                headers={"etag": f'"{checksum}"', "x-checksum-sha256": checksum},
            )
        if url.endswith("/media-analyses"):
            response = {
                "candidate_id": payload["candidate_id"],
                "batch_id": payload["batch_id"],
                "media_id": payload["media_id"],
                "replayed": False,
                "claim_count": len(payload.get("claims", [])),
                "keyframe_observation_count": len(payload.get("keyframe_observations", [])),
                "transcription_receipt_count": len(payload.get("transcription_receipts", [])),
                "journal_entry_count": len(payload.get("journal_entries", [])),
                "source_revision_sha256": checksum,
            }
            return BackendJsonResponse(
                payload=response,
                headers={"etag": f'"{checksum}"', "x-checksum-sha256": checksum},
            )
        response = {
            "candidate_id": payload["candidate_id"],
            "plan_id": payload["plan_id"],
            "page_id": payload["page_id"],
            "wave_number": payload.get("wave_number", 1),
            "wave_focus": payload.get("wave_focus", ["general"]),
            "replayed": False,
            "source_count": 1,
            "claim_count": 0,
            "media_count": 0,
            "duplicate_source_count": 0,
            "duplicate_claim_count": 0,
            "duplicate_media_count": 0,
            "completed": False,
            "media_ticket_limit": 2_048,
            "safety_limit_reached": False,
            "converged": False,
            "zero_yield_wave_streak": 0,
            "coverage_ready": False,
            "next_cursor": "next",
            "source_revision_sha256": checksum,
        }
        return BackendJsonResponse(
            payload=response,
            headers={"etag": f'"{checksum}"', "x-checksum-sha256": checksum},
        )


def _context() -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": "incident-day-research-read-1.0",
        "analysis_id": "AN-DIE-2026-07-06",
        "fire_id": "FR-26-00001",
        "episode_id": "EP-DIE-2026",
        "incident_name": "Die Justin",
        "incident_reference": [5.37, 44.75],
        "incident_bbox": [5.2, 44.6, 5.55, 44.9],
        "local_date": "2026-07-06",
        "timezone": "Europe/Paris",
        "window_start_at": "2026-07-05T22:00:00Z",
        "window_end_at": "2026-07-06T22:00:00Z",
        "episode_started_at": "2026-07-04T12:00:00Z",
        "episode_last_observed_at": "2026-07-21T12:00:00Z",
        "episode_ended_at": "2026-07-22T12:00:00Z",
        "episode_status": "ENDED",
        "source_registry_version": "2026-08-24",
        "source_policies": {
            "drome.gouv.fr": {
                "publisher": "Prefecture de la Drome",
                "source_type": "official",
                "independence_weight": 1.0,
                "claim_types": ["incident_status", "fire_progression"],
            }
        },
        "search_templates": {"html.duckduckgo.com": "https://html.duckduckgo.com/html/?q={query}"},
        "research_evidence": None,
        "satellite_artifacts": [],
        "spatial_observations": [],
        "coverage": {
            "queries_exhausted": False,
            "safety_limit_reached": False,
            "converged": False,
            "source_count": 0,
            "official_source_count": 0,
            "independent_evidence_family_count": 0,
            "claim_count": 0,
            "image_count": 0,
            "video_count": 0,
            "audio_count": 0,
            "media_analysis_required_count": 0,
            "media_analysis_completed_count": 0,
            "media_analysis_failed_count": 0,
            "satellite_artifact_count": 0,
            "materialized_satellite_count": 0,
            "satellite_analysis_required_count": 0,
            "satellite_analysis_completed_count": 0,
            "spatial_observation_count": 0,
            "time_qualified_observation_count": 0,
            "expected_lifecycle_phases": [
                "ignition_or_initial_detection",
                "daily_progression_or_status",
            ],
            "covered_lifecycle_phases": [],
            "missing_dimensions": ["web_query_waves", "spatial_observation"],
            "documentary_ready": False,
            "spatial_ready": False,
            "satellite_analysis_ready": True,
            "media_analysis_ready": True,
            "coverage_ready": False,
        },
    }
    payload["source_sha256"] = _digest(payload)
    return payload


def test_incident_day_adapter_builds_worker_target_without_perimeter() -> None:
    transport = _Transport(_context())
    adapter = AzureBackendIncidentDayEvidenceAdapter(
        AzureBackendEventEvidenceConfig(
            base_url="https://backend.fireviewer.test",
            bearer_token="token-" + ("x" * 40),
        ),
        transport=transport,
    )

    durable = adapter.read("AN-DIE-2026-07-06")

    assert durable.event.event_id == "AN-DIE-2026-07-06"
    assert durable.incident_id == "FR-26-00001"
    assert durable.viewpoint_label == "Die Justin"
    assert durable.research_target_kind == "incident_day"
    assert durable.geographic_references == ()
    assert set(durable.research_source_policies or {}) == {"drome.gouv.fr"}
    assert transport.urls[0].endswith("/api/v1/internal/incident-day-research/AN-DIE-2026-07-06")


def test_satellite_observation_batch_accepts_optional_reference_artifact() -> None:
    parsed = BackendSatelliteObservationBatch.model_validate(
        {
            "result_id": "SATOBS-S3-DIE-20260706",
            "artifact_revision_id": "EAR-S3-DIE-20260706",
            "reference_artifact_revision_id": None,
            "sink_request_sha256": "a" * 64,
            "status": "completed",
            "processor": "sentinel3_frp_v1",
            "processor_revision": "fireviewer-sentinel3-frp-cpu-1.1.0",
            "claim_ids": ["ECL-S3-DIE-20260706"],
            "observed_at": "2026-07-06T10:00:00Z",
            "valid_coverage_geojson": None,
            "coverage_metrics": {},
            "asset_receipt_sha256": "b" * 64,
            "raw_content_stored": False,
            "persisted_at": "2026-07-07T08:00:00Z",
        }
    )

    assert parsed.reference_artifact_revision_id is None


def test_satellite_observation_batch_accepts_explicit_openeo_unavailability() -> None:
    parsed = BackendSatelliteObservationBatch.model_validate(
        {
            "result_id": "SATOBS-S1-DIE-20260706",
            "artifact_revision_id": "EAR-S1-DIE-20260706",
            "reference_artifact_revision_id": "EAR-S1-DIE-20260630",
            "sink_request_sha256": "a" * 64,
            "status": "unavailable",
            "unavailable_reason": "cdse_openeo_not_authorized",
            "processor": "sentinel1_vvvh_change_v1",
            "processor_revision": "fireviewer-sentinel1-vvvh-change-openeo-1.1.0",
            "claim_ids": [],
            "observed_at": "2026-07-06T10:00:00Z",
            "valid_coverage_geojson": None,
            "coverage_metrics": {},
            "asset_receipt_sha256": "b" * 64,
            "raw_content_stored": False,
            "persisted_at": "2026-07-07T08:00:00Z",
        }
    )

    assert parsed.status == "unavailable"
    assert parsed.unavailable_reason == "cdse_openeo_not_authorized"


def test_incident_day_adapter_exposes_satellite_geometry_metrics_and_provenance_to_eve() -> None:
    payload = _context()
    payload["spatial_observations"] = [
        {
            "claim_id": "ECL-CLMS-DIE-20260706",
            "artifact_revision_id": "EAR-CDSE-CLMS-DIE-20260706",
            "provider_key": "copernicus-data-space",
            "semantic_role": "interpreted_observation",
            "source_url": ("https://browser.dataspace.copernicus.eu/?item=CLMS-DIE-20260706"),
            "attribution": "European Union, Copernicus Land Monitoring Service",
            "retrieved_at": "2026-07-07T08:00:00Z",
            "observed_at": "2026-07-06T10:00:00Z",
            "assertion_kind": "burned_area",
            "geometry_geojson": {
                "type": "Polygon",
                "coordinates": [
                    [
                        [5.30, 44.70],
                        [5.34, 44.70],
                        [5.34, 44.74],
                        [5.30, 44.74],
                        [5.30, 44.70],
                    ]
                ],
            },
            "coverage_geojson": {
                "type": "Polygon",
                "coordinates": [
                    [
                        [5.29, 44.69],
                        [5.35, 44.69],
                        [5.35, 44.75],
                        [5.29, 44.75],
                        [5.29, 44.69],
                    ]
                ],
            },
            "confidence": 0.82,
            "horizontal_accuracy_m": 300.0,
            "resolution_m": 300.0,
            "processor": "clms_burned_area_daily_v1",
            "source_dataset": "ba_global_300m_daily_v4",
            "satellite": "Sentinel-3",
            "instrument": "OLCI-SLSTR",
            "metrics": {
                "pixel_count": 12,
                "burn_probability_mean": 0.82,
                "burn_fraction_mean": 0.67,
            },
            "independent_family_key": "cdse:clms-die-20260706",
        }
    ]
    payload["coverage"]["spatial_observation_count"] = 1
    payload["coverage"]["time_qualified_observation_count"] = 1
    payload["coverage"]["spatial_ready"] = True
    payload["source_sha256"] = _digest(
        {key: value for key, value in payload.items() if key != "source_sha256"}
    )
    adapter = AzureBackendIncidentDayEvidenceAdapter(
        AzureBackendEventEvidenceConfig(
            base_url="https://backend.fireviewer.test",
            bearer_token="token-" + ("x" * 40),
        ),
        transport=_Transport(payload),
    )

    durable = adapter.read("AN-DIE-2026-07-06")

    assert durable.event.sources[0].source_type == "satellite"
    assert durable.event.claims[0].claim_type == "burned_area"
    assert '"burn_probability_mean":0.82' in durable.event.claims[0].text
    assert durable.event.satellite_observations[0].observation_type == "burn_scar"
    assert durable.geographic_references[0].geometry_geojson["type"] == "Polygon"
    assert durable.geographic_references[0].horizontal_uncertainty_m == 300.0


def test_incident_day_publisher_uses_ticket_only_sink() -> None:
    transport = _Transport(_context())
    publisher = BackendIncidentDayResearchPublisher(
        AzureBackendEventEvidenceConfig(
            base_url="https://backend.fireviewer.test",
            bearer_token="token-" + ("x" * 40),
        ),
        transport=transport,
    )
    payload = {
        "candidate_id": "AN-DIE-2026-07-06",
        "plan_id": "PLAN-AUTO-DIE",
        "page_id": "PAGE-WEB-1",
        "wave_number": 1,
        "wave_focus": ["incident_identity"],
    }

    receipt = publisher.publish(
        candidate_id="AN-DIE-2026-07-06",
        payload=payload,
    )

    assert receipt.candidate_id == "AN-DIE-2026-07-06"
    assert transport.urls[0].endswith(
        "/api/v1/internal/incident-day-research/AN-DIE-2026-07-06/pages"
    )


def test_incident_day_media_publisher_uses_post_collection_sink() -> None:
    transport = _Transport(_context())
    publisher = BackendIncidentDayMediaAnalysisPublisher(
        AzureBackendEventEvidenceConfig(
            base_url="https://backend.fireviewer.test",
            bearer_token="token-" + ("x" * 40),
        ),
        transport=transport,
    )
    payload = {
        "candidate_id": "AN-DIE-2026-07-06",
        "batch_id": "MEDIA-BATCH-1",
        "media_id": "MEDIA-VIDEO-1",
        "claims": [],
        "keyframe_observations": [{}],
        "transcription_receipts": [],
        "journal_entries": [{}],
    }

    receipt = publisher.publish(
        candidate_id="AN-DIE-2026-07-06",
        payload=payload,
    )

    assert receipt.batch_id == "MEDIA-BATCH-1"
    assert receipt.keyframe_observation_count == 1
    assert transport.urls[0].endswith(
        "/api/v1/internal/incident-day-research/AN-DIE-2026-07-06/media-analyses"
    )


def test_incident_day_satellite_observation_publisher_uses_deterministic_sink() -> None:
    transport = _Transport(_context())
    publisher = BackendIncidentDaySatelliteObservationPublisher(
        AzureBackendEventEvidenceConfig(
            base_url="https://backend.fireviewer.test",
            bearer_token="token-" + ("x" * 40),
        ),
        transport=transport,
    )
    receipt = publisher.publish(
        candidate_id="AN-DIE-2026-07-06",
        payload={
            "analysis_id": "AN-DIE-2026-07-06",
            "artifact_revision_id": "EAR-CDSE-FRP-1",
            "result_id": "SATOBS-CDSE-FRP-1",
            "status": "no_observation",
        },
    )

    assert receipt.artifact_revision_id == "EAR-CDSE-FRP-1"
    assert receipt.status == "no_observation"
    assert transport.urls[0].endswith(
        "/api/v1/internal/incident-day-research/AN-DIE-2026-07-06/satellite-observations"
    )
