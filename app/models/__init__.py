"""Database schema. JSON columns use the generic sa.JSON (JSONB on Postgres via variant)."""

from enum import StrEnum

import sqlalchemy as sa
from pydantic import NaiveDatetime
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel

from app.core.timeutil import utcnow

JSONType = sa.JSON().with_variant(JSONB(), "postgresql")


def json_col(default=dict):
    return Field(default_factory=default, sa_column=sa.Column(JSONType, nullable=False))


class Stage(StrEnum):
    NOVO = "novo"
    INTERESSANTE = "interessante"
    CONTATADO = "contatado"
    VISITADO = "visitado"
    DESCARTADO = "descartado"
    COMPRADO = "comprado"


STAGE_LABELS = {
    Stage.NOVO: "Novo",
    Stage.INTERESSANTE: "Interessante",
    Stage.CONTATADO: "Contatado",
    Stage.VISITADO: "Visitado",
    Stage.DESCARTADO: "Descartado",
    Stage.COMPRADO: "Comprado",
}


class SavedSearch(SQLModel, table=True):
    __tablename__ = "saved_search"
    id: int | None = Field(default=None, primary_key=True)
    name: str
    enabled: bool = True
    # {"models": [{"brand": "Honda", "model": "Fit"}], "max_price": 50000, "min_year": 2009,
    #  "max_km": null, "automatic_only": false, "state": "pr", "city": "curitiba",
    #  "cities": [...]}  (cities = optional allow-list, empty = any)
    filters: dict = json_col()
    sources: list = json_col(list)
    created_at: NaiveDatetime = Field(default_factory=utcnow)
    last_run_at: NaiveDatetime | None = None


class SourceStatus(SQLModel, table=True):
    __tablename__ = "source_status"
    name: str = Field(primary_key=True)
    enabled: bool = True
    last_run_at: NaiveDatetime | None = None
    last_success_at: NaiveDatetime | None = None
    last_status: str | None = None  # ok | error | blocked
    last_count: int = 0
    last_error: str | None = None
    blocked_since: NaiveDatetime | None = None


class SearchRun(SQLModel, table=True):
    __tablename__ = "search_run"
    id: int | None = Field(default=None, primary_key=True)
    saved_search_id: int | None = Field(default=None, foreign_key="saved_search.id", index=True)
    source: str = Field(index=True)
    started_at: NaiveDatetime = Field(default_factory=utcnow)
    finished_at: NaiveDatetime | None = None
    status: str = "running"  # running | ok | error | blocked
    n_found: int = 0
    n_new: int = 0
    n_price_changes: int = 0
    n_inactivated: int = 0
    error: str | None = None


class Vehicle(SQLModel, table=True):
    """A physical car; may be advertised by several listings (one per source)."""

    id: int | None = Field(default=None, primary_key=True)
    brand: str = Field(index=True)
    model: str = Field(index=True)
    version: str | None = None
    year_fab: int | None = None
    year_model: int | None = Field(default=None, index=True)
    km: int | None = None
    color: str | None = None
    transmission: str | None = None  # automatico | manual | None
    fuel: str | None = None
    city: str | None = None
    state: str | None = None
    seller_type: str | None = None  # loja | particular
    price: int | None = None  # best active price among listings
    active: bool = True
    stage: str = Field(default=Stage.NOVO, index=True)
    favorite: bool = False
    notes: str = ""
    score: int | None = None
    score_breakdown: list = json_col(list)
    red_flags: list = json_col(list)
    fipe_code: str | None = None
    fipe_model: str | None = None
    fipe_price: int | None = None
    fipe_ref: str | None = None
    fipe_diff_pct: float | None = None
    fipe_checked_at: NaiveDatetime | None = None
    created_at: NaiveDatetime = Field(default_factory=utcnow)
    updated_at: NaiveDatetime = Field(default_factory=utcnow)


class Listing(SQLModel, table=True):
    __table_args__ = (sa.UniqueConstraint("source", "external_id"),)
    id: int | None = Field(default=None, primary_key=True)
    source: str = Field(index=True)
    external_id: str
    url: str
    title: str = ""
    brand: str | None = None
    model: str | None = None
    version: str | None = None
    raw_brand: str | None = None
    raw_model: str | None = None
    raw_version: str | None = None
    year_fab: int | None = None
    year_model: int | None = None
    km: int | None = None
    transmission: str | None = None
    fuel: str | None = None
    color: str | None = None
    price: int | None = None
    last_price: int | None = None
    city: str | None = None
    neighborhood: str | None = None
    state: str | None = None
    seller_type: str | None = None
    seller_name: str | None = None
    photos: list = json_col(list)
    photo_hash: str | None = None
    description: str = ""
    published_at: NaiveDatetime | None = None
    first_seen: NaiveDatetime = Field(default_factory=utcnow)
    last_seen: NaiveDatetime = Field(default_factory=utcnow)
    active: bool = Field(default=True, index=True)
    missed_runs: int = 0
    detail_fetched: bool = False
    vehicle_id: int | None = Field(default=None, foreign_key="vehicle.id", index=True)
    match_confidence: float | None = None
    match_manual: bool = False


class SearchHit(SQLModel, table=True):
    """Which saved search found which listing (for per-search sold detection)."""

    __tablename__ = "search_hit"
    saved_search_id: int = Field(foreign_key="saved_search.id", primary_key=True)
    listing_id: int = Field(foreign_key="listing.id", primary_key=True)
    last_seen: NaiveDatetime = Field(default_factory=utcnow)


class PricePoint(SQLModel, table=True):
    __tablename__ = "price_history"
    id: int | None = Field(default=None, primary_key=True)
    listing_id: int = Field(foreign_key="listing.id", index=True)
    observed_at: NaiveDatetime = Field(default_factory=utcnow, index=True)
    price: int


class Alias(SQLModel, table=True):
    """raw text → canonical name. kind: brand | model | version."""

    id: int | None = Field(default=None, primary_key=True)
    kind: str = Field(index=True)
    pattern: str  # normalized text (see services.normalize.norm)
    canonical: str
    brand: str | None = None  # scope for model/version aliases
    model: str | None = None  # scope for version aliases


class ModelSpec(SQLModel, table=True):
    __tablename__ = "model_spec"
    id: int | None = Field(default=None, primary_key=True)
    brand: str
    model: str
    consumption_city: float | None = None  # km/l gasolina (aprox.)
    consumption_road: float | None = None
    notes: str = ""


class FipeCache(SQLModel, table=True):
    __tablename__ = "fipe_cache"
    key: str = Field(primary_key=True)  # request path
    payload: dict | list = Field(sa_column=sa.Column(JSONType, nullable=False))
    fetched_at: NaiveDatetime = Field(default_factory=utcnow)


class RedFlagRule(SQLModel, table=True):
    __tablename__ = "red_flag_rule"
    id: int | None = Field(default=None, primary_key=True)
    label: str
    pattern: str  # regex, case-insensitive, matched on title + version + description
    penalty: int = 10
    enabled: bool = True


class AppSetting(SQLModel, table=True):
    __tablename__ = "app_setting"
    key: str = Field(primary_key=True)
    value: dict | list | int | float | str | bool | None = Field(
        default=None, sa_column=sa.Column(JSONType)
    )


class AlertLog(SQLModel, table=True):
    __tablename__ = "alert_log"
    id: int | None = Field(default=None, primary_key=True)
    kind: str
    dedupe_key: str = Field(index=True, unique=True)
    vehicle_id: int | None = Field(default=None, foreign_key="vehicle.id")
    message: str
    sent: bool = False
    created_at: NaiveDatetime = Field(default_factory=utcnow)


class KanbanEvent(SQLModel, table=True):
    __tablename__ = "kanban_event"
    id: int | None = Field(default=None, primary_key=True)
    vehicle_id: int = Field(foreign_key="vehicle.id", index=True)
    from_stage: str | None = None
    to_stage: str
    at: NaiveDatetime = Field(default_factory=utcnow)
