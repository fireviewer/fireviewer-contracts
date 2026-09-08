from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from hashlib import sha256
from typing import Literal, Protocol

from pydantic import Field, model_validator

from fireviewer_contracts.contracts import SafeIdentifierV2, Sha256HexV2, StrictModel
from fireviewer_contracts.mvp.contracts import PointAssessmentV1, PointEvidenceBundleV1


class PointSupervisorInputImage(StrictModel):
    media_id: SafeIdentifierV2
    content_type: Literal["image/jpeg", "image/png", "image/webp"]
    sha256: Sha256HexV2
    content: bytes = Field(min_length=1, max_length=8 * 1_024 * 1_024, repr=False)

    @model_validator(mode="after")
    def validate_content_hash(self) -> PointSupervisorInputImage:
        if sha256(self.content).hexdigest() != self.sha256:
            raise ValueError("point supervisor image digest mismatch")
        return self


class PointSupervisor(Protocol):
    @property
    def supervisor_mode(self) -> Literal["managed_vl", "simulated"]: ...

    @property
    def max_images(self) -> int: ...

    def assess(
        self,
        bundle: PointEvidenceBundleV1,
        *,
        generated_at: datetime,
        images: tuple[PointSupervisorInputImage, ...] = (),
    ) -> PointAssessmentV1: ...


class PointSupervisorMediaRepository(Protocol):
    def read_media(
        self,
        *,
        event_id: str,
        media_id: str,
        expected_sha256: str,
        maximum_bytes: int = 8 * 1_024 * 1_024,
    ) -> PointSupervisorInputImage: ...


def selected_supervisor_images(
    *,
    bundle: PointEvidenceBundleV1,
    durable_media: Mapping[str, tuple[str, str]],
    repository: PointSupervisorMediaRepository,
    maximum_images: int,
) -> tuple[PointSupervisorInputImage, ...]:
    """Read only the bounded image evidence already selected into the point bundle."""

    if maximum_images <= 0:
        return ()
    media_references = {
        item.evidence_id: item
        for item in bundle.evidence_references
        if item.evidence_type == "media" and item.artifact_sha256 is not None
    }
    selected: list[PointSupervisorInputImage] = []
    for media_id in sorted(media_references):
        durable = durable_media.get(media_id)
        if durable is None:
            continue
        kind, durable_sha256 = durable
        reference = media_references[media_id]
        if kind != "photo" or durable_sha256 != reference.artifact_sha256:
            continue
        selected.append(
            repository.read_media(
                event_id=bundle.event_id,
                media_id=media_id,
                expected_sha256=durable_sha256,
            )
        )
        if len(selected) >= maximum_images:
            break
    return tuple(selected)


__all__ = [
    "PointSupervisor",
    "PointSupervisorInputImage",
    "PointSupervisorMediaRepository",
    "selected_supervisor_images",
]
