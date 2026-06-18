"""
Utilitários gerais para processamento de texto.

Funções:
  smart_truncate   → truncar texto em limite de palavra (usado em síntese e preview)
  remove_accents   → remover acentos (usado em busca por palavras-chave)
  normalize        → normalizar para ASCII lowercase (usado em comparações)
  clean_tags       → limpar tags de string CSV (usado no parser e CLI)
  clean_tag_list   → limpar lista de tags (usado no parser)
  extract_keywords → extrair palavras-chave relevantes
  STOPWORDS        → conjunto de stopwords em português
"""

from __future__ import annotations

import re
import unicodedata


STOPWORDS = frozenset("""a ante ao aos após até com contra de desde em entre
para perante por sem sob sobre trás o a os as da das do dos dum duns
num nums numa um uma umas uns ele ela eles elas me te se nos vos
lhe lhes eu tu você vocês o a os as meu minha meus minhas teu tua
teus tuas seu sua seus suas nosso nossa nossos nossas isso isto esse
essa esses esses estas este esta estes estas aquele aquela aquelas aquilo
que qual quem como quanto quanta quantos quantas onde aonde donde
quando porque porquê pois já também ainda muito pouco mais menos
demais todo toda todos todas algum alguma alguns algumas nenhum nenhuma
nenhuns nenhumas certo certa certos certas outro outra outros outras
vário vária vários várias tanto tanta tantos quantos quanto quanta
quantos qualquer quaisquer cada qual seja seja se caso sim não nem
era são fora fosse fosse fossem fosseis fosseis temos têm tem havia
haja hajam hajas hajamos hajais haja são seja seja sejamos sejais
sejam seria seriam seria seriam será serão seria seriam era eram é
são está estão estava estavam esteve estivera estiveram estivera
esteve estiveram estiverem estejamos estejais esteja estejam esteja
fui foi fomos foram fora foram fosse fosse fossem fosseis fosseis
fosse fosse fossem fora foram irei irá irão iria iriam iria iriam
vá vão vamos vais vai vou vai vai vão vamos""".split())


def smart_truncate(text: str, max_len: int = 50) -> str:
    """
    Trunca texto no limite de palavra mais próximo de max_len,
    evitando cortar no meio de uma palavra. Adiciona '…' ao final.

    Usado em:
      - synthesis.py: exibir descrição de memória na resposta
      - tools.py:   truncar descrições nos retornos das ferramentas
      - parser.py:  truncar título no Preview
      - ask_agent.py: truncar título personalizado

    Exemplo:
      smart_truncate("Mudança na regra de cadastro", 20) → "Mudança na regra…"
    """
    if len(text) <= max_len:
        return text
    cut = text.rfind(" ", 0, max_len - 1)
    if cut == -1:
        return text[:max_len].rstrip() + "…"
    return text[:cut].rstrip() + "…"


def remove_accents(text: str) -> str:
    """Remove acentos de uma string. Ex: 'cadastro' de 'cadastro'."""
    nfkd = unicodedata.normalize("NFKD", text)
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def normalize(text: str) -> str:
    """Remove acentos e converte para ASCII lowercase. Ex: 'cadastro'."""
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii").lower()


def clean_tags(tags_str: str) -> list[str]:
    """
    Converte string CSV de tags em lista limpa.
    Suporta separação por vírgula OU espaço.
    Usado em: CLI (cmd_add), tools.py, tool_queries.py.
    """
    return [t.strip().lower()[:20] for t in re.split(r"[,\s]+", tags_str) if t.strip()]


def clean_tag_list(raw_tags: list) -> list[str]:
    """
    Limpa lista de tags: lower, strip, máximo 20 caracteres, máximo 5 tags.
    Usado em: parser.py (pós-extração da LLM).
    """
    cleaned = [str(t).strip().lower()[:20] for t in raw_tags if t and str(t).strip()]
    return cleaned[:5]


def extract_keywords(text: str) -> str:
    """
    Extrai palavras-chave relevantes (não-stopwords, >2 caracteres).
    Usado em: agent.py (refinamento de busca quando ferramenta retorna vazio).
    """
    plain = remove_accents(text)
    words = plain.lower().split()
    seen = set()
    keywords = []
    for w in words:
        w = w.strip(""".,;:!?()[]{}"'""")
        if len(w) > 2 and w not in STOPWORDS and not w.startswith(("http", "www")) and w not in seen:
            seen.add(w)
            keywords.append(w)
    return " ".join(keywords[:10])
