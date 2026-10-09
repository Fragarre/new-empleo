"""Pruebas de la orquestación con fuente y detalles simulados."""
import tempfile
import unittest
from pathlib import Path

from gva_oportunidades.collector import collect_opportunities
from gva_oportunidades.db import connect_database
from gva_oportunidades.details import parse_detail_text
from gva_oportunidades.models import OpportunityCandidate, SourceReport


SAMPLE = """GENERALITAT VALENCIANA
Convocatoria 58/26. Pruebas selectivas de acceso al Cuerpo administrativo, C1-01. TURNO LIBRE
Organismo Conselleria de Economía, Hacienda y Administración Pública
Código GVA 110206 Código SIA 3260368
INFORMACIÓN BÁSICA
Convocatoria Oposición
Prueba Oposición
Grupo C1
Número de plazas totales 122
Etapa actual. Bases y apertura de plazo
Apertura plazo 01/10/2026
Cierre plazo 30/10/2026
Plazo abierto
"""


def _candidate(code, title):
    return OpportunityCandidate(
        source="sede_gva_empleo_publico",
        title=title,
        detail_url=f"https://sede.gva.es/es/detall-ocupacio-publica?id_emp={code}",
        official_code=code,
        raw_text=title,
    )


def _report(candidates, complete=True):
    return SourceReport(
        source="sede_gva_empleo_publico",
        requested_url="https://sede.gva.es/es/cercador-ocupacio-publica",
        final_url="https://sede.gva.es/es/cercador-ocupacio-publica",
        http_status=200,
        html_bytes=2000,
        candidates=tuple(candidates),
        complete=complete,
        warnings=() if complete else ("total no verificable",),
        total_records=len(candidates) if complete else None,
        page=1,
        page_size=100,
        deadline_state="A,P",
    )


class CollectorTests(unittest.TestCase):
    def test_default_mode_is_read_only_and_obeys_detail_limit(self):
        candidates = [_candidate("110206", "Primera"), _candidate("110207", "Segunda")]
        calls = []

        def fake_details(opener, code):
            calls.append(code)
            return parse_detail_text(code, SAMPLE.replace("110206", code))

        result = collect_opportunities(
            object(),
            limit=1,
            persist=False,
            discovery_function=lambda opener, **kwargs: _report(candidates),
            detail_fetcher=fake_details,
        )
        self.assertFalse(result.persisted)
        self.assertEqual(result.discovered_links, 2)
        self.assertEqual(result.selected_for_detail, 1)
        self.assertEqual(result.details_parsed, 1)
        self.assertFalse(result.collection_complete)
        self.assertEqual(calls, ["110206"])

    def test_incomplete_discovery_prevents_detail_fetch(self):
        calls = []
        result = collect_opportunities(
            object(),
            limit=5,
            persist=False,
            discovery_function=lambda opener, **kwargs: _report(
                [_candidate("110206", "Primera")], complete=False
            ),
            detail_fetcher=lambda opener, code: calls.append(code),
        )
        self.assertFalse(result.source_complete)
        self.assertEqual(result.selected_for_detail, 0)
        self.assertEqual(result.records_saved, 0)
        self.assertEqual(calls, [])

    def test_persistence_requires_unlimited_detail_mode(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                collect_opportunities(
                    object(),
                    limit=1,
                    persist=True,
                    database_path=Path(directory) / "test.sqlite3",
                    discovery_function=lambda opener, **kwargs: _report([]),
                    detail_fetcher=lambda opener, code: None,
                )

    def test_complete_run_persists_to_explicit_temporary_database(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "test.sqlite3"
            candidate = _candidate("110206", "Primera")

            def fake_details(opener, code):
                return parse_detail_text(code, SAMPLE)

            result = collect_opportunities(
                object(),
                limit=0,
                persist=True,
                database_path=database,
                discovery_function=lambda opener, **kwargs: _report([candidate]),
                detail_fetcher=fake_details,
            )
            self.assertTrue(result.persisted)
            self.assertTrue(result.collection_complete)
            self.assertEqual(result.records_saved, 1)
            with connect_database(database) as connection:
                opportunity = connection.execute(
                    "SELECT official_code, application_status, application_closes_on "
                    "FROM opportunities"
                ).fetchone()
                run = connection.execute(
                    "SELECT status, records_discovered, records_processed FROM source_runs"
                ).fetchone()
                self.assertEqual(opportunity["official_code"], "110206")
                self.assertEqual(opportunity["application_status"], "open")
                self.assertEqual(opportunity["application_closes_on"], "2026-10-30")
                self.assertEqual(run["status"], "success")
                self.assertEqual(run["records_discovered"], 1)
                self.assertEqual(run["records_processed"], 1)

    def test_incomplete_persisted_discovery_is_failed_without_opportunities(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "test.sqlite3"
            result = collect_opportunities(
                object(),
                limit=0,
                persist=True,
                database_path=database,
                discovery_function=lambda opener, **kwargs: _report(
                    [_candidate("110206", "Primera")], complete=False
                ),
                detail_fetcher=lambda opener, code: self.fail("No debe descargarse ningún detalle"),
            )
            self.assertFalse(result.source_complete)
            self.assertEqual(result.records_saved, 0)
            with connect_database(database) as connection:
                self.assertEqual(
                    connection.execute("SELECT COUNT(*) FROM opportunities").fetchone()[0],
                    0,
                )
                self.assertEqual(
                    connection.execute("SELECT status FROM source_runs").fetchone()[0],
                    "failed",
                )


if __name__ == "__main__":
    unittest.main()
