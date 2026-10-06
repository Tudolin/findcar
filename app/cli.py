"""carwatch CLI.

  python -m app.cli probe webmotors        # 1 page of the default search, no DB writes
  python -m app.cli run [--search ID]      # run saved searches now
  python -m app.cli capture webmotors out/ # save raw HTML to build test fixtures
"""

import argparse
import json
import sys
from pathlib import Path

from app.adapters import ADAPTERS, SearchFilters
from app.core.browser import BrowserUnavailable
from app.core.config import get_settings
from app.core.http import BlockedError, FetchError, PoliteClient
from app.core.logging import setup_logging


def _filters(args) -> SearchFilters:
    return SearchFilters(brand=args.brand, model=args.model, max_price=args.max_price,
                         min_year=args.min_year, max_pages=1)


def probe(args) -> int:
    adapter = ADAPTERS[args.source](PoliteClient(cache_ttl=0))
    try:
        items = adapter.search(_filters(args))
        if items and adapter.supports_detail:
            d = adapter.fetch_detail(items[0].url)
            print("detalhe do 1º anúncio:", json.dumps({k: getattr(d, k) for k in (
                "color", "transmission", "seller_type", "km")} if d else None, ensure_ascii=False))
    except BlockedError as exc:
        print(f"BLOQUEADO: {exc}")
        return 2
    except FetchError as exc:
        print(f"ERRO DE REDE: {exc}")
        return 3
    except BrowserUnavailable as exc:
        print(f"NAVEGADOR INDISPONÍVEL: {exc}")
        return 4
    finally:
        adapter.close()
    print(f"{len(items)} anúncios após filtros")
    for it in items[:5]:
        print(json.dumps({k: getattr(it, k) for k in (
            "external_id", "title", "price", "year_model", "km", "transmission", "city",
            "seller_type")}, ensure_ascii=False))
    return 0 if items else 1


def capture(args) -> int:
    adapter = ADAPTERS[args.source](PoliteClient(cache_ttl=0))
    url = adapter.build_url(_filters(args), 1)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    try:
        html = adapter.fetch_search_html(_filters(args))
    except BlockedError as exc:
        print(f"BLOQUEADO: {exc}")
        return 2
    except FetchError as exc:
        print(f"ERRO DE REDE: {exc}")
        return 3
    finally:
        adapter.close()
    path = out / f"{args.source}_search.html"
    path.write_text(html, encoding="utf-8")
    print(f"salvo {path} ({len(html)} bytes) de {url}")
    return 0


def run(args) -> int:
    from app.services import runner

    if args.search:
        print(runner.run_search(args.search))
    else:
        runner.run_all()
    return 0


def main(argv=None) -> int:
    setup_logging(get_settings().log_level)
    p = argparse.ArgumentParser(prog="carwatch")
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("probe", "capture"):
        sp = sub.add_parser(name)
        sp.add_argument("source", choices=list(ADAPTERS))
        if name == "capture":
            sp.add_argument("out")
        sp.add_argument("--brand", default="Honda")
        sp.add_argument("--model", default="Fit")
        sp.add_argument("--max-price", type=int, default=50000)
        sp.add_argument("--min-year", type=int, default=2009)
    r = sub.add_parser("run")
    r.add_argument("--search", type=int)
    args = p.parse_args(argv)
    return {"probe": probe, "capture": capture, "run": run}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
