"""Pruebas de la CLI sin llamadas de red ni acceso a datos reales."""
import json
import unittest
from contextlib import redirect_stdout
from io import StringIO
from types import SimpleNamespace
from unittest.mock import patch

import run_gva_oportunidades as runner


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.opener = object()
        self.outcome = SimpleNamespace(
            source_complete=True,
            detail_failures=(),
            collection_complete=False,
        )
        self.output = StringIO()

    def run_main(self, argv):
        with redirect_stdout(self.output):
            code = runner.main(argv)
        return code, json.loads(self.output.getvalue())

    @patch("run_gva_oportunidades.outcome_as_dict")
    @patch("run_gva_oportunidades.collect_opportunities")
    @patch("run_gva_oportunidades.build_decodo_opener")
    def test_default_mode_is_read_only_and_limits_detail_fetches(
        self, build_opener, collect, serialize
    ):
        build_opener.return_value = self.opener
        collect.return_value = self.outcome
        serialize.return_value = {
            "source_complete": True,
            "selected_for_detail": 5,
            "persisted": False,
            "collection_complete": False,
            "detail_failures": [],
        }

        code, payload = self.run_main([])

        self.assertEqual(code, 0)
        self.assertTrue(payload["ok"])
        self.assertFalse(payload["persisted"])
        collect.assert_called_once_with(
            self.opener,
            limit=5,
            persist=False,
            database_path="data/oportunidades_gva.sqlite3",
            page_size=100,
            max_pages=100,
        )

    @patch("run_gva_oportunidades.build_decodo_opener")
    def test_persistence_with_sample_limit_is_rejected_before_network(self, build_opener):
        with redirect_stdout(self.output), self.assertRaises(SystemExit) as raised:
            runner.main(["--persistir"])
        self.assertEqual(raised.exception.code, 2)
        build_opener.assert_not_called()

    @patch("run_gva_oportunidades.outcome_as_dict")
    @patch("run_gva_oportunidades.collect_opportunities")
    @patch("run_gva_oportunidades.build_decodo_opener")
    def test_persistence_requires_explicit_unlimited_mode(
        self, build_opener, collect, serialize
    ):
        build_opener.return_value = self.opener
        collect.return_value = self.outcome
        serialize.return_value = {
            "source_complete": True,
            "selected_for_detail": 12,
            "persisted": True,
            "collection_complete": True,
            "detail_failures": [],
        }

        code, payload = self.run_main(["--limite", "0", "--persistir"])

        self.assertEqual(code, 0)
        self.assertTrue(payload["persisted"])
        collect.assert_called_once_with(
            self.opener,
            limit=0,
            persist=True,
            database_path="data/oportunidades_gva.sqlite3",
            page_size=100,
            max_pages=100,
        )

    @patch("run_gva_oportunidades.build_decodo_opener")
    def test_negative_limit_is_rejected_before_network(self, build_opener):
        with redirect_stdout(self.output), self.assertRaises(SystemExit) as raised:
            runner.main(["--limite", "-1"])
        self.assertEqual(raised.exception.code, 2)
        build_opener.assert_not_called()

    @patch("run_gva_oportunidades.collect_opportunities")
    @patch("run_gva_oportunidades.build_decodo_opener")
    def test_unexpected_exception_does_not_print_exception_details(
        self, build_opener, collect
    ):
        build_opener.return_value = self.opener
        collect.side_effect = RuntimeError("internal detail must not leak")

        code, payload = self.run_main([])

        self.assertEqual(code, 1)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error_type"], "RuntimeError")
        self.assertEqual(payload["message"], "Fallo de ejecución.")
        self.assertNotIn("internal detail", self.output.getvalue())


if __name__ == "__main__":
    unittest.main()
