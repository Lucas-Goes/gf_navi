from __future__ import annotations

import unittest

from app.services.agent import (
    _summarize, _build_history_text, _check_repeated,
    REACT_SYSTEM_PROMPT,
)


class TestSummarize(unittest.TestCase):
    def test_empty_list(self):
        self.assertEqual(_summarize("search", []), "Nenhum resultado encontrado.")

    def test_list_with_items(self):
        result = _summarize("search", [
            {"title": "Mem 1", "id": "abc"},
            {"title": "Mem 2", "id": "def"},
        ])
        self.assertIn("Mem 1", result)
        self.assertIn("Mem 2", result)
        self.assertIn("2 resultado(s)", result)

    def test_list_with_more_than_3(self):
        result = _summarize("search", [
            {"title": "A"}, {"title": "B"}, {"title": "C"}, {"title": "D"},
        ])
        self.assertIn("4 resultado(s)", result)
        self.assertIn("(+1 outros)", result)

    def test_dict_with_total(self):
        result = _summarize("count", {"total": 10, "label": " (ativas)"})
        self.assertEqual(result, "Total: 10 (ativas)")

    def test_dict_with_error(self):
        result = _summarize("tool", {"error": "not found"})
        self.assertEqual(result, "Erro: not found")

    def test_dict_other(self):
        result = _summarize("tool", {"custom": "value"})
        self.assertIn("custom", result)

    def test_string(self):
        result = _summarize("tool", "hello world")
        self.assertEqual(result, "hello world")


class TestBuildHistoryText(unittest.TestCase):
    def test_empty_history(self):
        self.assertEqual(_build_history_text([]), "(nenhum passo executado ainda)")

    def test_single_step(self):
        text = _build_history_text([
            {"tool": "count_memories", "params": {}, "result": {"total": 5}},
        ])
        self.assertIn("count_memories", text)
        self.assertIn("Total: 5", text)

    def test_multiple_steps(self):
        text = _build_history_text([
            {"tool": "count_memories", "params": {}, "result": {"total": 5}},
            {"tool": "search_memories", "params": {"query": "test"}, "result": []},
        ])
        self.assertIn("Passo 1", text)
        self.assertIn("Passo 2", text)
        self.assertIn("Nenhum resultado", text)


class TestCheckRepeated(unittest.TestCase):
    def test_less_than_two_steps(self):
        self.assertFalse(_check_repeated([{"tool": "x", "params": {"a": 1}, "result": {}}]))

    def test_no_repetition(self):
        self.assertFalse(_check_repeated([
            {"tool": "x", "params": {"a": 1}, "result": {}},
            {"tool": "y", "params": {"b": 2}, "result": {}},
        ]))

    def test_repeated_tool_and_params(self):
        self.assertTrue(_check_repeated([
            {"tool": "x", "params": {"a": 1}, "result": {}},
            {"tool": "x", "params": {"a": 1}, "result": {}},
        ]))

    def test_same_tool_different_params_not_repeated(self):
        self.assertFalse(_check_repeated([
            {"tool": "x", "params": {"a": 1}, "result": {}},
            {"tool": "x", "params": {"a": 2}, "result": {}},
        ]))


class TestReactSystemPrompt(unittest.TestCase):
    def test_prompt_has_tool_descriptions_placeholder(self):
        self.assertIn("{tool_descriptions}", REACT_SYSTEM_PROMPT)

    def test_prompt_escaped_braces(self):
        self.assertIn("{{", REACT_SYSTEM_PROMPT)
        self.assertIn("}}", REACT_SYSTEM_PROMPT)

    def test_prompt_format_works(self):
        filled = REACT_SYSTEM_PROMPT.format(tool_descriptions="- count_memories")
        self.assertIn("count_memories", filled)
        self.assertNotIn("{tool_descriptions}", filled)
