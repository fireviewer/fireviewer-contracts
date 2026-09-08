from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import AnyHttpUrl, Field, model_validator

from fireviewer_contracts.contracts import SafeIdentifierV2, Sha256HexV2, StrictModel
from fireviewer_contracts.mvp.contracts.common import (
    CandidateArea,
    CandidateCluster,
    LocationCandidate,
    SchemaContractModel,
    TimeWindow,
    is_timezone_aware,
)


class EvidenceSource(StrictModel):
    source_id: SafeIdentifierV2
    origin_id: SafeIdentifierV2
    source_url: AnyHttpUrl | None = None
    publisher: str = Field(min_length=1, max_length=500)
    published_at: datetime | None = None
    retrieved_at: datetime
    source_type: Literal[
        "official",
        "press",
        "social",
        "witness",
        "satellite",
        "panoramax",
        "metadata",
        "other",
    ]
    independence_weight: float = Field(ge=0, le=1)

    @model_validator(mode="after")
    def validate_times(self) -> EvidenceSource:
        values = tuple(
            value for value in (self.published_at, self.retrieved_at) if value is not None
        )
        if any(not is_timezone_aware(value) for value in values):
            raise ValueError("source timestamps must include a timezone")
        return self


class Claim(StrictModel):
    claim_id: SafeIdentifierV2
    source_id: SafeIdentifierV2
    claim_type: SafeIdentifierV2
    text: str = Field(min_length=1, max_length=10_000)
    observed_at: datetime | None = None
    confidence: float = Field(ge=0, le=1)
    evidence_media_ids: tuple[SafeIdentifierV2, ...] = Field(default=(), max_length=16)

    @model_validator(mode="after")
    def validate_observed_at(self) -> Claim:
        if self.observed_at is not None and not is_timezone_aware(self.observed_at):
            raise ValueError("claim observed_at must include a timezone")
        if len(self.evidence_media_ids) != len(set(self.evidence_media_ids)):
            raise ValueError("claim evidence media identifiers must be unique")
        return self


class EvidenceMedia(StrictModel):
    media_id: SafeIdentifierV2
    source_id: SafeIdentifierV2
    media_group_id: SafeIdentifierV2
    origin_id: SafeIdentifierV2
    kind: Literal["photo", "video", "audio", "keyframe", "satellite_image"]
    sha256: Sha256HexV2
    captured_at: datetime | None = None
    parent_media_id: SafeIdentifierV2 | None = None

    @model_validator(mode="after")
    def validate_media(self) -> EvidenceMedia:
        if self.captured_at is not None and not is_timezone_aware(self.captured_at):
            raise ValueError("media captured_at must include a timezone")
        if self.kind == "keyframe" and self.parent_media_id is None:
            raise ValueError("keyframes require a parent_media_id")
        if self.kind != "keyframe" and self.parent_media_id is not None:
            raise ValueError("only keyframes may reference parent media")
        return self


class VisualObservation(StrictModel):
    observation_id: SafeIdentifierV2
    media_id: SafeIdentifierV2
    observation_type: Literal["detection", "place_candidate", "local_match", "target_pixel"]
    result_reference: SafeIdentifierV2
    confidence: float | None = Field(default=None, ge=0, le=1)


class SatelliteObservation(StrictModel):
    observation_id: SafeIdentifierV2
    source_id: SafeIdentifierV2
    media_id: SafeIdentifierV2 | None = None
    observation_type: Literal["burn_scar", "change", "thermal", "hotspot", "cloud_blocked"]
    result_reference: SafeIdentifierV2
    acquired_at: datetime
    confidence: float | None = Field(default=None, ge=0, le=1)

    @model_validator(mode="after")
    def validate_acquired_at(self) -> SatelliteObservation:
        if not is_timezone_aware(self.acquired_at):
            raise ValueError("satellite observation time must include a timezone")
        return self


class Contradiction(StrictModel):
    contradiction_id: SafeIdentifierV2
    left_evidence_id: SafeIdentifierV2
    right_evidence_id: SafeIdentifierV2
    description: str = Field(min_length=1, max_length=2_000)

    @model_validator(mode="after")
    def validate_distinct_evidence(self) -> Contradiction:
        if self.left_evidence_id == self.right_evidence_id:
            raise ValueError("a contradiction requires two distinct evidence records")
        return self


class Uncertainty(StrictModel):
    uncertainty_id: SafeIdentifierV2
    code: SafeIdentifierV2
    scope_type: Literal["event", "source", "media", "candidate", "cluster"]
    scope_id: SafeIdentifierV2
    description: str = Field(min_length=1, max_length=2_000)


class EventEvidenceV1(SchemaContractModel):
    schema_name: Literal["fireviewer.event-evidence.v1"] = Field(
        default="fireviewer.event-evidence.v1",
        alias="schema",
    )
    event_id: SafeIdentifierV2
    time_window: TimeWindow = Field(default_factory=TimeWindow)
    candidate_area: CandidateArea | None = None
    sources: tuple[EvidenceSource, ...] = Field(default=(), max_length=512)
    claims: tuple[Claim, ...] = Field(default=(), max_length=2_048)
    media: tuple[EvidenceMedia, ...] = Field(default=(), max_length=2_048)
    visual_observations: tuple[VisualObservation, ...] = Field(default=(), max_length=4_096)
    satellite_observations: tuple[SatelliteObservation, ...] = Field(default=(), max_length=1_024)
    location_candidates: tuple[LocationCandidate, ...] = Field(default=(), max_length=8_192)
    candidate_clusters: tuple[CandidateCluster, ...] = Field(default=(), max_length=256)
    contradictions: tuple[Contradiction, ...] = Field(default=(), max_length=1_024)
    uncertainties: tuple[Uncertainty, ...] = Field(default=(), max_length=1_024)
    needs_human_review: bool = False

    @model_validator(mode="after")
    def validate_evidence_graph(self) -> EventEvidenceV1:
        collections = {
            "source": tuple(item.source_id for item in self.sources),
            "claim": tuple(item.claim_id for item in self.claims),
            "media": tuple(item.media_id for item in self.media),
            "visual observation": tuple(item.observation_id for item in self.visual_observations),
            "satellite observation": tuple(
                item.observation_id for item in self.satellite_observations
            ),
            "location candidate": tuple(item.candidate_id for item in self.location_candidates),
            "candidate cluster": tuple(item.cluster_id for item in self.candidate_clusters),
            "contradiction": tuple(item.contradiction_id for item in self.contradictions),
            "uncertainty": tuple(item.uncertainty_id for item in self.uncertainties),
        }
        for label, identifiers in collections.items():
            if len(identifiers) != len(set(identifiers)):
                raise ValueError(f"duplicate {label} identifier")

        source_ids = set(collections["source"])
        media_by_id = {item.media_id: item for item in self.media}
        media_ids = set(media_by_id)
        if self.candidate_area is not None and not set(
            self.candidate_area.supporting_source_ids
        ).issubset(source_ids):
            raise ValueError("candidate area references an unknown source")
        if any(item.source_id not in source_ids for item in self.claims):
            raise ValueError("claim references an unknown source")
        if any(item.source_id not in source_ids for item in self.media):
            raise ValueError("media references an unknown source")
        for item in self.media:
            if item.parent_media_id is not None:
                parent = media_by_id.get(item.parent_media_id)
                if parent is None or parent.kind != "video":
                    raise ValueError("keyframe parent must reference a known video")
                if parent.media_group_id != item.media_group_id:
                    raise ValueError("keyframe and parent video must share media_group_id")
        if any(item.media_id not in media_ids for item in self.visual_observations):
            raise ValueError("visual observation references unknown media")
        if any(item.source_id not in source_ids for item in self.satellite_observations):
            raise ValueError("satellite observation references an unknown source")
        if any(
            item.media_id is not None and item.media_id not in media_ids
            for item in self.satellite_observations
        ):
            raise ValueError("satellite observation references unknown media")
        for candidate in self.location_candidates:
            if candidate.source_id is not None and candidate.source_id not in source_ids:
                raise ValueError("location candidate references an unknown source")
            if candidate.media_id is not None and candidate.media_id not in media_ids:
                raise ValueError("location candidate references unknown media")

        candidate_ids = set(collections["location candidate"])
        for cluster in self.candidate_clusters:
            if not set(cluster.supporting_candidate_ids).issubset(candidate_ids):
                raise ValueError("candidate cluster references an unknown location candidate")
            if not set(cluster.supporting_source_ids).issubset(source_ids):
                raise ValueError("candidate cluster references an unknown source")
            if not set(cluster.supporting_media_ids).issubset(media_ids):
                raise ValueError("candidate cluster references unknown media")

        evidence_ids = set(collections["claim"])
        evidence_ids.update(collections["visual observation"])
        evidence_ids.update(collections["satellite observation"])
        for contradiction in self.contradictions:
            if {
                contradiction.left_evidence_id,
                contradiction.right_evidence_id,
            } - evidence_ids:
                raise ValueError("contradiction references unknown evidence")

        scope_ids = {
            "event": {self.event_id},
            "source": source_ids,
            "media": media_ids,
            "candidate": candidate_ids,
            "cluster": set(collections["candidate cluster"]),
        }
        if any(item.scope_id not in scope_ids[item.scope_type] for item in self.uncertainties):
            raise ValueError("uncertainty references an unknown scope")
        return self
