import re
import unittest

from extraer_oportunidades_gva import classify_scope, is_service_unavailable_page, select_application_pair


PATTERN = re.compile(
    r"Apertura plazo\s+(\d{2}/\d{2}/\d{4})\s+Cierre plazo\s+(\d{2}/\d{2}/\d{4})",
    re.I,
)


class ApplicationWindowTests(unittest.TestCase):
    def test_gva_service_outage_page_is_detected(self):
        html = "<html><head><title>Aplicación fuera de servicio</title></head><body>Error</body></html>"
        self.assertTrue(is_service_unavailable_page(html, "Aplicación fuera de servicio"))

    def test_normal_listing_is_not_marked_as_service_outage(self):
        html = "<html><head><title>Buscador de empleo público</title></head><body>Resultados</body></html>"
        self.assertFalse(is_service_unavailable_page(html, "Buscador de empleo público"))

    def test_individual_detail_outage_raises_instead_of_becoming_a_record(self):
        from unittest.mock import patch
        from extraer_oportunidades_gva import parse_detail

        html = (
            "<html><head><title>Aplicación fuera de servicio</title></head>"
            "<body><h1>Aplicación fuera de servicio</h1></body></html>"
        )
        item = {
            "id_emp": "110071",
            "url": "https://sede.gva.es/es/detall-ocupacio-publica?id_emp=110071",
            "link_text": "",
        }
        with patch("extraer_oportunidades_gva.fetch", return_value=(html, item["url"])):
            with self.assertRaisesRegex(RuntimeError, "ficha individual 110071.*servicio no disponible"):
                parse_detail(item)

    def test_valid_initial_window_opening_12_days_after_publication_is_selected(self):
        pairs = list(PATTERN.finditer(
            "Apertura plazo 20/07/2026 Cierre plazo 31/07/2026"
        ))
        selected = select_application_pair("08/07/2026", pairs)
        self.assertIsNotNone(selected)
        self.assertEqual(selected.group(1), "20/07/2026")
        self.assertEqual(selected.group(2), "31/07/2026")

    def test_base_window_beats_a_misleading_footer_window(self):
        text = (
            "Apertura plazo 20/07/2026 Cierre plazo 31/07/2026 "
            "Fase Convocatoria Publicación DOGV de 08/07/2026 "
            + ("texto auxiliar " * 30)
            + "FAQ Apertura plazo 09/07/2026 Cierre plazo 22/07/2026"
        )
        pairs = list(PATTERN.finditer(text))
        preferred = list(PATTERN.finditer(text[:250]))
        selected = select_application_pair("08/07/2026", pairs, preferred)
        self.assertIsNotNone(selected)
        self.assertEqual(selected.group(1), "20/07/2026")
        self.assertEqual(selected.group(2), "31/07/2026")


    def test_published_list_of_approved_candidates_is_terminal(self):
        from extraer_oportunidades_gva import is_terminal_stage
        self.assertTrue(is_terminal_stage("Lista de aprobados"))

    def test_annulled_intermediate_act_does_not_end_the_whole_call(self):
        from extraer_oportunidades_gva import is_terminal_stage
        self.assertFalse(is_terminal_stage("Anulación acto elección destino"))

    def test_tribunal_appointment_is_not_terminal(self):
        from extraer_oportunidades_gva import is_terminal_stage
        self.assertFalse(is_terminal_stage("Nombramiento del tribunal"))

    def test_window_more_than_30_days_after_publication_is_not_guessed(self):
        pairs = list(PATTERN.finditer(
            "Apertura plazo 25/08/2026 Cierre plazo 14/09/2026"
        ))
        self.assertIsNone(select_application_pair("20/04/2026", pairs))

    def test_closing_date_before_opening_date_is_rejected(self):
        pairs = list(PATTERN.finditer(
            "Apertura plazo 31/07/2026 Cierre plazo 20/07/2026"
        ))
        self.assertIsNone(select_application_pair("08/07/2026", pairs))


class ScopeClassificationTests(unittest.TestCase):
    def test_general_administration_turno_libre_oposicion_is_in_scope(self):
        included, reason = classify_scope(
            "Convocatoria de acceso al cuerpo Superior de Administración A1-01. TURNO LIBRE",
            "Pruebas selectivas de acceso por oposición."
        )
        self.assertTrue(included)
        self.assertEqual(reason, "alcance_confirmado")

    def test_european_funds_specialty_is_excluded_even_with_target_group(self):
        included, reason = classify_scope(
            "Convocatoria APT-A1-01-02 de Fondos Europeos. TURNO LIBRE",
            "Pruebas selectivas de acceso A1-01 por oposición."
        )
        self.assertFalse(included)
        self.assertIn("exclusion_explicita", reason)

    def test_internal_promotion_is_excluded(self):
        included, reason = classify_scope(
            "Convocatoria de promoción interna del cuerpo administrativo C1-01",
            "Proceso selectivo."
        )
        self.assertFalse(included)
        self.assertIn("exclusion_explicita", reason)

    def test_missing_turno_libre_evidence_is_excluded(self):
        included, reason = classify_scope(
            "Convocatoria cuerpo administrativo C1-01",
            "Pruebas selectivas por oposición."
        )
        self.assertFalse(included)
        self.assertEqual(reason, "sin_evidencia_turno_libre")

    def test_non_target_group_is_excluded(self):
        included, reason = classify_scope(
            "Convocatoria cuerpo de bomberos B1",
            "Turno libre por oposición."
        )
        self.assertFalse(included)
        self.assertIn("exclusion_explicita", reason)

    def test_accented_exclusion_terms_are_normalized(self):
        included, reason = classify_scope(
            "Convocatoria de Administración A1-01 de Fondos Europeos, turno libre",
            "Proceso selectivo."
        )
        self.assertFalse(included)
        self.assertIn("fondos europeos", reason)


    def test_contextual_mention_of_excluded_area_does_not_exclude_general_admin(self):
        included, reason = classify_scope(
            "Convocatoria cuerpo administrativo C1-01, turno libre",
            "Proceso selectivo por oposición. La ficha enlaza información general sobre Fondos Europeos."
        )
        self.assertTrue(included)
        self.assertEqual(reason, "alcance_confirmado")

    def test_legal_sciences_specialty_is_accepted(self):
        included, reason = classify_scope(
            "Convocatoria A1-01. Especialidad ciencias jurídicas. TURNO LIBRE",
            "Proceso selectivo por oposición."
        )
        self.assertTrue(included)
        self.assertEqual(reason, "alcance_confirmado")

    def test_concurso_oposicion_is_excluded_as_a_different_selection_system(self):
        included, reason = classify_scope(
            "Convocatoria cuerpo administrativo C1-01, turno libre, concurso-oposición",
            "Proceso selectivo."
        )
        self.assertFalse(included)
        self.assertIn("exclusion_explicita", reason)


if __name__ == "__main__":
    unittest.main()
