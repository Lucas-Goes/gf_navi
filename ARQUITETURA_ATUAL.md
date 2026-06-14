# Arquitetura Atual — Cérebro Institucional (Navi)

> Sistema local de memória institucional para fechamento mensal.
> Stack: Python 3.13 + FastAPI + SQLite + ChromaDB + MiniLM (embedding local) + LLM provider genérico (NVIDIA/Bedrock/Ollama).

---

## 1. Visão Geral

- **Navi**: assistente via CLI + chat web (FastAPI + frontend HTML/JS/CSS).
- Três formas de entrada:
  - **Slash commands** (`/add`, `/search`, `/list`, etc.) — rota determinística, sem LLM.
  - **Linguagem natural** → LLM Router classifica intenção → executa tool → sintetiza resposta.
  - **Saudações** → resposta instantânea pré-definida.
- **1 endpoint:** `POST /api/chat` (SSE streaming).
- Dados nunca saem da máquina (embedding local, LLM opcional via API externa).

---

## 2. Stack Técnica

| Camada      | Tecnologia |
|-------------|------------|
| Backend     | Python 3.13 / FastAPI / Uvicorn (sync) |
| Frontend    | HTML + Tailwind CDN + Canvas (fairy theme) + marked.js + highlight.js |
| Database    | SQLite (`sqlite3` stdlib, síncrono) |
| Vector DB   | ChromaDB `PersistentClient` (embedded) |
| Embedding   | `paraphrase-multilingual-MiniLM-L12-v2` (sentence-transformers, local, 384 dims) |
| LLM         | Provider genérico via `LLMProvider` (OpenAI-compatible NVIDIA/Ollama/vLLM ou AWS Bedrock) |
| CLI         | `argparse` + `rich` |
| Frontend    | Servido como static do FastAPI |

---

## 3. Estrutura de Diretórios

```
gf_navi/
├── app/
│   ├── __init__.py
│   ├── main.py                  # FastAPI entry + uvicorn
│   ├── config.py                # Pydantic Settings
│   ├── models.py                # Pydantic schemas (Memory, Document, Preview, SearchResult)
│   ├── db_viewer.py             # CLI database viewer (Rich tables, export JSON/CSV)
│   ├── doc_sync.py              # Document sync + memory-document linking
│   ├── api/
│   │   └── routes.py            # POST /api/chat (SSE)
│   ├── storage/
│   │   ├── sqlite_store.py      # SQLite CRUD + migrations + noaccent search
│   │   └── vector_store.py      # ChromaDB wrapper (2 collections)
│   └── services/
│       ├── ask_agent.py         # AskAgent: high-level entry (Router + ReActAgent)
│       ├── agent.py             # ReActAgent: multi-turn tool loop (max 5 steps)
│       ├── router.py            # Slash commands + LLM intent classifier
│       ├── tools.py             # TOOL_DEFINITIONS (11 tools) + execution + singletons
│       ├── synthesis.py         # LLM answer synthesis + programmatic formatting
│       ├── ingestion.py         # Memory creation + preview + confirmation
│       ├── search.py            # Hybrid search (vector + SQL + term bonus)
│       ├── parser.py            # LLM text → structured Preview
│       └── llm.py               # LLMProvider abstraction (Bedrock, OpenAI-compatible)
├── cli.py                       # CLI entry point (add, correct, ask, search, list, db, etc.)
├── frontend/
│   ├── index.html               # Chat UI
│   ├── script.js                # SSE chat client + markdown rendering
│   └── styles.css               # Fairy theme
├── data/
│   ├── documents/               # .txt files for document sync
│   ├── memories.db              # SQLite database
│   └── chroma/                  # ChromaDB persist directory
├── tests/
│   ├── test_models.py           # 13 tests
│   ├── test_parser.py           # 19 tests
│   ├── test_ingestion.py        # 10 tests
│   ├── test_sqlite_store.py     # 15 tests
│   └── test_vector_store.py     # 9 tests
├── .env                         # LLM provider config
├── providers.json               # LLM presets (nvidia, bedrock, ollama)
└── requirements.txt
```

---

## 4. Fluxo Principal de Pergunta

```
Input → parse_slash()?
  ├─ Sim → executa tool direta, SEM LLM
  │        (/add, /search, /list, /get, /correct, /count, /help, /sync-docs)
  │
  └─ Não → _respond_greeting()?
             ├─ Sim → resposta instantânea ("Oi!", "Obrigado!", etc.)
             │
             └─ Não → classify_with_llm() — LLM Router (8 categorias)
                        ├─ greeting → resposta amigável
                        ├─ help → lista de ferramentas
                        ├─ add/correct/sync → tool única + síntese
                        ├─ list/count/search/get → ReActAgent.run()
                        │     └─ Loop multi-turn (MAX_STEPS=5):
                        │         1. LLM decide → tool + params
                        │         2. Executa tool
                        │         3. Se done → síntese
                        │         4. Se repetiu → força final
                        └─ fallback → ReActAgent sem tool inicial
```

---

## 5. Serviços Detalhados

### 5.1 Router (`router.py`)
- `parse_slash(text)`: detecta `/command` → retorna `(tool, params)` — zero LLM.
- `classify_with_llm(llm, question)`: 8 intenções disjuntivas (ordem de prioridade):
  1. SAUDACAO → greeting direto
  2. HELP → help tool
  3. ACTION_ADD → add_memory
  4. ACTION_CORRECT → correct_memory
  5. QUERY_LIST → list_memories
  6. QUERY_COUNT → count_memories
  7. QUERY_TOPIC → search_memories
  8. QUERY_ID → get_memory_detail

### 5.2 ReActAgent (`agent.py`)
- Loop de até 5 steps.
- Cada iteração: LLM decide `{"tool":..., "params":...}` ou `{"answer":"...", "done":true}`.
- Proteção contra loop: `_check_repeated()` detecta tool+params duplicados.
- Progresso visível: "⚙️ Passo 1: search_memories..."

### 5.3 Synthesis (`synthesis.py`)
- `synthesize_answer_stream()`: gera resposta final.
- Se `search_memories`: formatação **programática** 📌 título + 1-2 frases LLM + chain + detalhes.
- Outras tools: LLM sintetiza livremente.
- Prompt proíbe misturar informações de memórias diferentes.

### 5.4 Search (`search.py`)
- `hybrid_search()`: vector embedding + term bonus (rerank).
- `_term_bonus()`: substring matching, min 2 chars, peso 0.8.
- `fetch_k = max(top_k * 5, 15)` — buffer amplo pro rerank.
- Filtra resultados obsoletos (memórias `superseded_by` são removidas).

### 5.5 Tools (`tools.py`)
- 11 ferramentas registradas em `TOOL_DEFINITIONS`.
- `_execute_tool()` com type coercion, validação de params obrigatórios, proteção contra erro Pydantic.
- Singletons (`_get_sqlite`, `_get_vector`, `_get_llm`, etc.) com lazy init.

---

## 6. Correção de Memórias

- Memória nova recebe `supersedes_id` → antiga ganha `superseded_by` e `is_active=0`.
- ChromaDB mantém ambas (antiga ainda indexada, mas filtrada nos resultados).
- Na resposta: timeline completa da chain (v1 → v2 → v3), com datas e marcador "← atual".

---

## 7. Diferenças da ARQUITETURA.md (original)

### Removidos/Substituídos
- **BedrockParser** → `parser.py` (genérico, provider-agnóstico)
- **BedrockSynthesizer** → `synthesis.py` (genérico + formatação programática)
- **scripts/ingest_docs.py** → `doc_sync.py` (integrado, com linking automático)
- **pywebview** → removido (sem deploy desktop no momento)
- **CorrectionChain** (separado) → incorporado no `SearchService`

### Novos
- **`router.py`** — slash commands + LLM classifier (substituiu `_route_question` com keyword match)
- **`agent.py`** — ReActAgent multi-turn
- **`tools.py`** — tool registry + singletons (resolve circular import)
- **`synthesis.py`** — síntese + formatação programática
- **`cli.py`** — CLI completo (add, correct, ask, search, list, get, db, sync-docs, provider)
- **`db_viewer.py`** — database viewer via CLI (Rich tables)
- **Term bonus rerank** no `SearchService`
- **`noaccent()`** SQLite function para busca accent-insensitive
- **11 tools** no AskAgent vs 0 no original (antes era fluxo fixo sem tools)
- **Tags** — campo `tags TEXT` no SQLite, extraído pelo LLM no parser, exibido em outputs CLI/API e usado na síntese para evitar mistura de assuntos

### Modificados
- **LLM provider**: Bedrock-only → genérico (OpenAI-compatible + Bedrock)
- **Frontend**: Tailwind + Canvas → fairy theme com glassmorphism + highlight.js
- **API**: `/chat` POST com SSE, mesma interface CLI e API
- **Parser**: `closing_period` vazio agora usa mês corrente como fallback
- **Search**: term bonus + filtro de memórias obsoletas + vector rerank
- **Testes**: de 0 para 66 testes unitários

---

## 8. Próximas Melhorias (Sugeridas)

1. **FTS5** no SQLite — busca full-text stemmed em português (bônus: radical "politic" encontra "política", "políticas").
2. **Watch do diretório** `data/documents/` — sync automático ao detectar novos arquivos.
3. **Autenticação** simples (token) se for multi-usuário.
4. **Deploy desktop** via pywebview + PyInstaller (revisitar).
