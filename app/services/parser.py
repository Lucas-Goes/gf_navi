"""
Parser — extrai informações estruturadas de texto livre via LLM.

Fluxo:
  1. Recebe texto do usuário (descrição de memória)
  2. Envia para LLM com PARSER_SYSTEM_PROMPT
  3. LLM retorna JSON com title, fact_type, closing_period, tags, etc.
  4. _parse_response valida e normaliza os campos
  5. Retorna um Preview (pronto para ser armazenado ou confirmado)

Usado em: tools.py (_add_memory, _correct_memory), ask_agent.py (_preview_add, _preview_correct).
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Optional

from app.models import FactType, Preview
from app.services.utils import clean_tag_list
from app.siglas import expand_tags

PARSER_SYSTEM_PROMPT = """Você é um analista de memória institucional especializado em extrair informações estruturadas de textos sobre decisões, regras, implementações e incidentes do fechamento mensal de um banco.

Extraia os campos conforme o schema JSON abaixo:

{{
  "title": "string (obrigatório, título curto de até 40 caracteres, sem aspas ou meta-instruções)",
  "fact_type": "enum: rule_change | decision | implementation | incident | other",
  "closing_period": "string (obrigatório, formato YYYY-MM do período de fechamento)",
  "description": "string (obrigatório, 1 a 3 parágrafos detalhando o fato, SEM meta-instruções como 'adicione isso')",
  "tags": "array de strings (obrigatório, lista de 1 a 5 palavras-chave que categorizam a memória, máximo 5 tags, cada tag no máximo 20 caracteres)",
  "decided_by": "string | null",
  "requested_by": "string | null",
  "approved_by": "string | null",
  "metadata": "object | null",
  "supersedes_id": "string | null (UUID da memória que esta corrige, se for o caso)",
  "is_correction": "boolean",
  "confidence_score": "number (0.0 a 1.0, use 0.0 se os dados extraídos forem insuficientes ou inconsistentes)"
}}

Regras:
- fact_type deve ser um dos valores enumerados
- closing_period é obrigatório no formato YYYY-MM
- title: MÁXIMO 40 caracteres. NÃO inclua meta-instruções como "Atualização de" ou "Correção de" — extraia apenas o título factual
- description: Extraia APENAS o conteúdo factual. Ignore instruções do tipo "adicione", "inclua", "atualize"
- Se a confiança na extração for baixa (dados ambíguos, inconsistentes ou insuficientes), defina confidence_score como 0.0
- Se o texto mencionar correção de algo anterior, marque is_correction=true
- Se houver menção explícita a um ID de memória sendo corrigido, preencha supersedes_id
- tags: OBRIGATÓRIO. Extraia de 2 a 5 palavras-chave que representem os assuntos principais (ex: compliance, crédito, sistema, processo, relatório). Use apenas o radical da palavra. Cada tag deve ter no máximo 20 caracteres. NUNCA deixe vazio.
- Retorne APENAS o JSON, sem texto adicional."""


class ParserService:
    """
    Serviço que extrai campos estruturados de texto livre via LLM.

    Attributes:
        llm: Provider LLM configurado.

    Uso:
        parser = ParserService(llm)
        preview = parser.parse("Nova regra de crédito aprovada em junho/2026...")
    """

    def __init__(self, llm):
        self.llm = llm

    def parse(self, text: str) -> Optional[Preview]:
        """
        Extrai campos estruturados do texto e retorna um Preview.

        Args:
            text: Texto livre do usuário (descrição de memória).

        Retorna: Preview com campos extraídos, ou None se falhar.

        Usado em: tools._add_memory(), ask_agent._preview_add(), etc.
        """
        prompt = f"{PARSER_SYSTEM_PROMPT}\n\nTexto do usuário:\n{text}"

        try:
            content = self.llm.invoke(
                prompt=prompt,
                max_tokens=2000,
                temperature=0.1,
            )
            return self._parse_response(content, text)
        except Exception as e:
            raise RuntimeError(f"Erro ao chamar LLM: {e}")

    def _normalize_period(self, raw: str) -> str:
        """
        Normaliza um período de fechamento para o formato YYYY-MM.

        Aceita diversos formatos de entrada: YYYY-MM, YYYYMM, DD/MM/YYYY,
        MM/YYYY, "janeiro de 2024", datas ISO, etc.

        Usado em: _parse_response().
        """
        if not raw:
            return ""

        raw = raw.strip()

        MESES_PT = {
            "janeiro": 1, "fevereiro": 2, "março": 3, "abril": 4,
            "maio": 5, "junho": 6, "julho": 7, "agosto": 8,
            "setembro": 9, "outubro": 10, "novembro": 11, "dezembro": 12,
            "jan": 1, "fev": 2, "mar": 3, "abr": 4, "mai": 5, "jun": 6,
            "jul": 7, "ago": 8, "set": 9, "out": 10, "nov": 11, "dez": 12,
        }

        if re.match(r"^\d{4}-(0[1-9]|1[0-2])$", raw):
            ano = int(raw[:4])
            if 1900 <= ano <= 2099:
                return raw

        m = re.match(r"^\d{4}(\d{2})$", raw)
        if m:
            return f"{raw[:4]}-{raw[4:]}"

        m = re.match(r"(\d{2})/(\d{2})/(\d{4})", raw)
        if m:
            return f"{m.group(3)}-{m.group(2)}"

        m = re.match(r"(\d{2})/(\d{4})", raw)
        if m and 1 <= int(m.group(1)) <= 12:
            return f"{m.group(2)}-{m.group(1)}"

        m = re.match(r"(\d{4})-(\d{2})-(\d{2})", raw)
        if m:
            return f"{m.group(1)}-{m.group(2)}"

        raw_lower = raw.lower()
        for nome, num in MESES_PT.items():
            if nome in raw_lower:
                m = re.search(r"(\d{4})", raw)
                if m:
                    return f"{m.group(1)}-{num:02d}"

        try:
            parsed = datetime.fromisoformat(raw)
            return f"{parsed.year}-{parsed.month:02d}"
        except ValueError:
            pass

        return raw

    def _parse_response(self, content: str, original_text: str) -> Preview:
        """
        Converte a resposta JSON do LLM em um objeto Preview validado.

        Etapas:
          1. Extrai JSON do texto (remove ```json ... ``` se presente)
          2. Faz parse do JSON
          3. Valida/normaliza fact_type, closing_period, tags
          4. Cria Preview

        Args:
            content: Resposta crua do LLM.
            original_text: Texto original do usuário (fallback p/ description).

        Retorna: Preview validado.

        Usado em: ParserService.parse().
        """
        raw = content.strip()

        m = re.search(r"```(?:json)?\s*\n(.*?)```", raw, re.DOTALL)
        if m:
            json_str = m.group(1).strip()
        else:
            json_str = raw

        start = json_str.find("{")
        end = json_str.rfind("}")
        if start != -1 and end != -1 and end >= start:
            json_str = json_str[start: end + 1]
        json_str = json_str.strip()

        data = json.loads(json_str)

        try:
            fact_type = FactType(data.get("fact_type", "other"))
        except ValueError:
            fact_type = FactType.other

        supersedes_id = data.get("supersedes_id") or None

        raw_period = data.get("closing_period", "")
        closing_period = self._normalize_period(raw_period)
        if not closing_period or not re.match(r"^\d{4}-(0[1-9]|1[0-2])$", closing_period):
            closing_period = datetime.now().strftime("%Y-%m")

        description = data.get("description")
        if not description or not description.strip():
            description = original_text

        raw_cs = data.get("confidence_score")
        if raw_cs is None or raw_cs == "null" or raw_cs == "":
            cs = 1.0
        else:
            cs = float(raw_cs)

        raw_tags = data.get("tags", [])
        if isinstance(raw_tags, str):
            raw_tags = [raw_tags]
        cleaned_tags = clean_tag_list(raw_tags)
        if not cleaned_tags:
            from app.services.utils import extract_keywords
            kw = extract_keywords(f"{data.get('title', '')} {description}")
            cleaned_tags = kw.split()[:5] if kw else []
        else:
            seen = set()
            deduped = []
            for t in cleaned_tags:
                if t not in seen:
                    seen.add(t)
                    deduped.append(t)
            cleaned_tags = deduped[:5]
        expanded = expand_tags(cleaned_tags)
        if len(expanded) > len(cleaned_tags):
            cleaned_tags = expanded[:5]

        preview = Preview(
            title=data.get("title", "(sem título)")[:100],
            fact_type=fact_type,
            closing_period=closing_period,
            description=description,
            tags=cleaned_tags,
            decided_by=data.get("decided_by"),
            requested_by=data.get("requested_by"),
            approved_by=data.get("approved_by"),
            metadata=data.get("metadata"),
            supersedes_id=supersedes_id,
            is_correction=data.get("is_correction", False),
            confidence_score=cs,
        )
        return preview
