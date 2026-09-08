from __future__ import annotations

import os
import re
from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256
from pathlib import Path
from typing import Literal

ModelRole = Literal[
    "asr",
    "burned_area",
    "fire_detection",
    "visual_grounding",
    "multimodal_extraction",
    "fire_pointing",
    "source_research",
    "consensus_judge",
]
_IMMUTABLE_REVISION = re.compile(r"^[0-9a-f]{40}$")


class RegistryError(RuntimeError):
    pass


class ConsensusStrategy(StrEnum):
    """Execution policy for the pinned candidates of one model stage."""

    SINGLE_WITH_RULES = "single_with_rules"
    CASCADE = "cascade"
    QUORUM = "quorum"


class ConsensusFailureDecision(StrEnum):
    """Fail-closed outcome when candidates cannot produce an admissible result."""

    ABSTAIN = "abstain"
    HUMAN_REVIEW = "human_review"


@dataclass(frozen=True, slots=True)
class ModelSpec:
    role: ModelRole
    model_id: str
    revision: str
    source: Literal["huggingface", "local"] = "huggingface"

    def validate(self) -> None:
        if self.source == "huggingface" and not _IMMUTABLE_REVISION.fullmatch(self.revision):
            raise RegistryError(f"{self.role} must use a 40-character immutable commit SHA")
        if self.source == "local" and not re.fullmatch(r"sha256:[0-9a-f]{64}", self.revision):
            raise RegistryError(f"{self.role} local model must use a sha256 digest")


@dataclass(frozen=True, slots=True)
class ModelCandidateSpec:
    candidate_id: str
    spec: ModelSpec
    rank: int

    def validate(self) -> None:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", self.candidate_id):
            raise RegistryError(f"invalid model candidate id: {self.candidate_id!r}")
        if self.rank < 1 or self.rank > 8:
            raise RegistryError(f"{self.candidate_id} rank must be between 1 and 8")
        self.spec.validate()


@dataclass(frozen=True, slots=True)
class ModelGroupSpec:
    """Pinned candidates and deterministic consensus policy for one model role."""

    role: ModelRole
    candidates: tuple[ModelCandidateSpec, ...]
    strategy: ConsensusStrategy = ConsensusStrategy.SINGLE_WITH_RULES
    minimum_successful: int = 1
    minimum_agreeing: int = 1
    agreement_threshold: float = 1.0
    disagreement_decision: ConsensusFailureDecision = ConsensusFailureDecision.HUMAN_REVIEW
    always_challenge: bool = False
    adjudicator: ModelCandidateSpec | None = None
    adjudication_confidence_threshold: float = 0.65

    def validate(self) -> None:
        if not self.candidates:
            raise RegistryError(f"{self.role} model group cannot be empty")
        candidate_ids = [candidate.candidate_id for candidate in self.candidates]
        ranks = [candidate.rank for candidate in self.candidates]
        if len(candidate_ids) != len(set(candidate_ids)):
            raise RegistryError(f"{self.role} model group contains duplicate candidate ids")
        if len(ranks) != len(set(ranks)):
            raise RegistryError(f"{self.role} model group contains duplicate ranks")
        if ranks != sorted(ranks):
            raise RegistryError(f"{self.role} model candidates must be ordered by rank")
        if any(candidate.spec.role != self.role for candidate in self.candidates):
            raise RegistryError(f"{self.role} model group contains a candidate for another role")
        if not 0 <= self.agreement_threshold <= 1:
            raise RegistryError(f"{self.role} agreement threshold must be between 0 and 1")
        if not 0 <= self.adjudication_confidence_threshold <= 1:
            raise RegistryError(
                f"{self.role} adjudication confidence threshold must be between 0 and 1"
            )
        if not 1 <= self.minimum_successful <= len(self.candidates):
            raise RegistryError(f"{self.role} minimum_successful is outside the candidate count")
        if not 1 <= self.minimum_agreeing <= len(self.candidates):
            raise RegistryError(f"{self.role} minimum_agreeing is outside the candidate count")
        if self.strategy == ConsensusStrategy.SINGLE_WITH_RULES and len(self.candidates) != 1:
            raise RegistryError(f"{self.role} single_with_rules requires exactly one candidate")
        if self.strategy == ConsensusStrategy.QUORUM and len(self.candidates) < 2:
            raise RegistryError(f"{self.role} quorum requires at least two candidates")
        if self.strategy == ConsensusStrategy.QUORUM and self.adjudicator is None:
            raise RegistryError(f"{self.role} quorum requires a final adjudicator")
        if self.minimum_agreeing > 1 and self.adjudicator is None:
            raise RegistryError(
                f"{self.role} multi-candidate agreement requires a final adjudicator"
            )
        if self.adjudicator is not None:
            if self.adjudicator.spec.role != "consensus_judge":
                raise RegistryError(f"{self.role} adjudicator must use consensus_judge role")
            if self.adjudicator.candidate_id in candidate_ids:
                raise RegistryError(f"{self.role} adjudicator candidate id must be unique")
            if self.adjudicator.rank in ranks:
                raise RegistryError(f"{self.role} adjudicator rank must be unique")
            self.adjudicator.validate()
        for candidate in self.candidates:
            candidate.validate()


PUBLIC_MODELS: tuple[ModelSpec, ...] = (
    ModelSpec(
        role="source_research",
        model_id="Qwen/Qwen3-14B",
        revision="40c069824f4251a91eefaf281ebe4c544efd3e18",
    ),
    ModelSpec(
        role="asr",
        model_id="openai/whisper-large-v3-turbo",
        revision="41f01f3fe87f28c78e2fbf8b568835947dd65ed9",
    ),
    ModelSpec(
        role="visual_grounding",
        model_id="microsoft/Florence-2-large-ft",
        revision="4a12a2b54b7016a48a22037fbd62da90cd566f2a",
    ),
    ModelSpec(
        role="multimodal_extraction",
        model_id="Qwen/Qwen3.5-9B",
        revision="c202236235762e1c871ad0ccb60c8ee5ba337b9a",
    ),
    ModelSpec(
        role="burned_area",
        model_id="ibm-nasa-geospatial/Prithvi-EO-2.0-300M-BurnScars",
        revision="a3f2c410e45b8ac7417976614528a872f024d831",
    ),
)

DFINE_FIREVIEWER = ModelSpec(
    role="fire_detection",
    model_id="fireviewer/dfine-xlarge-fire-smoke",
    revision="3b3c2171ec78f3d33a9031df512a839e912b36f2",
)

RTDETR_FIREVIEWER = ModelSpec(
    role="fire_detection",
    model_id="fireviewer/rtdetr-v2-r50-fire-smoke",
    revision="27ca0bcdebdb1b9b860932fc4935513a5dfc7652",
)
# Legacy import kept temporarily while callers migrate to the explicit ensemble name.
RTDETR_BASELINE = RTDETR_FIREVIEWER

# The MVP uses the same pinned text model for source research and rare final
# adjudication. It fits the A40 in BF16 and is loaded only after every stage
# candidate has been serialized and released from VRAM.
CONSENSUS_JUDGE = ModelSpec(
    role="consensus_judge",
    model_id="Qwen/Qwen3-14B",
    revision="40c069824f4251a91eefaf281ebe4c544efd3e18",
)


def detector_ensemble_enabled() -> bool:
    value = os.getenv("FW_ENABLE_FIRE_DETECTOR_ENSEMBLE")
    if value is None:
        # Temporary compatibility with already provisioned validation pods.
        value = os.getenv("FW_ENABLE_RTDETR_BASELINE", "false")
    return value.strip().lower() in {"1", "true", "yes", "on"}


rtdetr_baseline_enabled = detector_ensemble_enabled


def consensus_judge_enabled() -> bool:
    value = os.getenv("FW_ENABLE_CONSENSUS_JUDGE", "false")
    return value.strip().lower() in {"1", "true", "yes", "on"}


def enabled_public_models() -> tuple[ModelSpec, ...]:
    models = PUBLIC_MODELS
    if detector_ensemble_enabled():
        # Both trained detectors are provisioned. SessionRunner unloads one before
        # loading the other, so the A40 never keeps both checkpoints in VRAM.
        models = (
            *PUBLIC_MODELS[:2],
            DFINE_FIREVIEWER,
            RTDETR_FIREVIEWER,
            *PUBLIC_MODELS[2:],
        )
    if consensus_judge_enabled():
        models = (*models, CONSENSUS_JUDGE)
    return models


def build_registry() -> dict[ModelRole, ModelSpec]:
    registry: dict[ModelRole, ModelSpec] = {}
    for spec in enabled_public_models():
        registry.setdefault(spec.role, spec)
    checkpoint = os.getenv("FW_RTDETR_CHECKPOINT_PATH")
    digest = os.getenv("FW_RTDETR_CHECKPOINT_SHA256")
    if checkpoint or digest:
        if not checkpoint or not digest:
            raise RegistryError("RT-DETR path and SHA-256 digest must be configured together")
        path = Path(checkpoint)
        if not path.exists():
            raise RegistryError(f"RT-DETR checkpoint does not exist: {path}")
        weights = path / "model.safetensors" if path.is_dir() else path
        if not weights.is_file():
            raise RegistryError("RT-DETR directory must contain model.safetensors")
        digest_value = digest.removeprefix("sha256:")
        actual_digest = sha256()
        with weights.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                actual_digest.update(chunk)
        if actual_digest.hexdigest() != digest_value:
            raise RegistryError("RT-DETR checkpoint SHA-256 does not match configuration")
        spec = ModelSpec(
            role="fire_detection",
            model_id=str(path),
            revision=f"sha256:{digest_value}",
            source="local",
        )
        spec.validate()
        registry[spec.role] = spec
    for spec in registry.values():
        spec.validate()
    return registry


def build_model_group_registry() -> dict[ModelRole, ModelGroupSpec]:
    """Build execution groups for the models that can actually run in this image."""

    groups: dict[ModelRole, ModelGroupSpec] = {}
    registry = build_registry()
    for role, spec in registry.items():
        if role == "burned_area":
            # Burned-area inference consumes the native V2 satellite contract
            # after the legacy media session and has its own fail-closed gate.
            continue
        if role == "fire_detection" and detector_ensemble_enabled():
            rtdetr_spec = spec if spec.source == "local" else RTDETR_FIREVIEWER
            detector_specs = (DFINE_FIREVIEWER, rtdetr_spec)
            group = ModelGroupSpec(
                role=role,
                candidates=tuple(
                    ModelCandidateSpec(
                        candidate_id=(
                            "fire_detection.dfine.primary"
                            if rank == 1
                            else "fire_detection.rtdetr.challenger"
                        ),
                        spec=candidate,
                        rank=rank,
                    )
                    for rank, candidate in enumerate(detector_specs, start=1)
                ),
                strategy=ConsensusStrategy.QUORUM,
                minimum_successful=2,
                minimum_agreeing=2,
                agreement_threshold=0.4,
                disagreement_decision=ConsensusFailureDecision.HUMAN_REVIEW,
                adjudicator=ModelCandidateSpec(
                    candidate_id="fire_detection.qwen3_14b.judge",
                    spec=CONSENSUS_JUDGE,
                    rank=3,
                ),
                adjudication_confidence_threshold=0.65,
            )
            group.validate()
            groups[role] = group
            continue
        group = ModelGroupSpec(
            role=role,
            candidates=(
                ModelCandidateSpec(
                    candidate_id=f"{role}.primary",
                    spec=spec,
                    rank=1,
                ),
            ),
        )
        group.validate()
        groups[role] = group
    return groups


def resolve_cached_snapshot(spec: ModelSpec, cache_root: Path) -> Path:
    """Resolve only the exact pinned snapshot; never fall back to a floating ref or network."""
    if spec.source == "local":
        return Path(spec.model_id)
    repository = cache_root / f"models--{spec.model_id.replace('/', '--')}" / "snapshots"
    snapshot = repository / spec.revision
    if not snapshot.is_dir():
        raise RegistryError(f"pinned model snapshot is absent from cache: {snapshot}")
    return snapshot
