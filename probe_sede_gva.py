"""Diagnóstico de solo lectura de la estructura del buscador oficial GVA."""
import sys
from decodo_proxy import build_decodo_opener
from gva_oportunidades.sources.sede_gva import SourceAccessError, SourceStructureError, build_search_url, discover_page


def main() -> int:
    print(f"GVA_SEARCH_URL={build_search_url()}")
    try:
        report = discover_page(build_decodo_opener())
    except SourceStructureError as exc:
        print("GVA_SOURCE_STRUCTURE=UNRECOGNIZED")
        print(f"GVA_DIAGNOSTIC={exc}")
        return 2
    except SourceAccessError as exc:
        print("GVA_SOURCE_ACCESS=FAILED")
        print(f"GVA_DIAGNOSTIC={exc}")
        return 1
    print(f"GVA_HTTP_STATUS={report.http_status}")
    print(f"GVA_HTML_BYTES={report.html_bytes}")
    print(f"GVA_DETAIL_LINKS={len(report.candidates)}")
    print(f"GVA_DISCOVERY_COMPLETE={report.complete}")
    for candidate in report.candidates[:10]:
        print(f"GVA_ITEM={candidate.official_code or 'SIN_CODIGO'} | {candidate.title} | {candidate.detail_url}")
    for warning in report.warnings:
        print(f"GVA_WARNING={warning}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
