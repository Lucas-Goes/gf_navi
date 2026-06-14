from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Optional

from app.models import FactType, Preview

PARSER_SYSTEM_PROMPT = """Você é um analista de memória institucional especializado em extrair informações estruturadas de textos sobre decisões, regras, implementações e incidentes do fechamento mensal de um banco.

Extraia os campos conforme o schema JSON abaixo:

{
  "title": "string (obrigatório, título curto de até 100 caracteres)",
  "fact_type": "enum: rule_change | decision | implementation | incident | other",
  "closing_period": "string (obrigatório, formato YYYY-MM do período de fechamento)",
  "description": "string (obrigatório, 1 a 3 parágrafos detalhando o fato)",
  "decided_by": "string | null (quem decidiu, se mencionado)",
  "requested_by": "string | null (quem solicitou, se mencionado)",
  "approved_by": "string | null (quem aprovou, se mencionado)",
  "metadata": "object | null (informações adicionais em chave-valor)",
  "supersedes_id": "string | null (UUID da memória que esta corrige, se for o caso)",
  "is_correction": "boolean (true se estiver corrigindo/substituindo outra memória)",
  "confidence_score": "number (0.0 a 1.0, o quão confiante você está sobre a extração)"
}

Regras:
- fact_type deve ser um dos valores enumerados
- closing_period é obrigatório no formato YYYY-MM
- Se o texto mencionar correção de algo anterior, marque is_correction=true
- Se houver menção explícita a um ID de memória sendo corrigido, preencha supersedes_id
- Retorne APENAS o JSON, sem texto adicional."""


class ParserService:
    def __init__(self, llm):
        self.llm = llm

    def parse(self, text: str) -> Optional[Preview]:
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
        raw = content.strip()

        m = re.search(r"```(?:json)?\s*\n(.*?)```", raw, re.DOTALL)
        if m:
            json_str = m.group(1).strip()
        else:
            json_str = raw

        start = json_str.find("{")
        end = json_str.rfind("}")
        if start != -1 and end != -1 and end >= start:
            json_str = json_str[start : end + 1]
        json_str = json_str.strip()

        data = json.loads(json_str)

        try:
            fact_type = FactType(data.get("fact_type", "other"))
        except ValueError:
            fact_type = FactType.other

        supersedes_id = data.get("supersedes_id") or None

        raw_period = data.get("closing_period", "")
        closing_period = self._normalize_period(raw_period)

        description = data.get("description")
        if not description or not description.strip():
            description = original_text

        raw_cs = data.get("confidence_score")
        if raw_cs is None or raw_cs == "null" or raw_cs == "":
            cs = 1.0
        else:
            cs = float(raw_cs)

        preview = Preview(
            title=data.get("title", "(sem título)")[:100],
            fact_type=fact_type,
            closing_period=closing_period,
            description=description,
            decided_by=data.get("decided_by"),
            requested_by=data.get("requested_by"),
            approved_by=data.get("approved_by"),
            metadata=data.get("metadata"),
            supersedes_id=supersedes_id,
            is_correction=data.get("is_correction", False),
            confidence_score=cs,
        )
        return preview
