# Fontes — status da investigação

> **Status: NÃO VERIFICADO.** O container onde esta investigação rodou tem política
> de rede que bloqueia `www.olx.com.br` e `www.webmotors.com.br` (proxy responde 403 no
> CONNECT). Nada abaixo foi observado em tráfego real; são **hipóteses** a confirmar
> assim que o acesso for liberado (ou com HARs/HTML salvos por você).

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
