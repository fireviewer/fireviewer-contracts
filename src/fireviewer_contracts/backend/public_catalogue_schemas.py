"""Public catalogue contracts, separate from the strict text-only discovery v1."""

from datetime import datetime
from typing import Literal

from pydantic import Field

from fireviewer_contracts.backend.schemas import IncidentDiscoveryItem, StrictModel

ResourceKind = Literal["document", "map", "data", "model"]


class PublicCatalogueLocation(StrictModel):
    coordinates: tuple[float, float]
    horizontal_uncertainty_m: float = Field(gt=0)


class PublicCatalogueIncident(IncidentDiscoveryItem):
    location: PublicCatalogueLocation | None


class PublicIncidentCatalogue(StrictModel):
    items: list[PublicCatalogueIncident]
    next_cursor: str | None = None


class PublicResourceIncident(StrictModel):
    fire_id: str
    name: str


class PublicCatalogueResource(StrictModel):
    id: str
    kind: ResourceKind
    title: str
    incident: PublicResourceIncident | None = None
    published_at: datetime | None = None
    url: str
    action: Literal["download", "open"]
    media_type: str | None = None
    size_bytes: int | None = Field(default=None, gt=0)
    license: str | None = None


class PublicCatalogueSource(StrictModel):
    status: Literal["available", "unavailable"]
    checked_at: datetime | None = None


class PublicCatalogueSources(StrictModel):
    hugging_face: PublicCatalogueSource


class PublicResourceCatalogue(StrictModel):
    items: list[PublicCatalogueResource]
    next_cursor: str | None = None
    sources: PublicCatalogueSources
