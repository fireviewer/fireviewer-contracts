from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from fireviewer_contracts.backend.incident_state_schemas import (
    IncidentStateRevision,
    IncidentUpdateRequest,
    IncidentUpdateResult,
    TemporalEvidence,
)

T = datetime(2026, 9, 18, 8, tzinfo=UTC)


def evidence(**changes):
    return TemporalEvidence.model_validate(dict(
        evidence_id="photo-1", revision=1, incident_id="FR-test", source_id="source-1",
        content_sha256="a" * 64, license="test-fixture", media_kind="image",
        observed_at=T, retrieved_at=T + timedelta(days=5), recorded_at=T + timedelta(days=5),
        **changes,
    ))


def state(**changes):
    payload = dict(
        revision_id="ISR-1", incident_id="FR-test", episode_id="E01", valid_from=T,
        knowledge_cutoff=T, reconstruction_mode="causal", calculated_at=T,
        algorithm_id="test", algorithm_revision="1", fusion_profile_sha256="a" * 64,
        spatial_context_sha256="b" * 64, seed_id="seed-1", source_input_sha256="c" * 64,
        quality="insufficient",
    )
    return IncidentStateRevision.model_validate({**payload, **changes})


def test_late_evidence_has_two_distinct_time_axes():
    proof = evidence()
    assert proof.known_at == T + timedelta(days=5)
    assert proof.observed_at == T
    assert proof.reference.content_sha256 == "a" * 64


@pytest.mark.parametrize("changes", [
    {"valid_from": T.replace(tzinfo=None)},
    {"valid_until": T},
    {"knowledge_cutoff": T + timedelta(days=1)},
    {"parent_revision_id": "ISR-1"},
    {"publication_state": "published"},
])
def test_state_rejects_temporal_and_publication_ambiguity(changes):
    with pytest.raises(ValidationError):
        state(**changes)


def test_retrospective_correction_and_timezone_normalization():
    later = T + timedelta(days=5)
    revision = state(
        valid_from=T.astimezone(__import__("zoneinfo").ZoneInfo("Europe/Paris")),
        reconstruction_mode="retrospective", knowledge_cutoff=later, calculated_at=later,
        supersedes_revision_id="ISR-older",
    )
    assert revision.valid_from.tzinfo == UTC
    assert revision.human_review_state == "pending"


def test_evidence_unknown_time_and_explicit_version_chain():
    base = evidence().model_dump()
    unknown = TemporalEvidence.model_validate({
        **base, "observed_at": None, "time_precision": "unknown"
    })
    assert unknown.observed_at is None
    with pytest.raises(ValidationError):
        TemporalEvidence.model_validate({**base, "revision": 2})
    withdrawn = TemporalEvidence.model_validate({
        **base, "revision": 2, "supersedes_revision": 1, "admissibility": "withdrawn"
    })
    assert withdrawn.reference.revision == 2
    with pytest.raises(ValidationError):
        TemporalEvidence.model_validate({**base, "time_precision": "unknown"})


def test_request_and_waiting_results_cannot_claim_completion():
    request = IncidentUpdateRequest(
        incident_id="FR-test", episode_id="E01", trigger="expiry", valid_at=T,
        knowledge_cutoff=T, reconstruction_mode="causal", idempotency_key="expiry-001",
    )
    assert request.expected_current_revision_id is None
    waiting = IncidentUpdateResult(incident_id="FR-test", status="awaiting_initialization")
    assert waiting.revision is None
    with pytest.raises(ValidationError):
        IncidentUpdateResult(incident_id="FR-test", status="created")
    with pytest.raises(ValidationError):
        IncidentUpdateResult(incident_id="FR-other", status="created", revision=state())
