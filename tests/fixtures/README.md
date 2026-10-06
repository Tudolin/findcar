# Fixtures

| arquivo | origem |
|---|---|
| `fipe/*.json` | **reais**, `fipe.parallelum.com.br/api/v2` (2026-10-06) |
| `webmotors/search_fit.html.gz` | **real**, busca Honda Fit PR ≤ R$ 50 mil, ≥ 2009 (2026-10-06), via Chromium |
| `webmotors/detail.html.gz` | **real**, anúncio 80053571 |
| `olx/search_fit.html.gz` | **real**, busca Honda Fit PR ≤ R$ 50 mil (2026-10-06), via Chromium |
| `olx/detail.html.gz` | **real**, anúncio 1540279843 |

O HTML foi enxugado (sem `<svg>`, `<style>` e scripts que não carregam dados) e comprimido.
Para atualizar quando um site mudar o layout:

```bash
docker compose exec app python -m app.cli capture olx /data/fx --brand Honda --model Fit
docker compose cp app:/data/fx/olx_search.html - | gzip -9 > tests/fixtures/olx/search_fit.html.gz
```
