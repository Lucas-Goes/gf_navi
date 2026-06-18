from __future__ import annotations

import json
import re
from pathlib import Path

_SIGLAS: dict[str, list[str]] | None = None


def _load() -> dict[str, list[str]]:
    global _SIGLAS
    if _SIGLAS is None:
        path = Path(__file__).parent / "siglas.json"
        _SIGLAS = json.loads(path.read_text())
    return _SIGLAS


def find(text: str) -> dict[str, list[str]]:
    """Retorna dict {sigla: [significados]} encontrados como palavras inteiras no texto."""
    siglas = _load()
    upper_words = set(re.sub(r"[^A-Z0-9]", " ", text.upper()).split())
    found = {}
    for sigla, significados in siglas.items():
        if sigla in upper_words:
            found[sigla] = significados
    return found


def expand_query(query: str) -> str:
    """Expande siglas na query com seus significados.

    Ex: 'cadastro EP' → 'cadastro EP Empresas PRO'
    """
    siglas = _load()
    upper_words = set(re.sub(r"[^A-Z0-9]", " ", query.upper()).split())
    extra = []
    for sigla in siglas:
        if sigla in upper_words:
            for significado in siglas[sigla]:
                extra.append(significado)
    if extra:
        return f"{query} {' '.join(extra)}"
    return query


def expand_tags(tags: list[str]) -> list[str]:
    """Expande tags que são siglas, adicionando os significados.

    Ex: ['ep', 'credito'] → ['ep', 'credito', 'empresas', 'pro']
    """
    siglas = _load()
    siglas_upper = {k.upper(): k for k in siglas}
    result = list(tags)
    for t in tags:
        key = t.upper()
        if key in siglas_upper:
            for significado in siglas[siglas_upper[key]]:
                for word in significado.lower().split():
                    w = word.strip(".,;:!?")
                    if w and w not in result:
                        result.append(w)
    return result[:10]


def expand_tags_grouped(tags: list[str]) -> list[list[str]]:
    """Retorna grupos de tags expandidas para AND entre grupos.

    Cada tag original vira um grupo com suas expansões.
    Ex: ['ep', 'credito'] → [['ep', 'empresas', 'pro'], ['credito']]
    """
    siglas = _load()
    siglas_upper = {k.upper(): k for k in siglas}
    groups = []
    for t in tags:
        group = [t]
        key = t.upper()
        if key in siglas_upper:
            for significado in siglas[siglas_upper[key]]:
                for word in significado.lower().split():
                    w = word.strip(".,;:!?")
                    if w and w not in group:
                        group.append(w)
        groups.append(group)
    return groups


def expand_terms(terms: list[str]) -> list[str]:
    """Dada uma lista de termos, adiciona significados de siglas encontradas (match exato)."""
    siglas = _load()
    result = list(terms)
    term_set = set(t.upper() for t in terms)
    for sigla in siglas:
        if sigla in term_set:
            for significado in siglas[sigla]:
                for word in significado.lower().split():
                    w = word.strip(".,;:!?")
                    if w and w not in result:
                        result.append(w)
    return result
