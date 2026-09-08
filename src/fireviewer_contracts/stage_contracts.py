from __future__ import annotations

import json
from collections.abc import Iterator, Mapping
from enum import StrEnum
from functools import lru_cache
from hashlib import sha256
from importlib.resources import files
from typing import Literal

from pydantic import Field, model_validator

from fireviewer_contracts.contracts import StrictModel


class StageRole(StrEnum):
    SOURCE_RESEARCH = "source_research"
    ASR = "asr"
    FIRE_DETECTION = "fire_detection"
    VISUAL_GROUNDING = "visual_grounding"
    MULTIMODAL_EXTRACTION = "multimodal_extraction"
    FIRE_POINTING = "fire_pointing"
    BURNED_AREA = "burned_area"
    CROSS_VIEW_REGISTRATION = "cross_view_registration"
    SPATIAL_PROJECTION = "spatial_projection"
    EVIDENCE_FUSION = "evidence_fusion"
    SITUATION_REPORT = "situation_report"


class StageCapability(StrEnum):
    RESEARCH_REQUEST = "research_request"
    TIME_CUTOFF = "time_cutoff"
    NETWORK_BROKER = "network_broker"
    SOURCE_CANDIDATES = "source_candidates"
    AUDIO_INPUT = "audio_input"
    VISUAL_INPUT = "visual_input"
    TEXT_INPUT = "text_input"
    TRANSCRIPT = "transcript"
    SELECTED_VISUAL = "selected_visual"
    DETECTION_REGIONS = "detection_regions"
    GROUNDED_REGIONS = "grounded_regions"
    FACTUAL_OBSERVATIONS = "factual_observations"
    FIRE_POINT_PIXEL = "fire_point_pixel"
    SATELLITE_MULTISPECTRAL = "satellite_multispectral"
    BURNED_AREA_GEOMETRY = "burned_area_geometry"
    EXPLICIT_ABSTENTION = "explicit_abstention"
    REFERENCE_BUNDLE = "reference_bundle"
    SPATIAL_MATCHES = "spatial_matches"
    CAMERA_POSE = "camera_pose"
    TERRAIN_REFERENCE = "terrain_reference"
    SPATIAL_PROPOSALS = "spatial_proposals"
    EVIDENCE_GRAPH = "evidence_graph"
    REPORT_DRAFT = "report_draft"


class FailurePolicy(StrEnum):
    BLOCK = "block"
    CONTINUE_DEGRADED = "continue_degraded"
    HUMAN_REVIEW = "human_review"


class StageContract(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    contract_id: str = Field(pattern=r"^stage\.[a-z0-9_]+\.v[0-9]+$")
    role: StageRole
    execution_kind: Literal["model", "deterministic", "orchestration"]
    model_binding: str | None = Field(default=None, min_length=2, max_length=128)
    required_all: tuple[StageCapability, ...] = ()
    required_any: tuple[StageCapability, ...] = ()
    produces: tuple[StageCapability, ...] = Field(min_length=1)
    minimum_output_any: tuple[StageCapability, ...] = Field(min_length=1)
    instructions: tuple[str, ...] = Field(min_length=1)
    forbidden_outputs: tuple[str, ...] = Field(min_length=1)
    max_input_items: int = Field(ge=1, le=32)
    max_output_items_per_input: int = Field(ge=1, le=10_000)
    max_wall_time_seconds: int = Field(ge=1, le=7_200)
    max_repair_attempts: int = Field(ge=0, le=1)
    failure_policy: FailurePolicy
    skip_when_not_applicable: bool
    downstream_roles: tuple[StageRole, ...] = ()

    @model_validator(mode="after")
    def validate_contract(self) -> StageContract:
        groups = (
            self.required_all,
            self.required_any,
            self.produces,
            self.minimum_output_any,
            self.downstream_roles,
        )
        if any(len(group) != len(set(group)) for group in groups):
            raise ValueError("stage contract lists must not contain duplicates")
        if not set(self.minimum_output_any).issubset(self.produces):
            raise ValueError("minimum_output_any must be a subset of produces")
        if self.role in self.downstream_roles:
            raise ValueError("a stage cannot list itself as a downstream role")
        if self.execution_kind == "model" and self.model_binding is None:
            raise ValueError("model stages require a model_binding")
        if self.execution_kind != "model" and self.model_binding is not None:
            raise ValueError("only model stages may define model_binding")
        if self.execution_kind != "model" and self.max_repair_attempts:
            raise ValueError("non-model stages cannot request model repairs")
        return self


class StageContractRegistry(Mapping[StageRole, StageContract]):
    def __init__(self, contracts: tuple[StageContract, ...]) -> None:
        by_role = {contract.role: contract for contract in contracts}
        if len(by_role) != len(contracts):
            raise ValueError("stage contract roles must be unique")
        self._contracts = by_role
        canonical = [
            contract.model_dump(mode="json")
            for contract in sorted(contracts, key=lambda item: item.role.value)
        ]
        payload = json.dumps(canonical, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
        self.digest = sha256(payload.encode("utf-8")).hexdigest()

    def __getitem__(self, key: StageRole) -> StageContract:
        return self._contracts[key]

    def __iter__(self) -> Iterator[StageRole]:
        return iter(self._contracts)

    def __len__(self) -> int:
        return len(self._contracts)


@lru_cache(maxsize=1)
def load_stage_contract_registry() -> StageContractRegistry:
    package_root = files("fireviewer_contracts.stage_contracts_data")
    contracts = tuple(
        StageContract.model_validate_json(resource.read_text(encoding="utf-8"))
        for resource in sorted(package_root.iterdir(), key=lambda item: item.name)
        if resource.name.endswith(".json")
    )
    if not contracts:
        raise RuntimeError("no packaged stage contracts were found")
    return StageContractRegistry(contracts)
