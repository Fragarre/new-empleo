import re
import unittest

from extraer_oportunidades_gva import select_application_pair


PATTERN = re.compile(
    r"Apertura plazo\s+(\d{2}/\d{2}/\d{4})\s+Cierre plazo\s+(\d{2}/\d{2}/\d{4})",
    re.I,
)


class ApplicationWindowTests(unittest.TestCase):
    def test_valid_initial_window_opening_12_days_after_publication_is_selected(self):
        pairs = list(PATTERN.finditer(
            "Apertura plazo 20/07/2026 Cierre plazo 31/07/2026"
        ))
        selected = select_application_pair("08/07/2026", pairs)
        self.assertIsNotNone(selected)
        self.assertEqual(selected.group(1), "20/07/2026")
        self.assertEqual(selected.group(2), "31/07/2026")

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
