"""Descubrimiento conservador desde el buscador oficial de empleo público GVA.

Solo consume páginas oficiales. No clasifica por heurística ni escribe en base de
datos. Los resultados se consideran completos únicamente si el total declarado
por la fuente coincide con los enlaces de detalle encontrados.
"""
from __future__ import annotations

from dataclasses import dataclass
from html.parser import HTMLParser
import math
import re
from urllib.parse import parse_qs, urlencode, urljoin, urlparse, urlunparse

SOURCE_ID = "sede_gva_empleo_publico"
SEARCH_URL = "https://sede.gva.es/es/cercador-ocupacio-publica"
DETAIL_PATH = "/es/detall-ocupacio-publica"
DETAIL_PATH_MARKERS = ("/detall-ocupacio-publica",)


class SourceAccessError(RuntimeError):
    """La fuente no respondió correctamente o no pudo consultarse."""


class SourceStructureError(RuntimeError):
    """No se reconoció la estructura; no equivale a cero resultados."""


@dataclass(frozen=True, slots=True)
class SearchLink:
    title: str
    url: str
    official_code: str | None


class _DetailLinkParser(HTMLParser):
    """Extrae enlaces oficiales de detalle sin depender de clases CSS."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[SearchLink] = []
        self._anchor: dict[str, str] | None = None
        self._text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() == "a":
            values = {key.lower(): (value or "") for key, value in attrs}
            self._anchor = {"href": values.get("href", "")}
            self._text = []

    def handle_data(self, data: str) -> None:
        if self._anchor is not None:
            value = " ".join(data.split())
            if value:
                self._text.append(value)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() != "a" or self._anchor is None:
            return
        raw_href = self._anchor.get("href", "").strip()
        title = " ".join(" ".join(self._text).split())
        self._anchor = None
        self._text = []
        if not raw_href or not title:
            return
        absolute = urljoin(SEARCH_URL, raw_href)
        parsed = urlparse(absolute)
        if parsed.hostname != "sede.gva.es":
            return
        path = parsed.path.casefold()
        if not any(marker in path for marker in DETAIL_PATH_MARKERS):
            return
        params = parse_qs(parsed.query)
        code_keys = {"id_emp", "codigo", "codigoempleo"}
        code = next(
            (values[0].strip() for key, values in params.items()
             if key.casefold() in code_keys and values and values[0].strip().isdigit()),
            None,
        )
        if not code:
            # A detail link without a stable official identifier cannot safely
            # be deduplicated or sent to the PDF endpoint.
            return
        canonical_path = DETAIL_PATH if path.endswith("/detall-ocupacio-publica") else parsed.path
        canonical = urlunparse(parsed._replace(path=canonical_path, fragment=""))
        self.links.append(SearchLink(title=title, url=canonical, official_code=code))


class _VisibleTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        value = " ".join(data.split())
        if value:
            self.parts.append(value)


def build_search_url(
    page: int = 1,
    page_size: int = 100,
    deadline_state: str | None = "A",
) -> str:
    """Construye una consulta del buscador GVA.

    tipoOrganismo=9 identifica Generalitat Valenciana en la búsqueda pública.
    deadline_state acepta A (abierto), P (pendiente), C (cerrado) o None
    para consultar sin filtrar por el plazo. La cobertura del rastreo se valida
    mediante el total explícito mostrado por la fuente.
    """
    if page < 1:
        raise ValueError("page debe ser >= 1")
    if not 1 <= page_size <= 100:
        raise ValueError("page_size debe estar entre 1 y 100")
    if deadline_state not in {"A", "P", "C", None}:
        raise ValueError("deadline_state debe ser A, P, C o None")
    params = {
        "tipoOrganismo": "9",
        "pagina": str(page),
        "tamanyoPagina": str(page_size),
    }
    if deadline_state is not None:
        params["plazos"] = deadline_state
    return f"{SEARCH_URL}?{urlencode(params)}"


def parse_total_results(html: str) -> int | None:
    """Extrae el total explícito mostrado por el buscador, si existe."""
    parser = _VisibleTextParser()
    parser.feed(html)
    parser.close()
    text = " ".join(parser.parts)
    patterns = (
        r"\b([\d.,]+)\s+resultados?\b",
        r"del\s+\d+\s+al\s+\d+\s+de\s+un\s+total\s+de\s+([\d.,]+)",
        r"resultados?\s*[:：]\s*([\d.,]+)",
    )
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            digits = re.sub(r"\D", "", match.group(1))
            if digits:
                return int(digits)
    return None


def parse_search_html(html: str) -> tuple[SearchLink, ...]:
    """Extrae y deduplica enlaces oficiales de detalle del HTML recibido."""
    parser = _DetailLinkParser()
    parser.feed(html)
    parser.close()
    unique: dict[str, SearchLink] = {}
    for link in parser.links:
        unique.setdefault(link.url, link)
    return tuple(unique.values())


def discover_page(
    opener,
    page: int = 1,
    page_size: int = 100,
    deadline_state: str | None = "A",
) -> SourceReport:
    """Consulta y analiza una sola página; no oculta errores ni HTML vacío.

    opener debe ser un urllib opener configurado con decodo_proxy.
    """
    from ..models import OpportunityCandidate, SourceReport

    requested_url = build_search_url(page, page_size, deadline_state)
    try:
        with opener.open(requested_url, timeout=40) as response:
            status = getattr(response, "status", response.getcode())
            final_url = response.geturl()
            charset = response.headers.get_content_charset() or "utf-8"
            payload = response.read()
    except Exception as exc:
        raise SourceAccessError(
            f"Falló la consulta de Sede GVA ({type(exc).__name__})"
        ) from None
    if not 200 <= status < 300:
        raise SourceAccessError(f"Sede GVA respondió HTTP {status}")
    html = payload.decode(charset, errors="replace")
    links = parse_search_html(html)
    total_records = parse_total_results(html)
    if not links and total_records != 0:
        raise SourceStructureError(
            "No se detectaron enlaces de detalle; posible renderizado JavaScript "
            "o cambio de estructura."
        )
    candidates = tuple(
        OpportunityCandidate(
            source=SOURCE_ID,
            title=link.title,
            detail_url=link.url,
            official_code=link.official_code,
            raw_text=link.title,
            metadata={"deadline_state": deadline_state, "page": page},
        )
        for link in links
    )
    warnings = ()
    if total_records is None:
        warnings = (
            "La fuente no declaró un total reconocible; no puede certificarse "
            "la cobertura completa.",
        )
    return SourceReport(
        source=SOURCE_ID,
        requested_url=requested_url,
        final_url=final_url,
        http_status=status,
        html_bytes=len(payload),
        candidates=candidates,
        complete=False,
        warnings=warnings,
        total_records=total_records,
        page=page,
        page_size=page_size,
        deadline_state=deadline_state or "all",
    )


def discover_pages(
    opener,
    page_size: int = 100,
    deadline_state: str | None = "A",
    max_pages: int = 100,
) -> SourceReport:
    """Recorre páginas y verifica los recuentos declarados por la fuente.

    El resultado complete=True solo se devuelve si el total declarado se pudo
    leer, todas las páginas esperadas se consultaron y el número de registros
    únicos coincide con dicho total.
    """
    from ..models import SourceReport

    if max_pages < 1:
        raise ValueError("max_pages debe ser >= 1")
    first = discover_page(opener, page=1, page_size=page_size, deadline_state=deadline_state)
    warnings = list(first.warnings)
    expected = first.total_records
    if expected is None:
        return first
    page_count = max(1, math.ceil(expected / page_size))
    if page_count > max_pages:
        warnings.append(
            f"Se requieren {page_count} páginas y max_pages={max_pages}; "
            "el rastreo queda incompleto."
        )
    pages_to_fetch = min(page_count, max_pages)
    unique: dict[str, OpportunityCandidate] = {
        item.detail_url: item for item in first.candidates
    }
    totals_stable = True
    for page_number in range(2, pages_to_fetch + 1):
        report = discover_page(
            opener, page=page_number, page_size=page_size,
            deadline_state=deadline_state,
        )
        if report.total_records != expected:
            totals_stable = False
            warnings.append(
                f"El total cambió entre páginas: página 1={expected}, "
                f"página {page_number}={report.total_records}."
            )
        for item in report.candidates:
            unique.setdefault(item.detail_url, item)
        warnings.extend(report.warnings)

    count_matches = len(unique) == expected
    if not count_matches:
        warnings.append(
            f"La fuente declara {expected} resultados, pero se hallaron "
            f"{len(unique)} enlaces oficiales únicos."
        )
    complete = (
        totals_stable and count_matches and page_count <= max_pages
        and not warnings
    )
    return SourceReport(
        source=first.source,
        requested_url=first.requested_url,
        final_url=first.final_url,
        http_status=first.http_status,
        html_bytes=first.html_bytes,
        candidates=tuple(unique.values()),
        complete=complete,
        warnings=tuple(dict.fromkeys(warnings)),
        total_records=expected,
        page=1,
        page_size=page_size,
        deadline_state=deadline_state or "all",
    )


def discover_active_pages(opener, page_size: int = 100, max_pages: int = 100) -> SourceReport:
    """Descubre oportunidades con plazo abierto o pendiente, sin incluir cerradas.

    Las dos vistas se recorren por separado y se deduplican por URL oficial.
    Si alguna vista no puede certificarse, el resultado global queda incompleto.
    """
    from ..models import OpportunityCandidate, SourceReport

    reports = [
        discover_pages(opener, page_size=page_size, deadline_state=state, max_pages=max_pages)
        for state in ("A", "P")
    ]
    candidates: dict[str, OpportunityCandidate] = {}
    for report in reports:
        for item in report.candidates:
            candidates.setdefault(item.detail_url, item)
    warning_list = [
        warning
        for report in reports
        for warning in report.warnings
    ]
    expected_total = sum(report.total_records or 0 for report in reports)
    if len(candidates) != expected_total:
        warning_list.append(
            f"Las vistas de plazo suman {expected_total} registros, "
            f"pero producen {len(candidates)} identificadores únicos."
        )
    warnings = tuple(dict.fromkeys(warning_list))
    complete = all(report.complete for report in reports) and len(candidates) == expected_total
    return SourceReport(
        source=SOURCE_ID,
        requested_url=" | ".join(report.requested_url for report in reports),
        final_url=" | ".join(report.final_url for report in reports),
        http_status=200 if all(200 <= report.http_status < 300 for report in reports) else 0,
        html_bytes=sum(report.html_bytes for report in reports),
        candidates=tuple(candidates.values()),
        complete=complete,
        warnings=warnings,
        total_records=expected_total,
        page=1,
        page_size=page_size,
        deadline_state="A,P",
    )
