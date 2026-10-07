from app.adapters.base import RawListing, SearchFilters, SourceAdapter
from app.adapters.olx import OlxAdapter
from app.adapters.socarrao import SoCarraoAdapter
from app.adapters.webmotors import WebmotorsAdapter

ADAPTERS: dict[str, type[SourceAdapter]] = {
    OlxAdapter.name: OlxAdapter,
    WebmotorsAdapter.name: WebmotorsAdapter,
    SoCarraoAdapter.name: SoCarraoAdapter,
}

__all__ = ["ADAPTERS", "RawListing", "SearchFilters", "SourceAdapter"]
