# carwatch — arquitetura e schema

> Implementado. Os nomes finais das tabelas estão em `app/models/__init__.py`; as diferenças em relação à proposta estão abaixo.
> Diferenças: sem tabela `source` (há `source_status`), `search_hit` liga busca↔anúncio para detectar vendidos
> por busca, `app_setting` (chave/valor) guarda pesos/alertas/agenda, `vehicle_match` virou colunas
> `match_confidence`/`match_manual` em `listing`, e `model_spec` guarda o consumo aproximado.

## Serviços (docker-compose, projeto `carwatch`)
| serviço | função | porta |
|---|---|---|
| `db` | PostgreSQL 16, volume `pgdata` | interna |
| `migrate` | one-shot `alembic upgrade head` + seed | — |
| `app` | FastAPI + Jinja2/HTMX/Alpine/Chart.js **e** APScheduler (jobs de scraping) no mesmo processo | `${BIND_ADDRESS:-127.0.0.1}:8000` (Tailscale: usar IP tailnet) |

Decisão: manter simples — um único container de app. Playwright/chromium fica opcional (build arg), só se alguma fonte exigir.
APIs: só gratuitas. FIPE via `https://fipe.parallelum.com.br/api/v2` (sem chave; token gratuito opcional aumenta o limite), com cache em `fipe_cache`.
Segredos só via `.env` (`POSTGRES_PASSWORD`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `FIPE_*`).

## Estrutura
```
app/
  api/ (routers HTMX/JSON)   adapters/ (base.py, olx.py, webmotors.py)
  services/ (ingest, pricing, dedupe, fipe, scoring, alerts, compare)
  models/ (SQLModel)   templates/   core/ (config, logging JSON, http client c/ rate limit+cache)
  scheduler.py
alembic/  tests/ (fixtures/olx, fixtures/webmotors)  docs/
```

## Adapter
```python
class SourceAdapter(Protocol):
    name: str
    def search(self, filters: SearchFilters) -> Iterable[RawListing]: ...
    def fetch_detail(self, url: str) -> RawListing: ...
```
Cliente HTTP compartilhado: rate limit 3–5 s + jitter, retry com backoff, cache em disco/DB,
UA realista. Bloqueio/captcha ⇒ `BlockedError` → job para, `source_run.status='blocked'`, alerta no Telegram.

## Schema (tabelas principais)
- `source` (id, name, enabled, min_interval)
- `saved_search` (id, name, filters JSONB, sources[], schedule_cron, enabled)
- `source_run` (id, source_id, saved_search_id, started_at, finished_at, status, n_listings, error)
- `vehicle` (id, brand_id, model_id, version_id, year_fab, year_model, km, color, city, fuel, transmission, status_kanban, notes, score, score_breakdown JSONB, fipe_code, fipe_price)
- `listing` (id, vehicle_id FK, source, external_id, url, title, raw_brand/model/version, price, km, seller_type, description, photos JSONB[urls], published_at, first_seen, last_seen, missed_runs, active, last_price) — UNIQUE(source, external_id)
- `price_history` (listing_id, observed_at, price) — escrita só quando o preço muda
- `vehicle_match` (vehicle_id, listing_id, confidence, method, manual: bool) — suporta unir/separar manual
- `brand`, `model`, `version`, `alias` (kind, raw_text → canonical_id) — normalização
- `fipe_cache` (fipe_code, ref_month, price, fetched_at)
- `red_flag_rule` (keyword/regex, weight, scope) ; `score_weights` (chave, peso)
- `alert_rule`, `alert_log` (dedupe de notificações)
- `kanban_event` (vehicle_id, from, to, at, note)
Dedup: bucket por modelo+ano ± km → score ponderado (km, cor, cidade, hash/similaridade de foto, TF-IDF da descrição).
Inativo: `missed_runs >= N` (padrão 3) ⇒ `active=false`, mantém `last_price`.

## Fases
1. MVP: OLX + histórico + lista; 2. Webmotors; 3. dedupe; 4. FIPE; 5. score; 6. comparativo/CSV;
7. Kanban; 8. Telegram; 9. CI/CD (ruff, pytest, GHCR minúsculo, deploy em runner self-hosted, como no findhome).
