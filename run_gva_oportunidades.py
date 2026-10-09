"""Punto de entrada CLI del colector GVA. Por defecto no persiste datos."""
import argparse
import json
import sys

from decodo_proxy import build_decodo_opener
from gva_oportunidades.collector import CollectionError, collect_opportunities, outcome_as_dict


def build_parser():
    parser = argparse.ArgumentParser(
        description="Descubre oportunidades de empleo público de la GVA desde fuentes oficiales."
    )
    parser.add_argument(
        "--limite",
        type=int,
        default=5,
        help="Máximo de detalles PDF a consultar. Valor predeterminado: 5; usa 0 para todos.",
    )
    parser.add_argument(
        "--persistir",
        action="store_true",
        help="Guardar observaciones en SQLite. Requiere --limite 0 y rastreo completo.",
    )
    parser.add_argument(
        "--base-datos",
        default="data/oportunidades_gva.sqlite3",
        help="Ruta de SQLite (predeterminada: data/oportunidades_gva.sqlite3).",
    )
    parser.add_argument("--tamanyo-pagina", type=int, default=100)
    parser.add_argument("--max-paginas", type=int, default=100)
    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.limite < 0:
        parser.error("--limite no puede ser negativo")
    if args.persistir and args.limite != 0:
        parser.error("--persistir requiere --limite 0 para no guardar una muestra parcial")

    try:
        result = collect_opportunities(
            build_decodo_opener(),
            limit=args.limite,
            persist=args.persistir,
            database_path=args.base_datos,
            page_size=args.tamanyo_pagina,
            max_pages=args.max_paginas,
        )
    except Exception as exc:
        print(json.dumps({
            "ok": False,
            "error_type": type(exc).__name__,
            "message": str(exc) if isinstance(exc, (ValueError, CollectionError)) else "Fallo de ejecución.",
        }, ensure_ascii=False))
        return 1

    payload = outcome_as_dict(result)
    payload["ok"] = result.source_complete and not result.detail_failures
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    if not result.source_complete:
        return 2
    if result.detail_failures:
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
