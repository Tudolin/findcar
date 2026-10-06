# carwatch

Monitor self-hosted de carros usados (Webmotors; OLX quando liberar). Ele guarda o histórico
de preços, une o mesmo carro anunciado em fontes diferentes, compara com a FIPE, dá um score
explicado (0–100) e avisa no Telegram. Uso pessoal, um usuário, roda no homelab com Docker Compose.

**Stack:** Python 3.12 · FastAPI · SQLModel/SQLAlchemy · Alembic · PostgreSQL 16 · httpx ·
APScheduler · Jinja2 + HTMX + Alpine.js + Chart.js (sem build step, libs servidas localmente).

## Funcionalidades

| | |
|---|---|
| Buscas salvas | CRUD em **Buscas**; cada busca ativa roda nos horários de **Configurações → Agenda** |
| Histórico de preços | toda mudança vira um ponto em `price_history`; gráfico por anúncio |
| Vendido/inativo | anúncio que some por N execuções (padrão 3) fica inativo e mantém o último preço |
| Deduplicação | modelo + ano + km + cor + cidade + versão + preço + hash da 1ª foto + descrição → confiança; une automaticamente ≥ 0,75, sugere ≥ 0,5; unir/separar manual |
| FIPE | API gratuita `fipe.parallelum.com.br/api/v2`, cache em `fipe_cache`, % acima/abaixo |
| Score | preço vs FIPE, km/ano, idade, câmbio, vendedor, consumo; red flags por regex (leilão, sinistro, repasse, “no estado”, Powershift, AL4…); cada ponto é explicado |
| Comparativo | até 6 veículos, melhor valor de cada linha destacado, export CSV |
| Decisão (Kanban) | Novo → Interessante → Contatado → Visitado → Descartado/Comprado, com notas |
| Telegram | anúncio novo com score ≥ X, queda ≥ Y% ou R$ Z, favorito inativo, resumo diário, fonte bloqueada |
| Dashboard | preço mediano semanal por modelo/ano, distribuição de km, ativos por fonte |
| Saúde | `/health` (página) e `/health.json`: última execução, sucesso, nº de anúncios e erros de cada fonte |

## Status das fontes (ver `docs/sources.md`)

- **Webmotors:** funciona a partir de IP residencial (HTTP 200, dados em `__NEXT_DATA__`). De IP de
  datacenter, o PerimeterX bloqueia. O parser localiza o array de anúncios por heurística; a
  fixture de teste ainda é **sintética**, até ser trocada por uma captura real (veja abaixo).
- **OLX:** o Cloudflare devolve 403 mesmo do IP residencial, inclusive no `robots.txt`. A fonte
  vem **desativada**; o adapter existe, mas não foi validado.
- Em bloqueio ou captcha a execução para, registra `blocked` e manda um alerta. **Não há
  tentativa de contornar.**

## Setup (Debian + Dockge)

```bash
# 1. Stack no Dockge
sudo mkdir -p /opt/stacks/carwatch && cd /opt/stacks/carwatch
curl -fsSLo compose.yaml https://raw.githubusercontent.com/Tudolin/findcar/main/docker-compose.yml
curl -fsSLo .env https://raw.githubusercontent.com/Tudolin/findcar/main/.env.example
nano .env        # POSTGRES_PASSWORD, BIND_ADDRESS, Telegram (opcional)

# 2. Subir (ou "Deploy" no Dockge)
docker compose up -d
docker compose ps        # db e app "healthy"; migrate "exited (0)"
```

O repositório é privado, então rode antes `docker login ghcr.io` com um PAT `read:packages`,
ou use `docker compose up -d --build` a partir de um clone.

**Acesso pelo Tailscale:** só o `app` publica porta, em `${BIND_ADDRESS}:${APP_PORT}`. Duas opções:
- `BIND_ADDRESS=<IP 100.x do host>` (`tailscale ip -4`), acessível só pela tailnet; ou
- `BIND_ADDRESS=127.0.0.1` + `tailscale serve --bg 8000`, que dá HTTPS em `https://<host>.<tailnet>.ts.net`.

O Postgres não publica porta.

### No celular (app instalável)

A interface é responsiva e foi pensada para toque:
- barra de abas inferior e menu "Mais"
- alvos de toque de 44px
- campos com 16px (o iPhone não dá zoom)
- filtros recolhíveis
- galeria com swipe
- Kanban com colunas deslizáveis e "segurar para arrastar"
- respeita a área segura do iPhone
- tema claro/escuro automático

Também é um **PWA**, então dá para instalar na tela inicial e abrir em tela cheia, como um app:

1. Instale o Tailscale no celular e entre na mesma tailnet.
2. A instalação exige **HTTPS**. No servidor: `tailscale serve --bg 8000`, mantendo
   `BIND_ADDRESS=127.0.0.1`. Abra `https://<host>.<tailnet>.ts.net`.
3. Android/Chrome: menu ⋮ → **Instalar app**. iPhone/Safari: Compartilhar → **Adicionar à Tela de Início**.

Sem rede, o app mostra uma tela "sem conexão" em vez de dados velhos. Preços sempre vêm do
servidor.

### Variáveis de ambiente

| variável | padrão | uso |
|---|---|---|
| `POSTGRES_PASSWORD` | — (obrigatória) | senha do banco |
| `POSTGRES_USER` / `POSTGRES_DB` | `carwatch` | |
| `BIND_ADDRESS` / `APP_PORT` | `127.0.0.1` / `8000` | onde a UI escuta |
| `TAG` | `latest` | tag da imagem no GHCR |
| `TIMEZONE` | `America/Sao_Paulo` | agenda e exibição |
| `SCHEDULER_ENABLED` | `true` | liga o agendador interno |
| `HTTP_MIN_DELAY` / `HTTP_MAX_DELAY` | `3` / `5` | intervalo (s) entre requisições ao mesmo host, com jitter |
| `HTTP_CACHE_TTL` | `1800` | cache de respostas em disco (s) |
| `FIPE_TOKEN` | vazio | token gratuito opcional da FIPE (aumenta a cota) |
| `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` | vazio | alertas |
| `LOG_LEVEL` | `INFO` | logs em JSON no stdout |

Pesos do score, red flags, limites de alerta, horários, limiar de dedup e consumo por modelo
ficam no banco e são editados em **Configurações**.

### Primeira execução recomendada

```bash
docker compose exec app python -m app.cli probe webmotors              # 1 página, não grava nada
docker compose exec app python -m app.cli capture webmotors /data/fx   # salva o HTML bruto
docker compose cp app:/data/fx/webmotors_search.html ./webmotors_search.html
```

Se o `probe` retornar anúncios, rode **Buscas → Rodar agora**. Se vier `BLOQUEADO`, a fonte
fica registrada como bloqueada; não insista. Mande o HTML capturado para virar a fixture real
dos testes.

> **Sem login:** o app é de usuário único e não tem autenticação. A proteção é a rede: mantenha
> `BIND_ADDRESS` em 127.0.0.1 ou no IP da tailnet; nunca exponha a porta na internet.

## CI/CD

- `ci.yml`: ruff, pytest (SQLite e fixtures, sem acessar os sites), `alembic upgrade` +
  `alembic check` no Postgres 16.
- `release.yml`: em push na `main` ou tag `v*`, roda o CI e publica `ghcr.io/tudolin/findcar`
  (nome em minúsculas) com as tags `latest`, `sha-xxxxxxx` e semver.
- `deploy.yml`: depois de um Release bem-sucedido, roda no **self-hosted runner** com label
  `carwatch-server`. Copia o compose para `/opt/stacks/carwatch/compose.yaml` (a variável
  `CARWATCH_DIR` muda o caminho), faz `pull` e `up -d` e espera o healthcheck. O `.env` do
  servidor nunca é tocado.

Backup: `deploy/backup.sh` (pg_dump com gzip e retenção de 14 dias), para rodar via cron.

## Desenvolvimento

```bash
uv venv -p 3.12 && uv pip install -e ".[dev]"
pytest && ruff check .
# Postgres local + app
export DATABASE_URL=postgresql+psycopg://carwatch:carwatch@localhost:5432/carwatch DATA_DIR=./data
alembic upgrade head && python -m app.seed
python scripts/demo_data.py        # SÓ em banco de dev: dados fictícios para ver a UI
uvicorn app.main:app --reload
```

Estrutura:
```
app/
  adapters/   base.py (SourceAdapter, RawListing, SearchFilters), webmotors.py, olx.py, nextdata.py
  services/   ingest, runner, vehicles, dedupe, fipe, scoring, alerts, telegram, stats, compare, scheduler
  api/        pages.py (GET), actions.py (POST), health.py, templating.py
  models/     schema SQLModel          core/  config, db, http (cliente educado), logging JSON
  templates/  Jinja2 + HTMX/Alpine     static/ css, js, vendor (htmx, alpine, chart.js, sortable)
alembic/      migrações                tests/  pytest + fixtures
```

## Como adicionar uma fonte (ex.: iCarros)

1. Investigue a fonte e documente em `docs/sources.md`. Ordem de preferência: endpoint JSON do
   frontend, JSON embutido, HTML e, por último, Playwright. Respeite o `robots.txt`.
2. Crie `app/adapters/icarros.py`:
   ```python
   class ICarrosAdapter(SourceAdapter):
       name, label = "icarros", "iCarros"
       def build_url(self, f: SearchFilters, page: int) -> str: ...
       def search(self, f: SearchFilters) -> list[RawListing]:
           html = self.client.get_text(self.build_url(f, 1))  # rate limit, cache, retry, bloqueio
           return [i for i in self.parse_search(html) if passes_filters(i, f)]
       def fetch_detail(self, url) -> RawListing | None: ...   # opcional
   ```
   Use sempre `self.client` (`PoliteClient`): ele aplica o intervalo, faz backoff e levanta
   `BlockedError`. Devolva strings cruas; a normalização (aliases, câmbio, vendedor) é feita
   na ingestão.
3. Registre em `app/adapters/__init__.py` (`ADAPTERS`) e adicione `"icarros"` em
   `SOURCE_LABELS` (`app/api/templating.py`) e no formulário de busca.
4. Salve uma resposta real em `tests/fixtures/icarros/` e escreva o teste de parsing.
5. O registro `SourceStatus` é criado na primeira execução; ative/desative em **Configurações → Fontes**.
