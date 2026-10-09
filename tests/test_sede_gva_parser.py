"""Pruebas del parser y de la paginación usando HTML controlado."""
import unittest
from urllib.parse import parse_qs, urlparse

from gva_oportunidades.sources.sede_gva import (
    build_search_url,
    discover_pages,
    discover_active_pages,
    parse_search_html,
    parse_total_results,
)


class _Headers:
    def get_content_charset(self):
        return "utf-8"


class _Response:
    status = 200
    headers = _Headers()

    def __init__(self, url, body):
        self.url = url
        self.body = body

    def geturl(self):
        return self.url

    def getcode(self):
        return self.status

    def read(self):
        return self.body.encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class _FakeOpener:
    def __init__(self, pages):
        self.pages = pages
        self.urls = []

    def open(self, url, timeout=40):
        self.urls.append(url)
        query = parse_qs(urlparse(url).query)
        page = int(query["pagina"][0])
        return _Response(url, self.pages[page])


def _page(code, title, total=2):
    return (
        f"<html><body><p>{total} resultados</p>"
        f'<a href="/detall-ocupacio-publica?id_emp={code}&id_etapa=1">'
        f"<span>{title}</span></a></body></html>"
    )


class _StateAwareFakeOpener:
    def __init__(self, state_pages):
        self.state_pages = state_pages

    def open(self, url, timeout=40):
        query = parse_qs(urlparse(url).query)
        state = query["plazos"][0]
        page = int(query["pagina"][0])
        return _Response(url, self.state_pages[state][page])


class SedeGvaParserTests(unittest.TestCase):
    def test_url_includes_gva_scope_and_pagination(self):
        url = build_search_url(page=2, page_size=30)
        self.assertIn("tipoOrganismo=9", url)
        self.assertIn("pagina=2", url)
        self.assertIn("tamanyoPagina=30", url)
        self.assertIn("plazos=A", url)

    def test_can_query_without_deadline_filter(self):
        url = build_search_url(deadline_state=None)
        self.assertNotIn("plazos=", url)

    def test_rejects_invalid_pagination(self):
        with self.assertRaises(ValueError):
            build_search_url(page=0)
        with self.assertRaises(ValueError):
            build_search_url(page_size=101)
        with self.assertRaises(ValueError):
            build_search_url(deadline_state="X")

    def test_extracts_realistic_id_emp_detail_link_without_language_prefix(self):
        html = _page("110206", "Convocatoria administrativa GVA")
        links = parse_search_html(html)
        self.assertEqual(len(links), 1)
        self.assertEqual(links[0].title, "Convocatoria administrativa GVA")
        self.assertEqual(links[0].official_code, "110206")
        self.assertEqual(
            links[0].url,
            "https://sede.gva.es/es/detall-ocupacio-publica?id_emp=110206&id_etapa=1",
        )

    def test_ignores_external_links_and_non_detail_pages(self):
        html = '''<html><body>
          <a href="https://example.org/es/detall-ocupacio-publica?id_emp=999">Externo</a>
          <a href="/es/cercador-ocupacio-publica">Buscador</a>
          <a href="/es/detall-ocupacio-publica">Sin identificador</a>
        </body></html>'''
        self.assertEqual(parse_search_html(html), ())

    def test_keeps_existing_code_query_variant(self):
        html = '''<a href="/es/detall-ocupacio-publica?codigo=110406">
                   Convocatoria de prueba GVA</a>'''
        links = parse_search_html(html)
        self.assertEqual(len(links), 1)
        self.assertEqual(links[0].official_code, "110406")

    def test_parses_total_results_when_explicit(self):
        self.assertEqual(parse_total_results("<div>139 resultados</div>"), 139)
        self.assertEqual(
            parse_total_results(
                "<div>Resultados de la búsqueda: del 1 al 30 de un total de 1.234</div>"
            ),
            1234,
        )
        self.assertIsNone(parse_total_results("<div>Loading...</div>"))

    def test_deduplicates_same_url(self):
        html = '''<a href="/es/detall-ocupacio-publica?id_emp=123">Convocatoria</a>
          <a href="/es/detall-ocupacio-publica?id_emp=123">Duplicada</a>'''
        links = parse_search_html(html)
        self.assertEqual(len(links), 1)
        self.assertEqual(links[0].title, "Convocatoria")

    def test_discovers_all_declared_pages_and_marks_complete(self):
        opener = _FakeOpener({
            1: _page("101", "Oportunidad 101"),
            2: _page("102", "Oportunidad 102"),
        })
        report = discover_pages(opener, page_size=1, deadline_state="A")
        self.assertTrue(report.complete)
        self.assertEqual(report.total_records, 2)
        self.assertEqual(len(report.candidates), 2)
        self.assertEqual(len(opener.urls), 2)

    def test_discovers_open_and_pending_views_as_one_verified_set(self):
        opener = _StateAwareFakeOpener({
            "A": {1: _page("101", "Abierta", total=1)},
            "P": {1: _page("102", "Pendiente", total=1)},
        })
        report = discover_active_pages(opener)
        self.assertTrue(report.complete)
        self.assertEqual(report.deadline_state, "A,P")
        self.assertEqual(report.total_records, 2)
        self.assertEqual({item.official_code for item in report.candidates}, {"101", "102"})

    def test_combined_view_detects_overlap_between_status_filters(self):
        opener = _StateAwareFakeOpener({
            "A": {1: _page("101", "Mismo registro", total=1)},
            "P": {1: _page("101", "Mismo registro", total=1)},
        })
        report = discover_active_pages(opener)
        self.assertFalse(report.complete)
        self.assertEqual(len(report.candidates), 1)
        self.assertTrue(any("identificadores únicos" in item for item in report.warnings))

    def test_does_not_claim_complete_when_pages_duplicate_records(self):
        opener = _FakeOpener({
            1: _page("101", "Oportunidad 101"),
            2: _page("101", "Oportunidad 101"),
        })
        report = discover_pages(opener, page_size=1, deadline_state="A")
        self.assertFalse(report.complete)
        self.assertEqual(len(report.candidates), 1)
        self.assertTrue(any("enlaces oficiales únicos" in item for item in report.warnings))


if __name__ == "__main__":
    unittest.main()
