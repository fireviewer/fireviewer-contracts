from __future__ import annotations

import json
from datetime import date, datetime
from hashlib import sha256
from pathlib import PurePosixPath
from typing import Literal

from pydantic import AnyHttpUrl, Field, model_validator

from fireviewer_contracts.contracts import SafeIdentifierV2, Sha256HexV2, StrictModel
from fireviewer_contracts.mvp.contracts.common import (
    is_timezone_aware,
    validate_lon_lat,
)

SourceKind = Literal[
    "prefecture",
    "fr_alert",
    "copernicus_ems",
    "legifrance",
    "official_social",
    "press",
]
CaseStage = Literal["candidate", "coverage_profiled", "benchmark_ready", "excluded"]
GroundTruthStatus = Literal["acquisition_pending", "available", "not_identified"]
MediaRightsStatus = Literal[
    "verified",
    "not_checked_internal_benchmark",
    "restricted",
]


class CorpusSource(StrictModel):
    source_id: SafeIdentifierV2
    origin_id: SafeIdentifierV2
    publisher: str = Field(min_length=2, max_length=300)
    kind: SourceKind
    url: AnyHttpUrl
    retrieved_at: datetime
    claim_summary: str = Field(min_length=10, max_length=1_000)
    content_sha256: Sha256HexV2 | None = None

    @model_validator(mode="after")
    def validate_retrieval_time(self) -> CorpusSource:
        if not is_timezone_aware(self.retrieved_at):
            raise ValueError("corpus source retrieval time must include a timezone")
        return self


class CollectionAoi(StrictModel):
    center_wgs84: tuple[float, float]
    bbox_wgs84: tuple[float, float, float, float]
    basis: Literal["cems_activation_extent", "official_commune_buffer"]
    provenance_source_id: SafeIdentifierV2

    @model_validator(mode="after")
    def validate_aoi(self) -> CollectionAoi:
        validate_lon_lat(self.center_wgs84, label="collection AOI center")
        min_lon, min_lat, max_lon, max_lat = self.bbox_wgs84
        validate_lon_lat((min_lon, min_lat), label="collection AOI minimum corner")
        validate_lon_lat((max_lon, max_lat), label="collection AOI maximum corner")
        if min_lon >= max_lon or min_lat >= max_lat:
            raise ValueError("collection AOI bbox must be ordered")
        if max_lon - min_lon > 2 or max_lat - min_lat > 2:
            raise ValueError("collection AOI must remain regional")
        longitude, latitude = self.center_wgs84
        if not min_lon <= longitude <= max_lon or not min_lat <= latitude <= max_lat:
            raise ValueError("collection AOI center must be inside its bbox")
        return self


class GroundTruthReference(StrictModel):
    status: GroundTruthStatus
    method: str = Field(min_length=5, max_length=500)
    source_id: SafeIdentifierV2 | None = None
    source_url: AnyHttpUrl | None = None
    content_sha256: Sha256HexV2 | None = None
    feature_count: int | None = Field(default=None, ge=1)
    bbox_wgs84: tuple[float, float, float, float] | None = None
    center_wgs84: tuple[float, float] | None = None
    radius_m: float | None = Field(default=None, gt=0, le=1_000_000)

    @model_validator(mode="after")
    def validate_availability(self) -> GroundTruthReference:
        derived = (
            self.content_sha256,
            self.feature_count,
            self.bbox_wgs84,
            self.center_wgs84,
            self.radius_m,
        )
        if self.status == "available":
            if (
                self.source_id is None
                or self.source_url is None
                or any(value is None for value in derived)
            ):
                raise ValueError("available ground truth requires source and derived measurements")
            assert self.bbox_wgs84 is not None
            assert self.center_wgs84 is not None
            min_lon, min_lat, max_lon, max_lat = self.bbox_wgs84
            validate_lon_lat((min_lon, min_lat), label="ground-truth minimum corner")
            validate_lon_lat((max_lon, max_lat), label="ground-truth maximum corner")
            validate_lon_lat(self.center_wgs84, label="ground-truth center")
            if min_lon >= max_lon or min_lat >= max_lat:
                raise ValueError("ground-truth bbox must be ordered")
        elif any(value is not None for value in derived):
            raise ValueError("unavailable ground truth cannot contain derived measurements")
        return self


class CorpusMedia(StrictModel):
    media_id: SafeIdentifierV2
    origin_id: SafeIdentifierV2
    source_id: SafeIdentifierV2
    media_url: AnyHttpUrl
    relative_path: str | None = Field(default=None, min_length=1, max_length=500)
    captured_at: datetime | None = None
    media_sha256: Sha256HexV2 | None = None
    license: str | None = Field(default=None, min_length=2, max_length=500)
    rights_status: MediaRightsStatus = "not_checked_internal_benchmark"

    @model_validator(mode="after")
    def validate_capture_time(self) -> CorpusMedia:
        if self.captured_at is not None and not is_timezone_aware(self.captured_at):
            raise ValueError("corpus media capture time must include a timezone")
        if self.relative_path is not None:
            path = PurePosixPath(self.relative_path)
            if path.is_absolute() or ".." in path.parts or "\\" in self.relative_path:
                raise ValueError("corpus media relative path must remain repository-relative")
        return self


class Summer2026EventCase(StrictModel):
    case_id: SafeIdentifierV2
    label: str = Field(min_length=3, max_length=300)
    event_date: date
    stage: CaseStage = "candidate"
    collection_aoi: CollectionAoi
    sources: tuple[CorpusSource, ...] = Field(min_length=2, max_length=20)
    ground_truth: GroundTruthReference
    media: tuple[CorpusMedia, ...] = Field(default=(), max_length=50)

    @model_validator(mode="after")
    def validate_case(self) -> Summer2026EventCase:
        if self.event_date.year != 2026 or self.event_date.month not in {6, 7, 8}:
            raise ValueError("event case must belong to June, July or August 2026")
        source_ids = [source.source_id for source in self.sources]
        if len(source_ids) != len(set(source_ids)):
            raise ValueError("event source identifiers must be unique")
        known_sources = set(source_ids)
        if self.collection_aoi.provenance_source_id not in known_sources:
            raise ValueError("collection AOI references an unknown source")
        if self.ground_truth.source_id not in known_sources | {None}:
            raise ValueError("ground truth references an unknown source")
        media_ids = [item.media_id for item in self.media]
        if len(media_ids) != len(set(media_ids)):
            raise ValueError("event media identifiers must be unique")
        if any(item.source_id not in known_sources for item in self.media):
            raise ValueError("event media references an unknown source")
        if self.stage == "benchmark_ready" and readiness_blockers(self):
            raise ValueError("benchmark-ready case still has readiness blockers")
        return self


class CaseReadiness(StrictModel):
    case_id: SafeIdentifierV2
    ready: bool
    blocker_codes: tuple[SafeIdentifierV2, ...]


class CorpusReadinessReport(StrictModel):
    corpus_sha256: Sha256HexV2
    case_count: int = Field(ge=5, le=10)
    ready_case_count: int = Field(ge=0, le=10)
    cases: tuple[CaseReadiness, ...]


class Summer2026Corpus(StrictModel):
    schema_name: Literal["fireviewer.mvp-summer-2026-corpus.v1"] = Field(
        alias="schema",
        serialization_alias="schema",
    )
    country: Literal["FR"]
    frozen_at: datetime
    cases: tuple[Summer2026EventCase, ...] = Field(min_length=5, max_length=10)

    @model_validator(mode="after")
    def validate_corpus(self) -> Summer2026Corpus:
        if not is_timezone_aware(self.frozen_at):
            raise ValueError("corpus freeze time must include a timezone")
        case_ids = [case.case_id for case in self.cases]
        if len(case_ids) != len(set(case_ids)):
            raise ValueError("corpus case identifiers must be unique")
        if {case.event_date.month for case in self.cases if case.stage != "excluded"} != {
            6,
            7,
            8,
        }:
            raise ValueError("active corpus cases must cover June, July and August 2026")
        return self

    def canonical_sha256(self) -> str:
        serialized = json.dumps(
            self.model_dump(mode="json", by_alias=True),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
        return sha256(serialized).hexdigest()

    def readiness_report(self) -> CorpusReadinessReport:
        cases = tuple(
            CaseReadiness(
                case_id=case.case_id,
                ready=not (blockers := readiness_blockers(case)),
                blocker_codes=blockers,
            )
            for case in self.cases
            if case.stage != "excluded"
        )
        return CorpusReadinessReport(
            corpus_sha256=self.canonical_sha256(),
            case_count=len(cases),
            ready_case_count=sum(item.ready for item in cases),
            cases=cases,
        )


def readiness_blockers(case: Summer2026EventCase) -> tuple[SafeIdentifierV2, ...]:
    blockers: set[str] = set()
    if case.ground_truth.status != "available":
        blockers.add("ground_truth_not_available")
    if len({source.origin_id for source in case.sources}) < 2:
        blockers.add("independent_sources_below_2")
    if any(source.content_sha256 is None for source in case.sources):
        blockers.add("source_snapshot_missing")
    if not 20 <= len(case.media) <= 50:
        blockers.add("event_media_below_minimum")
    if any(item.media_sha256 is None for item in case.media):
        blockers.add("media_digest_missing")
    return tuple(sorted(blockers))
