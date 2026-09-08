from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from fireviewer_contracts.contracts import SafeIdentifierV2, StrictModel
from fireviewer_contracts.mvp.contracts.common import ProviderRun, SchemaContractModel


class Detection(StrictModel):
    detection_id: SafeIdentifierV2
    detection_class: Literal["fire", "smoke"]
    bbox: tuple[float, float, float, float]
    score: float = Field(ge=0, le=1)
    prompt: str = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def validate_bbox(self) -> Detection:
        left, top, right, bottom = self.bbox
        if not 0 <= left < right <= 1 or not 0 <= top < bottom <= 1:
            raise ValueError("detection bbox must be an ordered normalized box")
        return self


class DetectionResultV1(SchemaContractModel):
    schema_name: Literal["fireviewer.detection.v1"] = Field(
        default="fireviewer.detection.v1",
        alias="schema",
    )
    media_id: SafeIdentifierV2
    provider_run: ProviderRun
    detections: tuple[Detection, ...] = Field(default=(), max_length=512)
    status: Literal["fire", "smoke", "fire_and_smoke", "none", "uncertain"]
    review_status: Literal["candidate", "accepted", "rejected"] = "candidate"
    needs_human_review: bool = False

    @model_validator(mode="after")
    def validate_status(self) -> DetectionResultV1:
        detection_ids = [item.detection_id for item in self.detections]
        if len(detection_ids) != len(set(detection_ids)):
            raise ValueError("detection identifiers must be unique")
        classes = {item.detection_class for item in self.detections}
        expected = {
            "fire": {"fire"},
            "smoke": {"smoke"},
            "fire_and_smoke": {"fire", "smoke"},
            "none": set(),
        }
        if self.status in expected and classes != expected[self.status]:
            raise ValueError("detection status must match the returned detection classes")
        if self.status == "uncertain" and not self.needs_human_review:
            raise ValueError("uncertain detections require human review")
        return self
