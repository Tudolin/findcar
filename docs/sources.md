# Fontes

Status atual: **as duas fontes funcionam** (verificado ao vivo em 2026-10-06, com uma execução
completa: 139 anúncios, 125 veículos, 98% com FIPE e 14 carros unidos entre as fontes).

## Resumo

| fonte | como lê | onde estão os dados | detalhe do anúncio |
|---|---|---|---|
| **OLX** | Chromium headless (sempre) | cards `section.olx-adcard` renderizados no HTML | `<script id="initial-data" data-json>`: descrição, cor, câmbio, combustível, `professionalAd`, fotos, `has_auction` |
| **Webmotors** | HTTP; Chromium se for barrado | cards com link `/comprar/…/{id}` (h2 marca+modelo, h3 versão, p ano/km/cidade/preço); fallback `__NEXT_DATA__.props.pageProps.catalogProps.items` | JSON-LD `Car` (cor, câmbio, combustível, km, anos) + `Product` (preço, vendedor `AutoDealer` = loja) |

As fixtures em `tests/fixtures/` são páginas **reais** capturadas nessa data.

## OLX

- **Por que precisa de navegador:** o Cloudflare responde 403 a clientes HTTP (`curl`, `httpx`)
  mesmo de IP residencial; até o `robots.txt` é bloqueado. O bloqueio é pela assinatura TLS/HTTP
  da conexão, não pelo IP. Um Chromium real recebe a página normal. É a mesma estratégia do
  projeto findhome.
- **`__NEXT_DATA__`:** não existe mais nas páginas de busca (o findhome já registrava isso).
- **Busca:** `/autos-e-pecas/carros-vans-e-utilitarios/{marca}/{modelo}/estado-pr/regiao-de-curitiba-e-paranagua?o={página}&pe={preço máx}`.
  Se essa URL vier vazia, o adapter tenta sem a região e, por fim, a busca textual
  `?q=marca modelo`. São 50 anúncios por página.
- **Card:** título no padrão FIPE + ano ("Honda Fit LX 1.4/ 1.4 Flex 8v/16v 5P Mec. 2004");
  `aria-label` "235000 quilômetros rodados", "Cor Cinza"; local "Curitiba, Xaxim"; data
  "Hoje, 16:17". O câmbio vem do título: no padrão FIPE só as versões automáticas trazem "Aut.",
  então sem "Aut." o câmbio é inferido como manual, e o anúncio confirma depois.
- **Bloqueio:** o Cloudflare às vezes desafia uma requisição isolada. Nesse caso há **uma**
  nova tentativa depois de ~60 s; se bloquear de novo, a execução para e é registrada. Nenhum
  captcha é resolvido.

## Webmotors

- **Busca:** `/carros/{uf}/{marca}/{modelo}?tipoveiculo=carros&estadocidade=Paraná&marca1=HONDA&modelo1=FIT&precoate=50000&anode=2009&page=N`.
- **HTTP vs navegador:** de IP residencial a página vem com HTTP 200; de datacenter o
  PerimeterX bloqueia. O adapter tenta HTTP e, se for barrado ou a página vier sem anúncios,
  usa o Chromium no resto da execução.
- **User-Agent do navegador:** usa a versão real do Chromium. Um UA "Chrome/128" num Chromium
  153 é uma incoerência que o PerimeterX detecta.
- **robots.txt:** para `User-agent: *`, só `/api/detail/` é proibido, e não o usamos. As
  regras de `/comprar/` valem apenas para robôs de IA (GPTBot, ClaudeBot…).
- As classes CSS são CSS Modules com hash (`vehicle-card-desktop_Container__RbTrf`), que muda a
  cada deploy. Por isso o card é localizado pelo link do anúncio e lido por tag.

## FIPE

`https://fipe.parallelum.com.br/api/v2` é gratuita e funciona sem chave (um token gratuito
aumenta a cota). Respostas ficam em `fipe_cache` por 15–30 dias. O casamento usa marca →
modelos que começam com o nome do modelo → tokens da versão (cilindrada pesa mais) + câmbio →
anos disponíveis.

## Comportamento educado

1 requisição a cada 3–5 s por host, com jitter; retry com backoff exponencial para 5xx/429;
cache de respostas; no máximo `details.max_per_run` páginas de anúncio por execução (padrão 25);
3 execuções por dia por padrão; imagens, fontes e mídia não são baixadas pelo navegador.

## Como validar do servidor

```bash
docker compose exec app python -m app.cli probe olx        # busca + 1 detalhe
docker compose exec app python -m app.cli probe webmotors
```
