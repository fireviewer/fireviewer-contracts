from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any
from urllib.parse import urlsplit

from fireviewer_contracts.contracts import BatchItem, ItemResult, WorkerInputV2


class OutputValidationError(ValueError):
    pass


FORBIDDEN_KEYS = frozenset(
    {
        "ai_estimated_location",
        "estimated_location",
        "fire_location",
        "inferred_location",
        "predicted_spread",
        "probable_location",
        "projection",
        "risk_score",
        "threatened_area",
    }
)

FORBIDDEN_LANGUAGE = re.compile(
    r"\b(?:probablement|sans\s+doute|devrait|pourrait\s+atteindre|semble\s+se\s+diriger|"
    r"on\s+peut\s+supposer|prévision|projection|probable|infér(?:é|ée|er))\b",
    flags=re.IGNORECASE,
)


def validate_internal_urls(items: Iterable[BatchItem], allowed_hosts: frozenset[str]) -> None:
    if not allowed_hosts:
        raise OutputValidationError("FW_ALLOWED_MEDIA_HOSTS must contain at least one hostname")
    for item in items:
        urls = [item.working_file_url, item.audio_url]
        urls.extend(frame.working_file_url for frame in item.frames)
        for value in urls:
            if value is None:
                continue
            parsed = urlsplit(str(value))
            if parsed.scheme != "https":
                raise OutputValidationError("media URLs must use HTTPS")
            if parsed.username or parsed.password:
                raise OutputValidationError("media URLs must not contain user information")
            if parsed.hostname not in allowed_hosts:
                raise OutputValidationError(f"media URL host is not allowed: {parsed.hostname}")


def validate_v2_internal_urls(batch: WorkerInputV2, allowed_hosts: frozenset[str]) -> None:
    validate_internal_urls(batch.items, allowed_hosts)  # type: ignore[arg-type]
    if batch.reference_bundle is None:
        return
    for asset in batch.reference_bundle.assets:
        parsed = urlsplit(str(asset.working_file_url))
        if parsed.scheme != "https":
            raise OutputValidationError("reference URLs must use HTTPS")
        if parsed.username or parsed.password:
            raise OutputValidationError("reference URLs must not contain user information")
        if parsed.hostname not in allowed_hosts:
            raise OutputValidationError(f"reference URL host is not allowed: {parsed.hostname}")


def _walk(value: Any, path: str = "output") -> Iterable[tuple[str, Any]]:
    if isinstance(value, Mapping):
        for key, child in value.items():
            child_path = f"{path}.{key}"
            yield child_path, child
            yield from _walk(child, child_path)
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            child_path = f"{path}[{index}]"
            yield child_path, child
            yield from _walk(child, child_path)


def validate_forbidden_output(value: Any) -> None:
    dumped = value.model_dump(mode="json") if hasattr(value, "model_dump") else value
    for path, child in _walk(dumped):
        key = path.rsplit(".", 1)[-1].lower()
        if key in FORBIDDEN_KEYS:
            raise OutputValidationError(f"forbidden output field: {path}")
        if isinstance(child, str) and FORBIDDEN_LANGUAGE.search(child):
            raise OutputValidationError(f"forbidden speculative language: {path}")


def validate_evidence_links(source: BatchItem, result: ItemResult) -> None:
    evidence_ids = {source.input_id, "metadata", "article_text"}
    evidence_ids.update(frame.frame_id for frame in source.frames)
    evidence_ids.update(segment.segment_id for segment in result.transcript.segments)
    region_ids = {region.region_id for region in result.pixel_regions}

    selection_ids = [selection.evidence_id for selection in result.visual_evidence_selection]
    if len(selection_ids) != len(set(selection_ids)):
        raise OutputValidationError("visual evidence selection contains duplicate evidence ids")
    visual_ids = {frame.frame_id for frame in source.frames}
    if not visual_ids and source.working_file_url is not None:
        visual_ids.add(source.input_id)
    if set(selection_ids) != visual_ids:
        raise OutputValidationError(
            "visual evidence selection must cover every visual input exactly"
        )
    if visual_ids and not any(
        selection.selected_for_grounding for selection in result.visual_evidence_selection
    ):
        raise OutputValidationError("at least one visual input must remain selected")

    for region in result.pixel_regions:
        if region.evidence_id not in evidence_ids:
            raise OutputValidationError(
                f"region {region.region_id} references unknown evidence {region.evidence_id}"
            )
    for collection_name, collection in (
        ("factual_observations", result.factual_observations),
        ("explicit_places", result.explicit_places),
        ("explicit_times", result.explicit_times),
    ):
        for entry in collection:
            if entry.evidence_id not in evidence_ids:
                raise OutputValidationError(
                    f"{collection_name} references unknown evidence {entry.evidence_id}"
                )
            region_id = getattr(entry, "region_id", None)
            if region_id is not None and region_id not in region_ids:
                raise OutputValidationError(
                    f"{collection_name} references unknown pixel region {region_id}"
                )


def validate_item_result(source: BatchItem, result: ItemResult) -> None:
    validate_forbidden_output(result)
    has_location = source.metadata.latitude is not None
    if has_location != result.metadata_result.capture_location_available:
        raise OutputValidationError("capture location availability does not match source metadata")
    validate_evidence_links(source, result)
    marker = result.geographic_marker_candidate
    if marker is not None:
        if not has_location:
            raise OutputValidationError("a geographic marker requires explicit source coordinates")
        if marker.type != "media_capture":
            raise OutputValidationError("only a media capture marker is allowed")
        if marker.geometry_origin != source.metadata.location_origin:
            raise OutputValidationError("marker origin must match source metadata")
