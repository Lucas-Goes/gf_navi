from __future__ import annotations

import unittest
from unittest.mock import patch

from unittest.mock import MagicMock, patch

from app.services.synthesis import (
    synthesize_answer_stream,
    _format_details, _format_chain,
)


def _fake_llm_stream(*args, **kwargs):
    yield "Resposta gerada pelo LLM para: Test (2026-06)"



class TestSynthesisFormatters(unittest.TestCase):
    def test_format_details_active(self):
        mem = {"id": "abc123", "title": "Test", "fact_type": "decision",
               "closing_period": "2026-06", "tags": ["tag1", "tag2"],
               "is_active": True, "registration_date": "2026-06-01T10:00:00"}
        result = _format_details(mem)
        self.assertIn("abc123", result)
        self.assertIn("✅ Ativa", result)
        self.assertIn("decision", result)

    def test_format_details_inactive(self):
        mem = {"id": "abc123", "title": "Test", "fact_type": "decision",
               "closing_period": "2026-06", "tags": [],
               "is_active": False, "registration_date": ""}
        result = _format_details(mem)
        self.assertIn("❌ Inativa", result)

    def test_format_details_no_tags(self):
        mem = {"id": "abc123", "title": "Test", "fact_type": "decision",
               "closing_period": "2026-06", "tags": [],
               "is_active": True, "registration_date": ""}
        result = _format_details(mem)
        self.assertNotIn("🏷️", result)

    def test_format_chain_empty(self):
        self.assertEqual(_format_chain([]), "")

    def test_format_chain_single(self):
        self.assertEqual(_format_chain([{"id": "abc", "registration_date": "2026-06",
                                          "is_active": True, "tags": [], "title": "Test"}]), "")

    def test_format_chain_multiple(self):
        chain = [
            {"id": "aaa", "registration_date": "2026-01", "is_active": False,
             "tags": ["tag1"], "title": "First version"},
            {"id": "bbb", "registration_date": "2026-06", "is_active": True,
             "tags": ["tag1"], "title": "Updated version"},
        ]
        result = _format_chain(chain)
        self.assertIn("v1", result)
        self.assertIn("v2", result)
        self.assertIn("First version", result)
        self.assertIn("Updated version", result)


class TestSynthesizeAnswerStream(unittest.TestCase):
    def test_help_handler(self):
        result = list(synthesize_answer_stream("help", "**Navi** help text", "help"))
        self.assertTrue(any("**Navi**" in chunk for chunk in result))

    def test_count_handler(self):
        result = list(synthesize_answer_stream("count", {"total": 15, "label": ""}, "count_memories"))
        self.assertTrue(any("15" in chunk for chunk in result))

    def test_list_empty_handler(self):
        result = list(synthesize_answer_stream("list", [], "list_memories"))
        self.assertTrue(any("não encontrei" in chunk.lower() for chunk in result))

    @patch("app.services.synthesis._call_llm_stream", side_effect=_fake_llm_stream)
    def test_list_nonempty_handler(self, mock_llm):
        result = list(synthesize_answer_stream("list", [{
            "id": "abc", "title": "Test", "fact_type": "decision",
            "closing_period": "2026-06", "tags": ["tag1"],
        }], "list_memories"))
        self.assertTrue(any("Resposta gerada" in chunk for chunk in result))

    def test_add_handler(self):
        result = list(synthesize_answer_stream("add", {
            "id": "abc123def456", "title": "New Mem", "fact_type": "decision",
            "closing_period": "2026-06", "tags": ["test"], "is_active": True,
            "registration_date": "",
        }, "add_memory"))
        self.assertTrue(any("New Mem" in chunk for chunk in result))
        self.assertTrue(any("adicionada" in chunk.lower() for chunk in result))

    def test_correct_handler(self):
        result = list(synthesize_answer_stream("correct", {
            "id": "abc123def456", "title": "Fixed Mem", "fact_type": "rule_change",
            "closing_period": "2026-06", "tags": ["test"], "is_active": True,
            "supersedes_id": "old123", "registration_date": "",
        }, "correct_memory"))
        self.assertTrue(any("Fixed Mem" in chunk for chunk in result))
        self.assertTrue(any("corrigida" in chunk.lower() for chunk in result))

    def test_periods_handler(self):
        result = list(synthesize_answer_stream("periods", [
            {"period": "2026-06", "count": 5},
        ], "list_periods"))
        self.assertTrue(any("2026-06" in chunk for chunk in result))

    def test_types_handler(self):
        result = list(synthesize_answer_stream("types", [
            {"type": "decision", "count": 10},
        ], "list_fact_types"))
        self.assertTrue(any("decision" in chunk for chunk in result))

    def test_preview_add_handler(self):
        result = list(synthesize_answer_stream("add", {
            "title": "Preview Add", "fact_type": "decision",
            "closing_period": "2026-06", "tags": ["test"], "description": "Desc",
        }, "add_memory_preview"))
        self.assertTrue(any("Preview Add" in chunk for chunk in result))

    def test_preview_correct_handler(self):
        result = list(synthesize_answer_stream("correct", {
            "title": "Preview Correct", "fact_type": "rule_change",
            "closing_period": "2026-06", "tags": ["test"], "description": "Desc",
            "supersedes_id": "old123", "supersedes_title": "Old Mem",
        }, "correct_memory_preview"))
        self.assertTrue(any("Preview Correct" in chunk for chunk in result))
        self.assertTrue(any("Old Mem" in chunk for chunk in result))

    def test_sync_handler(self):
        result = list(synthesize_answer_stream("sync", {"output": "ok"}, "sync_documents"))
        self.assertTrue(any("concluída" in chunk.lower() for chunk in result))
