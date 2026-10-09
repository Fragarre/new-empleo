"""Pruebas con texto de ejemplo basado en el formato público de detalle GVA."""
import unittest
from gva_oportunidades.details import (
    DetailExtractionError,
    build_detail_page_url,
    build_detail_pdf_url,
    parse_detail_text,
)


SAMPLE = '''GENERALITAT VALENCIANA
Convocatoria 58/26. Pruebas selectivas de acceso al Cuerpo administrativo, C1-01. TURNO LIBRE
Organismo Conselleria de Economía, Hacienda y Administración Pública
Código GVA 110206 Código SIA 3260368
INFORMACIÓN BÁSICA
Convocatoria Oposición
Prueba Oposición
Grupo C1
Número de plazas totales 122
'''


class DetailParserTests(unittest.TestCase):
    def test_builds_official_pdf_url(self):
        url = build_detail_pdf_url("110206")
        self.assertIn("detall-ocupacio-publica", url)
        self.assertIn("codigo=110206", url)
        with self.assertRaises(ValueError):
            build_detail_pdf_url("x110206")

    def test_public_detail_url_is_separate_from_pdf_url(self):
        item = parse_detail_text("110206", SAMPLE)
        self.assertEqual(item.detail_url, "https://sede.gva.es/es/detall-ocupacio-publica?id_emp=110206")
        self.assertIn("_accion=pdf", item.pdf_url)
        self.assertEqual(build_detail_page_url("110206"), item.detail_url)

    def test_extracts_explicit_fields(self):
        item = parse_detail_text("110206", SAMPLE)
        self.assertEqual(item.official_code, "110206")
        self.assertIn("58/26", item.title)
        self.assertEqual(item.organism, "Conselleria de Economía, Hacienda y Administración Pública")
        self.assertEqual(item.call_type, "Oposición")
        self.assertEqual(item.test_type, "Oposición")
        self.assertEqual(item.group, "C1")
        self.assertEqual(item.total_places, 122)
        self.assertEqual(len(item.pdf_sha256), 64)

    def test_rejects_mismatched_official_code(self):
        with self.assertRaises(DetailExtractionError):
            parse_detail_text("999", SAMPLE)

    def test_does_not_invent_missing_fields(self):
        minimal = "GENERALITAT VALENCIANA\nConvocatoria 1/26. Ejemplo\nCódigo GVA 110206"
        item = parse_detail_text("110206", minimal)
        self.assertIsNone(item.organism)
        self.assertIsNone(item.total_places)


if __name__ == "__main__":
    unittest.main()
