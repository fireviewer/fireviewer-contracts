from __future__ import annotations

import json
import re
from enum import StrEnum
from functools import lru_cache
from hashlib import sha256
from importlib.resources import files
from typing import Literal

from pydantic import Field, model_validator

from fireviewer_contracts.contracts import StrictModel


class ArtifactKind(StrEnum):
    SOURCE_CANDIDATES = "source_candidates"
    TRANSCRIPT = "transcript"
    DETECTION_REGIONS = "detection_regions"
    FIRE_POINT_PIXEL = "fire_point_pixel"
    VISIBLE_FRONT_MASK = "visible_front_mask"
    CROSS_VIEW_WINDOW = "cross_view_window"
    CAMERA_POSE = "camera_pose"
    SPATIAL_POINT = "spatial_point"
    OBSERVED_HOTSPOT = "observed_hotspot"
    OBSERVED_BURNED_PERIMETER = "observed_burned_perimeter"
    PROBABLE_ACTIVITY_ENVELOPE = "probable_activity_envelope"
    SIMULATED_SPREAD_SCENARIO = "simulated_spread_scenario"
    FACT_SET = "fact_set"
    SITUATION_REPORT = "situation_report"


class ImplementationStatus(StrEnum):
    INTEGRATED = "integrated"
    BASELINE_ONLY = "baseline_only"
    INTEGRATION_REQUIRED = "integration_required"
    TRAINING_REQUIRED = "training_required"
    BENCHMARK_REQUIRED = "benchmark_required"


class StageActivation(StrEnum):
    OPEN = "open"
    BASELINE_ONLY = "baseline_only"
    CLOSED = "closed"


class MvpCandidate(StrictModel):
    candidate_id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{1,127}$")
    model_id: str = Field(min_length=2, max_length=256)
    source: Literal[
        "huggingface",
        "local_bundle",
        "source_repository",
        "deterministic",
        "sensor_adapter",
        "simulation",
    ]
    source_repository: str | None = Field(default=None, max_length=2_048)
    revision: str | None = Field(default=None, min_length=2, max_length=128)
    rank: int = Field(ge=1, le=3)
    status: ImplementationStatus
    provisioned_by_mvp: bool = False
    current_candidate: bool = False
    notes: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_candidate(self) -> MvpCandidate:
        executable = {
            ImplementationStatus.INTEGRATED,
            ImplementationStatus.BASELINE_ONLY,
        }
        if (
            self.source == "huggingface"
            and self.revision is not None
            and re.fullmatch(r"[0-9a-f]{40}", self.revision) is None
        ):
            raise ValueError("Hugging Face revisions must be immutable 40-character commits")
        if (
            self.source == "local_bundle"
            and self.revision is not None
            and re.fullmatch(r"(?:[0-9a-f]{40}|sha256:[0-9a-f]{64})", self.revision) is None
        ):
            raise ValueError("local bundles require a source commit or SHA-256 digest")
        if self.source == "source_repository" and (
            self.source_repository is None
            or not self.source_repository.startswith("https://github.com/")
        ):
            raise ValueError("source repositories require an explicit public GitHub URL")
        if self.status in executable and self.revision is None:
            raise ValueError("integrated candidates require an immutable revision")
        if self.current_candidate and self.status not in executable:
            raise ValueError("an unavailable candidate cannot be selected as current")
        if self.provisioned_by_mvp and self.status not in executable:
            raise ValueError("an unavailable candidate cannot be provisioned by the MVP")
        return self


class MvpStage(StrictModel):
    stage_id: str = Field(pattern=r"^[a-z][a-z0-9_]{2,63}$")
    activation: StageActivation
    policy: Literal["single_with_rules", "cascade", "quorum", "deterministic_fusion"]
    candidates: tuple[MvpCandidate, ...] = Field(min_length=1, max_length=3)
    outputs: tuple[ArtifactKind, ...] = Field(min_length=1)
    judge_on_contradiction: bool = False
    activation_requirements: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_stage(self) -> MvpStage:
        candidate_ids = [candidate.candidate_id for candidate in self.candidates]
        ranks = [candidate.rank for candidate in self.candidates]
        if len(candidate_ids) != len(set(candidate_ids)):
            raise ValueError("candidate ids must be unique inside a stage")
        if len(ranks) != len(set(ranks)) or ranks != sorted(ranks):
            raise ValueError("candidate ranks must be unique and ordered")
        current = [candidate for candidate in self.candidates if candidate.current_candidate]
        if self.activation in {StageActivation.OPEN, StageActivation.BASELINE_ONLY}:
            if len(current) != 1:
                raise ValueError("an active stage requires exactly one current candidate")
        elif current:
            raise ValueError("a closed stage cannot select a current candidate")
        if (
            self.activation == StageActivation.OPEN
            and current
            and current[0].status != ImplementationStatus.INTEGRATED
        ):
            raise ValueError("an open stage must use an integrated candidate")
        if (
            self.activation == StageActivation.BASELINE_ONLY
            and current
            and current[0].status != ImplementationStatus.BASELINE_ONLY
        ):
            raise ValueError("a baseline-only stage must select a baseline candidate")
        if self.policy == "single_with_rules" and len(self.candidates) != 1:
            raise ValueError("single_with_rules requires exactly one candidate")
        if self.policy == "quorum" and len(self.candidates) < 2:
            raise ValueError("quorum requires at least two candidates")
        if self.judge_on_contradiction and len(self.candidates) < 2:
            raise ValueError("contradiction adjudication requires at least two candidates")
        if len(self.outputs) != len(set(self.outputs)):
            raise ValueError("stage outputs must be unique")
        return self


class MvpHardware(StrictModel):
    gpu: Literal["NVIDIA A40"]
    vram_gib: Literal[48]
    minimum_system_ram_gib: int = Field(ge=64, le=256)
    dtype: Literal["bfloat16"]
    attention: Literal["flash_attention_2"]
    quantization: Literal["none"]
    execution: Literal["strictly_sequential"]
    maximum_large_models_in_vram: Literal[1]


class MvpJudge(StrictModel):
    candidate: MvpCandidate
    confidence_threshold: float = Field(ge=0.5, le=1.0)
    supported_evidence: tuple[Literal["structured_outputs", "source_text"], ...]
    visual_disagreement_without_direct_evidence: Literal["abstain"]

    @model_validator(mode="after")
    def validate_judge(self) -> MvpJudge:
        if self.candidate.model_id != "Qwen/Qwen3-14B":
            raise ValueError("the frozen MVP judge is Qwen3-14B")
        if self.candidate.status != ImplementationStatus.INTEGRATED:
            raise ValueError("the frozen MVP judge must be integrated")
        if not self.candidate.provisioned_by_mvp:
            raise ValueError("the frozen MVP judge must be provisioned")
        return self


class MvpStack(StrictModel):
    schema_version: Literal["1.0"]
    stack_id: Literal["firewarning-mvp-a40-v1"]
    status: Literal["frozen"]
    hardware: MvpHardware
    judge: MvpJudge
    stages: tuple[MvpStage, ...] = Field(min_length=1)
    auto_publication: Literal[False]
    human_validation_required: Literal[True]
    private_results_on_abstention: Literal[True]

    @model_validator(mode="after")
    def validate_stack(self) -> MvpStack:
        stage_ids = [stage.stage_id for stage in self.stages]
        if len(stage_ids) != len(set(stage_ids)):
            raise ValueError("MVP stage ids must be unique")
        candidate_ids = [
            candidate.candidate_id for stage in self.stages for candidate in stage.candidates
        ]
        candidate_ids.append(self.judge.candidate.candidate_id)
        if len(candidate_ids) != len(set(candidate_ids)):
            raise ValueError("MVP candidate ids must be globally unique")
        if not any(stage.judge_on_contradiction for stage in self.stages):
            raise ValueError("at least one multi-candidate stage must invoke the final judge")
        return self


@lru_cache(maxsize=1)
def load_mvp_stack() -> MvpStack:
    resource = files("fireviewer_contracts.mvp_stack_data").joinpath("a40-v1.json")
    return MvpStack.model_validate_json(resource.read_text(encoding="utf-8"))


def mvp_stack_digest(stack: MvpStack | None = None) -> str:
    payload = (stack or load_mvp_stack()).model_dump(mode="json")
    canonical = json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    return sha256(canonical.encode("utf-8")).hexdigest()
