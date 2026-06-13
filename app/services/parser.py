from __future__ import annotations

import json
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

    def _parse_response(self, content: str, original_text: str) -> Preview:
        json_str = content.strip()
        if json_str.startswith("```"):
            json_str = json_str.split("\n", 1)[1]
            json_str = json_str.rsplit("```", 1)[0]
        json_str = json_str.strip()

        data = json.loads(json_str)

        try:
            fact_type = FactType(data.get("fact_type", "other"))
        except ValueError:
            fact_type = FactType.other

        supersedes_id = data.get("supersedes_id") or None

        preview = Preview(
            title=data.get("title", "(sem título)")[:100],
            fact_type=fact_type,
            closing_period=data.get("closing_period", ""),
            description=data.get("description", original_text),
            decided_by=data.get("decided_by"),
            requested_by=data.get("requested_by"),
            approved_by=data.get("approved_by"),
            metadata=data.get("metadata"),
            supersedes_id=supersedes_id,
            is_correction=data.get("is_correction", False),
            confidence_score=float(data.get("confidence_score", 1.0)),
        )
        return preview
