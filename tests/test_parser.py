from __future__ import annotations

import json
import unittest
from unittest.mock import MagicMock, patch

from app.services.parser import ParserService


class TestParserService(unittest.TestCase):
    def setUp(self):
        self.mock_llm = MagicMock()
        self.parser = ParserService(self.mock_llm)
        self.valid_json = json.dumps({
            "title": "Nova regra de cadastro",
            "fact_type": "rule_change",
            "closing_period": "2026-06",
            "description": "Regra aprovada para cadastro de agencias.",
            "decided_by": "Diretoria",
            "requested_by": "Ana",
            "approved_by": "Carlos",
            "metadata": None,
            "is_correction": False,
            "confidence_score": 0.95,
        })

    def test_parse_success(self):
        self.mock_llm.invoke.return_value = self.valid_json
        preview = self.parser.parse("Nova regra de cadastro")
        self.assertIsNotNone(preview)
        self.assertEqual(preview.title, "Nova regra de cadastro")
        self.assertEqual(preview.fact_type.value, "rule_change")
        self.assertEqual(preview.closing_period, "2026-06")
        self.assertEqual(preview.confidence_score, 0.95)

    def test_parse_with_code_block(self):
        response = f"```json\n{self.valid_json}\n```"
        self.mock_llm.invoke.return_value = response
        preview = self.parser.parse("texto")
        self.assertIsNotNone(preview)
        self.assertEqual(preview.title, "Nova regra de cadastro")

    def test_parse_null_confidence(self):
        data = json.loads(self.valid_json)
        data["confidence_score"] = None
        self.mock_llm.invoke.return_value = json.dumps(data)
        preview = self.parser.parse("teste")
        self.assertEqual(preview.confidence_score, 1.0)

    def test_parse_string_null_confidence(self):
        data = json.loads(self.valid_json)
        data["confidence_score"] = "null"
        self.mock_llm.invoke.return_value = json.dumps(data)
        preview = self.parser.parse("teste")
        self.assertEqual(preview.confidence_score, 1.0)

    def test_parse_empty_confidence(self):
        data = json.loads(self.valid_json)
        data["confidence_score"] = ""
        self.mock_llm.invoke.return_value = json.dumps(data)
        preview = self.parser.parse("teste")
        self.assertEqual(preview.confidence_score, 1.0)

    def test_parse_missing_description_falls_back(self):
        data = json.loads(self.valid_json)
        del data["description"]
        self.mock_llm.invoke.return_value = json.dumps(data)
        preview = self.parser.parse("Texto original do usuario")
        self.assertEqual(preview.description, "Texto original do usuario")

    def test_invalid_fact_type_falls_to_other(self):
        data = json.loads(self.valid_json)
        data["fact_type"] = "invalid_type"
        self.mock_llm.invoke.return_value = json.dumps(data)
        preview = self.parser.parse("teste")
        self.assertEqual(preview.fact_type.value, "other")

    def test_llm_error_raises(self):
        self.mock_llm.invoke.side_effect = Exception("API error")
        with self.assertRaises(RuntimeError):
            self.parser.parse("teste")

    def test_normalize_yyyy_mm(self):
        result = self.parser._normalize_period("2026-06")
        self.assertEqual(result, "2026-06")

    def test_normalize_invalid_month_returns_raw(self):
        result = self.parser._normalize_period("2026-13")
        self.assertEqual(result, "2026-13")

    def test_normalize_yyyymm(self):
        result = self.parser._normalize_period("202606")
        self.assertEqual(result, "2026-06")

    def test_normalize_dd_mm_yyyy(self):
        result = self.parser._normalize_period("15/06/2026")
        self.assertEqual(result, "2026-06")

    def test_normalize_mm_yyyy(self):
        result = self.parser._normalize_period("06/2026")
        self.assertEqual(result, "2026-06")

    def test_normalize_with_month_name(self):
        result = self.parser._normalize_period("junho de 2026")
        self.assertEqual(result, "2026-06")

    def test_normalize_full_date_iso(self):
        result = self.parser._normalize_period("2026-06-15")
        self.assertEqual(result, "2026-06")

    def test_normalize_empty_returns_empty(self):
        self.assertEqual(self.parser._normalize_period(""), "")

    def test_normalize_none_returns_empty(self):
        self.assertEqual(self.parser._normalize_period(None), "")

    def test_parse_json_with_nested_braces(self):
        nested = json.dumps({
            "title": "Test",
            "fact_type": "decision",
            "closing_period": "2026-06",
            "description": "Descricao com {chaves} aninhadas",
            "confidence_score": 0.9,
        })
        self.mock_llm.invoke.return_value = nested
        preview = self.parser.parse("teste")
        self.assertEqual(preview.description, "Descricao com {chaves} aninhadas")

    def test_parse_title_truncated(self):
        data = json.loads(self.valid_json)
        data["title"] = "A" * 200
        self.mock_llm.invoke.return_value = json.dumps(data)
        preview = self.parser.parse("teste")
        self.assertEqual(len(preview.title), 100)
