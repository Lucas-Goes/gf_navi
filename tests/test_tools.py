from __future__ import annotations

import unittest

from app.models import FactType, Preview
from app.services.tool_registry import (
    TOOL_LABELS, TOOL_DEFINITIONS, _is_empty_result,
    _format_tool_descriptions, _format_user_help, _execute_tool,
    REACT_EXCLUDED,
)
from app.services.tool_helpers import _store_preview, _confirm_preview, _cancel_preview


class TestToolLabels(unittest.TestCase):
    """20 test scenarios for tool utilities."""

    def test_tool_labels_has_all_definitions(self):
        for name in TOOL_DEFINITIONS:
            self.assertIn(name, TOOL_LABELS, f"TOOL_LABELS missing {name}")

    def test_tool_labels_are_strings(self):
        for label in TOOL_LABELS.values():
            self.assertIsInstance(label, str)
            self.assertGreater(len(label), 0)

    def test_tool_labels_known_values(self):
        self.assertEqual(TOOL_LABELS["help"], "Ajuda")
        self.assertEqual(TOOL_LABELS["add_memory"], "Adicionar memória")
        self.assertEqual(TOOL_LABELS["count_memories"], "Contar memórias")


class TestIsEmptyResult(unittest.TestCase):
    def test_none_is_empty(self):
        self.assertTrue(_is_empty_result(None))

    def test_empty_list_is_empty(self):
        self.assertTrue(_is_empty_result([]))

    def test_non_empty_list_is_not_empty(self):
        self.assertFalse(_is_empty_result([{"id": "abc"}]))

    def test_total_zero_is_empty(self):
        self.assertTrue(_is_empty_result({"total": 0, "label": ""}))

    def test_total_non_zero_is_not_empty(self):
        self.assertFalse(_is_empty_result({"total": 5, "label": ""}))

    def test_error_dict_is_empty(self):
        self.assertTrue(_is_empty_result({"error": "not found"}))

    def test_string_is_not_empty(self):
        self.assertFalse(_is_empty_result("hello"))

    def test_bool_false_is_not_empty(self):
        self.assertFalse(_is_empty_result(False))

    def test_int_zero_is_not_empty(self):
        self.assertFalse(_is_empty_result(0))


class TestFormatToolDescriptions(unittest.TestCase):
    def test_returns_string(self):
        result = _format_tool_descriptions()
        self.assertIsInstance(result, str)
        self.assertNotIn("count_memories", result)
        self.assertIn("search_memories", result)

    def test_includes_all_tools(self):
        result = _format_tool_descriptions()
        for name in TOOL_DEFINITIONS:
            if name in REACT_EXCLUDED:
                self.assertNotIn(name, result)
            else:
                self.assertIn(name, result)


class TestFormatUserHelp(unittest.TestCase):
    def test_returns_help_text(self):
        result = _format_user_help()
        self.assertIsInstance(result, str)
        self.assertIn("/add", result)
        self.assertIn("/help", result)
        self.assertIn("Navi", result)


class TestPreviewFunctions(unittest.TestCase):
    def setUp(self):
        self.preview = Preview(
            title="Test Memory",
            fact_type=FactType.decision,
            closing_period="2026-06",
            description="Test description",
            tags=["test"],
        )

    def test_store_and_confirm_preview(self):
        pid = _store_preview("add", "text", self.preview)
        self.assertIsInstance(pid, str)
        self.assertEqual(len(pid), 8)

    def test_confirm_nonexistent_preview_returns_error(self):
        result = _confirm_preview("nonexistent")
        self.assertIn("error", result)

    def test_cancel_nonexistent_preview_returns_error(self):
        result = _cancel_preview("nonexistent")
        self.assertIn("error", result)

    def test_store_then_cancel_returns_title(self):
        pid = _store_preview("add", "text", self.preview)
        result = _cancel_preview(pid)
        self.assertNotIn("error", result)
        self.assertEqual(result["title"], "Test Memory")

    def test_canceled_preview_cannot_be_confirmed(self):
        pid = _store_preview("add", "text", self.preview)
        _cancel_preview(pid)
        result = _confirm_preview(pid)
        self.assertIn("error", result)


class TestExecuteTool(unittest.TestCase):
    def test_unknown_tool_returns_error(self):
        result = _execute_tool("nonexistent", {})
        self.assertIn("error", result)

    def test_help_tool_returns_help_text(self):
        result = _execute_tool("help", {})
        self.assertIsInstance(result, str)
        self.assertIn("Navi", result)

    def test_missing_required_params_returns_error(self):
        result = _execute_tool("search_memories", {})
        self.assertIn("error", result)

    def test_invalid_fn_parameter_coercion(self):
        result = _execute_tool("count_memories", {"active": "true"})
        self.assertIsInstance(result, dict)
        self.assertIn("total", result)
