# Plano: Eliminar Redundâncias entre CLI, Tools e Frontend

## Objetivo
CLI e Frontend/API devem compartilhar a mesma lógica de negócio. As funções em `tools.py` são a fonte única da verdade. O CLI vira um wrapper thin que adiciona apenas UI (preview/confirmação).

---

## Fase 1 — Unificar lógica core em `tools.py`

### 1.1 Criar `app/services/utils.py`
- `remove_accents(text)` — versão canônica (NFKD + combining)
- `normalize(text)` — NFKD + ascii ignore + lower (para busca/hybrid)
- `clean_tags(tags_str: str) -> list[str]` — split, strip, lower, max 20 chars
- `clean_tag_list(raw_tags: list) -> list[str]` — limpa lista, max 5
- `extract_keywords(text) -> str` — extrai keywords relevantes
- `STOPWORDS` — frozenset compartilhado

### 1.2 Mover `CORRECT_PROMPT` para `tools.py`
- Copiar `CORRECT_PROMPT` (cli.py:100-122) para `tools.py`
- Refatorar `_correct_memory` para usar o merge via LLM (como o CLI faz)
- `_correct_memory` passa a receber `(text, id=None)` e faz merge do conteúdo original + novo

### 1.3 Unificar `_infer_memory`
- Manter a versão de `tools.py` (mais completa, com `top_k=5`)
- Remover `_infer_memory` de `cli.py`
- CLI chama `tools._infer_memory`

### 1.4 Eliminar accent removal duplicado
- `tools.py:528` `_remove_accents` → importar de `utils.remove_accents` e remover local
- `sqlite_store.py:21` `_noaccent` → importar de `utils.remove_accents` e remover local
- `search.py:12` `_normalize` → importar de `utils.normalize` e remover local
- `tools.py:533` `_extract_keywords` → importar de `utils.extract_keywords` e remover local
- `tools.py:506` `STOPWORDS` → importar de `utils.STOPWORDS`

---

## Fase 2 — Refatorar CLI para wrapper thin

### 2.1 `cli.py` `get_services()` → não precisa mais de parser/ingestion separados
- CLI chama `tools._get_sqlite()`, `tools._get_search()`, `tools._get_llm()`, etc.
- Remove `get_services()` ou simplifica

### 2.2 `cmd_add()`
- Chama `tools._add_memory(text, fact_type, closing_period, title)` para salvar
- Usa `tools._get_sqlite().get_memory(memory_id)` para obter o resultado e mostrar preview
- Preview e confirmação continuam no CLI (são UI)

### 2.3 `cmd_correct()`
- Chama `tools._correct_memory(text, id)` para o merge + save
- CLI adiciona confirmação interativa

### 2.4 `cmd_search()`
- Chama `tools._search_memories(query, top_k, fact_type, closing_period, tags)`
- Formata output com a mesma lógica de `_format_details`/ `_format_chain`

### 2.5 `cmd_list()`
- Chama `tools._list_memories(fact_type, closing_period, tags, limit)`
- CLI mostra em formato tabular

### 2.6 `cmd_get()`
- Chama `tools._get_memory_detail(id)`
- CLI formata para exibição

### 2.7 Remover do CLI
- `_infer_memory()` — usar `tools._infer_memory`
- `_show_preview()` — usar formatação de `tools._get_memory_detail` + síntese
- `_print_result()` — usar `tools._search_memories` + `utils`
- `CORRECT_PROMPT` — movido para `tools.py`

---

## Fase 3 — Adicionar comandos faltantes no CLI

### 3.1 `cmd_count()`
```python
sub.add_parser("count", help="Contar memórias")
sub.add_argument("--type", help="Filtrar por tipo")
sub.add_argument("--period", help="Filtrar por período")
sub.add_argument("--tags", help="Filtrar por tags")
```
Chama `tools._count_memories(active=True, fact_type, closing_period, tags)`

### 3.2 `cmd_list_periods()`
```python
sub.add_parser("list-periods", help="Listar períodos disponíveis")
```
Chama `tools._list_periods()`

### 3.3 `cmd_list_fact_types()`
```python
sub.add_parser("list-types", help="Listar tipos de memória")
```
Chama `tools._list_fact_types()`

### 3.4 `cmd_search_docs()`
```python
sub.add_parser("search-docs", help="Buscar documentos")
sub.add_argument("text", nargs="+")
sub.add_argument("--top-k", type=int, default=5)
```
Chama `tools._search_documents(query, top_k)`

---

## Fase 4 — Eliminar definições duplicadas

### 4.1 Centralizar slash commands
- Criar `SLASH_COMMANDS` em `router.py` como dict único
- `frontend/script.js` busca via API ou importa de router.py
- Alternativa: manter ambos mas adicionar teste que falha se divergirem

### 4.2 Gerar CHECK constraint de FactType
- Em `sqlite_store.py`, gerar dinamicamente:
```python
types = ", ".join(f"'{e.value}'" for e in FactType)
CHECK(fact_type IN ({types}))
```

### 4.3 Fix imports inline
- Mover `from app.doc_sync import _link_documents` para topo de `tools.py`

---

## Arquivos Modificados

| Arquivo | Alteração |
|---------|-----------|
| `app/services/utils.py` | **Novo** — funções compartilhadas |
| `app/services/tools.py` | Recebe CORRECT_PROMPT, refatora correct/infer, importa utils |
| `app/services/search.py` | Importa `normalize` de utils |
| `app/storage/sqlite_store.py` | Importa `remove_accents` de utils, CHECK dinâmico |
| `app/services/parser.py` | Importa `clean_tag_list` de utils |
| `cli.py` | Wrapper thin sobre tools + novos comandos |
| `frontend/script.js` | Slash commands centralizado (opcional) |
