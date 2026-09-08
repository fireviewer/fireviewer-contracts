from __future__ import annotations
from typing import Any, Final, Literal
from pydantic import Field, model_validator
from fireviewer_contracts.contracts import SafeIdentifierV2, Sha256HexV2, StrictModel, WorkerInputV2
MEGALOC_REVISION = "37bb43d65dd6388d1578052de5eb0bcdceb497e7"
PRITHVI_REVISION = "a3f2c410e45b8ac7417976614528a872f024d831"
REQUEST_SCHEMA: Final[Literal["fireviewer.geo-gpu-request.v1"]] = (
    "fireviewer.geo-gpu-request.v1"
)
RESPONSE_SCHEMA: Final[Literal["fireviewer.geo-gpu-response.v1"]] = (
    "fireviewer.geo-gpu-response.v1"
)
ARTIFACT_SCHEMA: Final[Literal["fireviewer.sagemaker-geo-model-artifact.v1"]] = (
    "fireviewer.sagemaker-geo-model-artifact.v1"
)


class ArtifactFile(StrictModel):
    path: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,511}$")
    byte_size: int = Field(gt=0)
    sha256: Sha256HexV2


class GeoModelArtifactManifest(StrictModel):
    schema_name: Literal["fireviewer.sagemaker-geo-model-artifact.v1"] = Field(
        default=ARTIFACT_SCHEMA,
        alias="schema",
        serialization_alias="schema",
    )
    megaloc_revision: Literal["37bb43d65dd6388d1578052de5eb0bcdceb497e7"]
    prithvi_revision: Literal["a3f2c410e45b8ac7417976614528a872f024d831"]
    files: tuple[ArtifactFile, ...] = Field(min_length=6, max_length=32)

    @model_validator(mode="after")
    def validate_unique_files(self) -> GeoModelArtifactManifest:
        paths = [item.path for item in self.files]
        if len(paths) != len(set(paths)):
            raise ValueError("model artifact paths must be unique")
        return self


class EncodedPayload(StrictModel):
    input_id: SafeIdentifierV2
    content_type: str = Field(min_length=3, max_length=128)
    content_sha256: Sha256HexV2
    content_base64: str = Field(min_length=4, max_length=360_000_000)


class GeoGpuRequest(StrictModel):
    schema_name: Literal["fireviewer.geo-gpu-request.v1"] = Field(
        default=REQUEST_SCHEMA,
        alias="schema",
        serialization_alias="schema",
    )
    request_id: SafeIdentifierV2
    operation: Literal["megaloc.encode", "prithvi.burned_area"]
    payloads: tuple[EncodedPayload, ...] = Field(min_length=1, max_length=16)
    worker_input: WorkerInputV2 | None = None

    @model_validator(mode="after")
    def validate_operation_shape(self) -> GeoGpuRequest:
        payload_ids = [item.input_id for item in self.payloads]
        if len(payload_ids) != len(set(payload_ids)):
            raise ValueError("payload input identifiers must be unique")
        if self.operation == "megaloc.encode":
            if self.worker_input is not None:
                raise ValueError("MegaLoc requests cannot include a worker_input")
            if any(not item.content_type.startswith("image/") for item in self.payloads):
                raise ValueError("MegaLoc accepts image payloads only")
            return self
        if self.worker_input is None:
            raise ValueError("Prithvi requests require worker_input metadata")
        worker_ids = [item.input_id for item in self.worker_input.items]
        if set(payload_ids) != set(worker_ids) or len(payload_ids) != len(worker_ids):
            raise ValueError("Prithvi payloads must match every worker input item")
        if any(item.content_type not in {"image/tiff", "image/geotiff"} for item in self.payloads):
            raise ValueError("Prithvi accepts GeoTIFF payloads only")
        return self


class GeoGpuResponse(StrictModel):
    schema_name: Literal["fireviewer.geo-gpu-response.v1"] = Field(
        default=RESPONSE_SCHEMA,
        alias="schema",
        serialization_alias="schema",
    )
    request_id: SafeIdentifierV2
    operation: Literal["megaloc.encode", "prithvi.burned_area"]
    status: Literal["completed", "abstained"]
    model_id: str = Field(min_length=1, max_length=500)
    model_revision: str = Field(pattern=r"^[0-9a-f]{40}$")
    result: dict[str, Any]
    reason_codes: tuple[str, ...] = Field(default=(), max_length=16)


