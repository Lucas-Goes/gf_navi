from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from app.services.router import parse_slash


class TestParseSlash(unittest.TestCase):
    """Slash command parsing."""

    def test_slash_add_parsed(self):
        result = parse_slash("/add nova regra de crédito")
        self.assertIsNotNone(result)
        tool, params = result
        self.assertEqual(tool, "add_memory")
        self.assertIn("text", params)
        self.assertIn("nova regra", params["text"])

    def test_slash_add_with_flags(self):
        result = parse_slash('/add nova regra --type rule_change --period 2026-06 --title "Regra"')
        self.assertIsNotNone(result)
        tool, params = result
        self.assertEqual(tool, "add_memory")
        self.assertEqual(params.get("fact_type"), "rule_change")
        self.assertEqual(params.get("closing_period"), "2026-06")

    def test_slash_search(self):
        result = parse_slash("/search compliance")
        self.assertIsNotNone(result)
        tool, params = result
        self.assertEqual(tool, "search_memories")
        self.assertEqual(params.get("query"), "compliance")

    def test_slash_search_with_flags(self):
        result = parse_slash("/search compliance --type decision")
        self.assertIsNotNone(result)
        tool, params = result
        self.assertEqual(tool, "search_memories")
        self.assertEqual(params.get("query"), "compliance")
        self.assertEqual(params.get("fact_type"), "decision")

    def test_slash_list(self):
        result = parse_slash("/list")
        self.assertIsNotNone(result)
        tool, params = result
        self.assertEqual(tool, "list_memories")

    def test_slash_list_with_filters(self):
        result = parse_slash("/list --type decision --period 2026-06 --tags tag1,tag2 --limit 10")
        self.assertIsNotNone(result)
        tool, params = result
        self.assertEqual(tool, "list_memories")
        self.assertEqual(params.get("fact_type"), "decision")
        self.assertEqual(params.get("closing_period"), "2026-06")
        self.assertEqual(params.get("tags"), "tag1,tag2")

    def test_slash_get(self):
        result = parse_slash("/get abc12345")
        self.assertIsNotNone(result)
        tool, params = result
        self.assertEqual(tool, "get_memory_detail")
        self.assertEqual(params.get("id"), "abc12345")

    def test_slash_count(self):
        result = parse_slash("/count")
        self.assertIsNotNone(result)
        tool, params = result
        self.assertEqual(tool, "count_memories")

    def test_slash_count_with_filter(self):
        result = parse_slash("/count --type decision")
        self.assertIsNotNone(result)
        tool, params = result
        self.assertEqual(tool, "count_memories")
        self.assertEqual(params.get("fact_type"), "decision")

    def test_slash_help(self):
        result = parse_slash("/help")
        self.assertIsNotNone(result)
        tool, params = result
        self.assertEqual(tool, "help")

    def test_slash_sync_docs(self):
        result = parse_slash("/sync-docs")
        self.assertIsNotNone(result)
        tool, params = result
        self.assertEqual(tool, "sync_documents")

    def test_slash_confirm(self):
        result = parse_slash("/confirm abc12345")
        self.assertIsNotNone(result)
        tool, params = result
        self.assertEqual(tool, "confirm_preview")
        self.assertEqual(params.get("preview_id"), "abc12345")

    def test_slash_cancel(self):
        result = parse_slash("/cancel abc12345")
        self.assertIsNotNone(result)
        tool, params = result
        self.assertEqual(tool, "cancel_preview")
        self.assertEqual(params.get("preview_id"), "abc12345")

    def test_not_a_slash(self):
        result = parse_slash("conte as memórias")
        self.assertIsNone(result)

    def test_only_slash(self):
        result = parse_slash("/")
        self.assertIsNone(result)

    def test_unknown_slash(self):
        result = parse_slash("/unknown")
        self.assertIsNone(result)


class TestClassifyWithLLM(unittest.TestCase):
    def test_requires_llm_provider(self):
        from app.services.router import classify_with_llm
        result = classify_with_llm(None, "hello")
        self.assertIsNone(result)
