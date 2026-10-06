# Fontes — status da investigação

> **Status (2026-10-06): BLOQUEADO a partir de IP de datacenter.** Investigação parada
> conforme a regra do projeto (não burlar bloqueios). Os itens marcados como hipótese
> ainda precisam ser confirmados a partir de um IP residencial (homelab) ou de HAR salvo.

## Observado em 2026-10-06 (container em nuvem, IP de datacenter, httpx/curl com UA de Chrome)
| fonte | resultado | proteção |
|---|---|---|
| OLX (`/autos-e-pecas/...?pe=50000&rs=32`) | **403** "Attention Required! \| Cloudflare" (até `robots.txt` deu 403) | Cloudflare WAF/Bot Management (`__cf_bm`) |
| Webmotors (`/carros/pr-curitiba?...`) | **403** "Access to this page has been denied" com captcha | PerimeterX/HUMAN (app `PX7Vv0zOst`), via CloudFront |
| FIPE (`fipe.parallelum.com.br/api/v2`) | **200** JSON, sem chave | — |

Webmotors `robots.txt`: `Disallow: /api/detail/`, `/comprar/` (com Allow por marca) e uma seção
para bots de IA (GPTBot, ClaudeBot etc.). As páginas de busca `/carros/...` não aparecem como Disallow
no trecho lido. O scraper do carwatch deve respeitar isso e não usar `/api/detail/`.

Implicação: o adapter precisa rodar a partir do homelab (IP residencial), em baixo volume.
Se o bloqueio persistir também de lá, a fonte fica desativada e marcada `blocked` em `/health`;
não haverá tentativa de contornar o captcha nem fingerprinting.

## Checklist de investigação (por fonte)

Para cada site, em ordem de preferência: (a) endpoint JSON do frontend → (b) JSON
embutido (`__NEXT_DATA__` etc.) → (c) HTML → (d) Playwright.

- [ ] URL de busca e parâmetros (preço máx, ano mín, ordenação, região, câmbio, paginação)
- [ ] `robots.txt` e termos de uso (o que é permitido)
- [ ] Resposta a `httpx` com UA realista: 200, 403, challenge (Cloudflare/Akamai/PerimeterX)?
- [ ] Existência de JSON embutido / endpoint XHR; campos disponíveis na listagem vs detalhe
- [ ] Paginação, limite de páginas, total de resultados
- [ ] Identificador estável do anúncio (id_externo)
- [ ] Fotos (URLs), data de publicação, tipo de vendedor, km, câmbio, cor

## OLX (hipóteses)
- Site em Next.js; a listagem costuma trazer JSON em `__NEXT_DATA__` (`props.pageProps.ads`).
- Filtros via query string (`pe=` preço máx, `rs=` ano mín, `o=` página/ordenação — **a confirmar**).
- Proteção anti-bot provável; se houver captcha/403 → parar e registrar (sem burlar).

## Webmotors (hipóteses)
- Frontend consome API JSON interna de busca (POST/GET com filtros) — **a confirmar na aba Network**.
- Possível proteção anti-bot (Akamai/Cloudflare) e exigência de headers específicos.
- Se a API exigir tokens/cookies de sessão gerados por JS, cair para Playwright só como fallback.

## FIPE (hipótese)
- API pública gratuita (ex.: parallelum "fipe.online" v2) com limite de requisições; cachear tudo em tabela.
