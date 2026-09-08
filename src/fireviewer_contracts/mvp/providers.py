from __future__ import annotations

from datetime import datetime
from typing import Literal, Protocol, runtime_checkable

from pydantic import Field, JsonValue, model_validator

from fireviewer_contracts.contracts import SafeIdentifierV2, StrictModel
from fireviewer_contracts.mvp.contracts import (
    CandidateCluster,
    DetectionResultV1,
    EventEvidenceV1,
    EvidenceMedia,
    LocalizationResultV1,
    LocationCandidate,
    SatelliteResultV1,
    SatelliteScene,
    TimeWindow,
)
from fireviewer_contracts.mvp.contracts.common import is_timezone_aware


class ProviderDescriptor(StrictModel):
    provider_id: SafeIdentifierV2
    provider_version: str = Field(min_length=1, max_length=255)
    model_id: str | None = Field(default=None, min_length=1, max_length=500)
    model_version: str | None = Field(default=None, min_length=1, max_length=255)
    config: dict[str, JsonValue] = Field(default_factory=dict)
    capabilities: tuple[SafeIdentifierV2, ...] = Field(min_length=1, max_length=64)

    @model_validator(mode="after")
    def validate_descriptor(self) -> ProviderDescriptor:
        if (self.model_id is None) != (self.model_version is None):
            raise ValueError("provider model identity and version must be supplied together")
        if len(self.capabilities) != len(set(self.capabilities)):
            raise ValueError("provider capabilities must be unique")
        return self


class ProviderHealth(StrictModel):
    status: Literal["healthy", "degraded", "unavailable"]
    checked_at: datetime
    reason_codes: tuple[SafeIdentifierV2, ...] = Field(default=(), max_length=32)

    @model_validator(mode="after")
    def validate_health(self) -> ProviderHealth:
        if not is_timezone_aware(self.checked_at):
            raise ValueError("provider health timestamp must include a timezone")
        if self.status != "healthy" and not self.reason_codes:
            raise ValueError("degraded or unavailable provider health requires a reason code")
        return self


class ResearchRequest(StrictModel):
    event_id: SafeIdentifierV2
    query: str = Field(min_length=1, max_length=4_000)
    time_window: TimeWindow = Field(default_factory=TimeWindow)
    existing_evidence: EventEvidenceV1 | None = None


@runtime_checkable
class Provider(Protocol):
    descriptor: ProviderDescriptor

    def healthcheck(self) -> ProviderHealth: ...


@runtime_checkable
class ResearchProvider(Provider, Protocol):
    def search(self, request: ResearchRequest) -> EventEvidenceV1: ...


@runtime_checkable
class VisionProvider(Provider, Protocol):
    def detect(self, media: EvidenceMedia) -> DetectionResultV1: ...


@runtime_checkable
class SatelliteProvider(Provider, Protocol):
    def analyze(self, media: EvidenceMedia, scene: SatelliteScene) -> SatelliteResultV1: ...


@runtime_checkable
class PlaceRetriever(Provider, Protocol):
    def retrieve(
        self,
        evidence: EventEvidenceV1,
        media_ids: tuple[SafeIdentifierV2, ...],
    ) -> tuple[LocationCandidate, ...]: ...


@runtime_checkable
class EvidenceFusionProvider(Provider, Protocol):
    def fuse(self, evidence: EventEvidenceV1) -> EventEvidenceV1: ...


@runtime_checkable
class LocalMatcher(Provider, Protocol):
    def verify(
        self,
        evidence: EventEvidenceV1,
        cluster: CandidateCluster,
    ) -> tuple[LocationCandidate, ...]: ...


@runtime_checkable
class PoseEstimator(Provider, Protocol):
    def localize(
        self,
        evidence: EventEvidenceV1,
        cluster: CandidateCluster,
    ) -> LocalizationResultV1: ...


__all__ = [
    "EvidenceFusionProvider",
    "LocalMatcher",
    "PlaceRetriever",
    "PoseEstimator",
    "Provider",
    "ProviderDescriptor",
    "ProviderHealth",
    "ResearchProvider",
    "ResearchRequest",
    "SatelliteProvider",
    "VisionProvider",
]
