"""Pruebas de persistencia SQLite: nueva fila, repetición y cambio trazable."""
import tempfile
import unittest
from pathlib import Path

from gva_oportunidades.db import connect_database, initialize_database
from gva_oportunidades.details import parse_detail_text
from gva_oportunidades.repository import finish_source_run, start_source_run, upsert_details


BASE_TEXT = '''GENERALITAT VALENCIANA
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
'''


class RepositoryTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test.sqlite3"
        initialize_database(self.db_path)
        self.connection = connect_database(self.db_path)

    def tearDown(self):
        self.connection.close()
        self.temp_dir.cleanup()

    def test_initial_insert_and_repeated_observation(self):
        run_id = start_source_run(self.connection, "sede_gva_empleo_publico")
        details = parse_detail_text("110206", BASE_TEXT)
        opportunity_id, created, changed = upsert_details(self.connection, details, source_run_id=run_id)
        self.assertTrue(created)
        self.assertEqual(changed, ())
        self.assertEqual(self.connection.execute("SELECT COUNT(*) FROM opportunities").fetchone()[0], 1)
        upsert_details(self.connection, details, source_run_id=run_id)
        self.assertEqual(self.connection.execute("SELECT COUNT(*) FROM opportunities").fetchone()[0], 1)
        self.assertEqual(self.connection.execute("SELECT COUNT(*) FROM opportunity_observations").fetchone()[0], 1)
        finish_source_run(self.connection, run_id, "success", 1, 1)

    def test_changed_title_is_logged(self):
        first_run = start_source_run(self.connection, "sede_gva_empleo_publico")
        first = parse_detail_text("110206", BASE_TEXT)
        first_id, created, _ = upsert_details(self.connection, first, source_run_id=first_run)
        self.assertTrue(created)
        finish_source_run(self.connection, first_run, "success", 1, 1)

        second_run = start_source_run(self.connection, "sede_gva_empleo_publico")
        changed_text = BASE_TEXT.replace("Convocatoria 58/26.", "Convocatoria corregida 58/26.")
        second = parse_detail_text("110206", changed_text)
        second_id, created, changed = upsert_details(self.connection, second, source_run_id=second_run)
        self.assertFalse(created)
        self.assertEqual(first_id, second_id)
        self.assertIn("title", changed)
        history = self.connection.execute("SELECT field_name, previous_value, current_value FROM opportunity_changes").fetchall()
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["field_name"], "title")

    def test_deadline_change_is_logged_and_unknown_does_not_clear_known_status(self):
        first_run = start_source_run(self.connection, "sede_gva_empleo_publico")
        first = parse_detail_text("110206", BASE_TEXT)
        upsert_details(self.connection, first, source_run_id=first_run)
        finish_source_run(self.connection, first_run, "success", 1, 1)

        second_run = start_source_run(self.connection, "sede_gva_empleo_publico")
        changed_text = BASE_TEXT.replace("Cierre plazo 30/10/2026", "Cierre plazo 31/10/2026")
        changed_text = changed_text.replace("Plazo abierto", "Plazo cerrado")
        second = parse_detail_text("110206", changed_text)
        _, created, changed = upsert_details(self.connection, second, source_run_id=second_run)
        self.assertFalse(created)
        self.assertIn("application_closes_on", changed)
        self.assertIn("application_status", changed)
        finish_source_run(self.connection, second_run, "success", 1, 1)

        history = self.connection.execute(
            "SELECT field_name, evidence_url FROM opportunity_changes ORDER BY id"
        ).fetchall()
        self.assertEqual(len(history), 2)
        self.assertTrue(all("_accion=pdf" in row["evidence_url"] for row in history))

        third_run = start_source_run(self.connection, "sede_gva_empleo_publico")
        without_dates = BASE_TEXT.split("Etapa actual. Bases y apertura de plazo")[0]
        third = parse_detail_text("110206", without_dates)
        upsert_details(self.connection, third, source_run_id=third_run)
        current = self.connection.execute(
            "SELECT application_status, application_closes_on FROM opportunities WHERE official_code='110206'"
        ).fetchone()
        self.assertEqual(current["application_status"], "closed")
        self.assertEqual(current["application_closes_on"], "2026-10-31")

    def test_failed_run_must_be_explicit(self):
        run_id = start_source_run(self.connection, "sede_gva_empleo_publico")
        finish_source_run(self.connection, run_id, "failed", error_type="SourceAccessError", error_summary="HTTP error")
        run = self.connection.execute("SELECT status, error_type FROM source_runs WHERE id=?", (run_id,)).fetchone()
        self.assertEqual(run["status"], "failed")
        self.assertEqual(run["error_type"], "SourceAccessError")


if __name__ == "__main__":
    unittest.main()
