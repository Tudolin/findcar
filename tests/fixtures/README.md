# Fixtures

| arquivo | origem |
|---|---|
| `fipe/*.json` | **reais**, baixados de `fipe.parallelum.com.br/api/v2` em 2026-10-06 |
| `webmotors/search_fit.html` | **sintético** — formato `__NEXT_DATA__` esperado; substituir por captura real |
| `olx/*.html` | **sintético** — OLX bloqueia (Cloudflare 403); formato não verificado |

Para gerar fixtures reais a partir do homelab (IP residencial):

```bash
docker compose exec app python -m app.cli capture webmotors /data/fixtures --brand Honda --model Fit
docker compose cp app:/data/fixtures/webmotors_search.html tests/fixtures/webmotors/search_fit.html
```
