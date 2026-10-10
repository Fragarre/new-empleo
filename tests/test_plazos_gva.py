import re
import unittest

from extraer_oportunidades_gva import is_service_unavailable_page, select_application_pair


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


if __name__ == "__main__":
    unittest.main()
