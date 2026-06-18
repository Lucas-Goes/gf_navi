"""
Re-exportação de compatibilidade — módulo original foi dividido em:

- tool_helpers.py:   singletons, preview helpers
- tool_queries.py:   funções de consulta ao banco
- tool_registry.py:  TOOL_DEFINITIONS, TOOL_LABELS, HELP_COMMANDS, _execute_tool

Importe diretamente do submódulo apropriado em código novo.
"""

from app.services.tool_helpers import (
    _get_sqlite, _get_vector, _get_llm, _get_ingestion, _get_search,
    _store_preview, _confirm_preview, _cancel_preview,
)
from app.services.tool_queries import (
    CORRECT_JSON_SCHEMA,
    _count_memories, _build_chain, _search_memories, _get_memory_detail,
    _list_periods, _list_fact_types, _add_memory, _infer_memory,
    _correct_memory, _list_memories, _search_documents, _sync_documents,
)
from app.services.tool_registry import (
    TOOL_LABELS, TOOL_DEFINITIONS,
    _format_tool_descriptions, HELP_COMMANDS, _format_user_help,
    _execute_tool, _is_empty_result,
)

__all__ = [
    "TOOL_LABELS", "TOOL_DEFINITIONS", "HELP_COMMANDS", "CORRECT_JSON_SCHEMA",
    "_get_sqlite", "_get_vector", "_get_llm", "_get_ingestion", "_get_search",
    "_store_preview", "_confirm_preview", "_cancel_preview",
    "_count_memories", "_build_chain", "_search_memories", "_get_memory_detail",
    "_list_periods", "_list_fact_types", "_add_memory", "_infer_memory",
    "_correct_memory", "_list_memories", "_search_documents", "_sync_documents",
    "_format_tool_descriptions", "_format_user_help",
    "_execute_tool", "_is_empty_result",
]
