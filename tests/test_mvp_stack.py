from __future__ import annotations

import json

from fireviewer_contracts.model_registry import (
    DFINE_FIREVIEWER,
    PUBLIC_MODELS,
    RTDETR_FIREVIEWER,
)
from fireviewer_contracts.mvp_stack import (
    ImplementationStatus,
    StageActivation,
    load_mvp_stack,
    mvp_stack_digest,
)


def test_frozen_mvp_is_a40_only_and_fail_closed() -> None:
    stack = load_mvp_stack()

    assert stack.stack_id == "firewarning-mvp-a40-v1"
    assert stack.hardware.gpu == "NVIDIA A40"
    assert stack.hardware.vram_gib == 48
    assert stack.hardware.dtype == "bfloat16"
    assert stack.hardware.attention == "flash_attention_2"
    assert stack.hardware.quantization == "none"
    assert stack.hardware.execution == "strictly_sequential"
    assert stack.hardware.maximum_large_models_in_vram == 1
    assert stack.auto_publication is False
    assert stack.human_validation_required is True
    assert stack.private_results_on_abstention is True
    assert len(mvp_stack_digest(stack)) == 64


def test_unavailable_candidates_are_never_current_or_provisioned() -> None:
    stack = load_mvp_stack()
    executable = {
        ImplementationStatus.INTEGRATED,
        ImplementationStatus.BASELINE_ONLY,
    }

    for stage in stack.stages:
        for candidate in stage.candidates:
            if candidate.status not in executable:
                assert candidate.current_candidate is False
                assert candidate.provisioned_by_mvp is False
        if stage.activation == StageActivation.CLOSED:
            assert all(not candidate.current_candidate for candidate in stage.candidates)


def test_every_current_runtime_model_is_represented_by_the_frozen_stack() -> None:
    stack = load_mvp_stack()
    manifest_models = {
        (candidate.model_id, candidate.revision)
        for stage in stack.stages
        for candidate in stage.candidates
        if candidate.revision is not None
    }

    for spec in (*PUBLIC_MODELS, DFINE_FIREVIEWER, RTDETR_FIREVIEWER):
        assert (spec.model_id, spec.revision) in manifest_models


def test_large_final_judge_is_pinned_qwen3_14b() -> None:
    stack = load_mvp_stack()

    assert stack.judge.candidate.model_id == "Qwen/Qwen3-14B"
    assert stack.judge.candidate.revision == "40c069824f4251a91eefaf281ebe4c544efd3e18"
    assert stack.judge.visual_disagreement_without_direct_evidence == "abstain"


def test_deferred_experimental_models_are_absent_from_the_mvp_manifest() -> None:
    payload = json.dumps(load_mvp_stack().model_dump(mode="json"), sort_keys=True).lower()

    for deferred in ("artifixer", "locateanything", "vipe", "cosmos", "a6000"):
        assert deferred not in payload
