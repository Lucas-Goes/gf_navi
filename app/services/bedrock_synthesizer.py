from __future__ import annotations

from app.models import SearchResult

SYNTHESIZER_SYSTEM_PROMPT = """Você é Navi, assistente de memória institucional do maior banco da América Latina. Você ajuda analistas de fechamento mensal a consultar decisões, regras, implementações e incidentes passados.

Com base no contexto fornecido (memórias e documentos), responda à pergunta do usuário em português brasileiro.

Diretrizes:
- Organize a resposta em tópicos claros
- Cite as fontes usando [mem:id] para cada memória referenciada
- Se houver correções, indique a versão mais recente
- Inclua uma linha do tempo quando relevante
- Se não houver informação suficiente, diga honestamente
- Formate em markdown para legibilidade"""


class BedrockSynthesizer:
    def __init__(self):
        self._client = None

    @property
    def client(self):
        if self._client is None:
            from app.config import get_bedrock_client
            self._client = get_bedrock_client()
        return self._client

    def synthesize(
        self, question: str, results: list[SearchResult]
    ) -> str:
        from app.config import settings

        context_parts = []
        for r in results:
            m = r.memory
            header = f"[mem:{m.id[:8]}]"
            parts = [
                f"---\n{header}",
                f"Título: {m.title}",
                f"Tipo: {m.fact_type.value}",
                f"Período: {m.closing_period}",
                f"Descrição: {m.description}",
                f"Decidido por: {m.decided_by or '—'}",
                f"Solicitado por: {m.requested_by or '—'}",
                f"Aprovado por: {m.approved_by or '—'}",
                f"Data de registro: {m.registration_date[:10]}",
                f"Registrado por: {m.registered_by}",
            ]
            if r.warnings:
                parts.append(f"Avisos: {'; '.join(r.warnings)}")
            if r.related_documents:
                for d in r.related_documents:
                    parts.append(f"  Documento relacionado: {d.title}")
            context_parts.append("\n".join(parts))

        context = "\n".join(context_parts)
        prompt = (
            f"{SYNTHESIZER_SYSTEM_PROMPT}\n\n"
            f"Contexto das memórias institucionais:\n{context}\n\n"
            f"Pergunta do usuário:\n{question}\n\n"
            f"Resposta:"
        )

        try:
            response = self.client.converse(
                modelId=settings.bedrock_model_id,
                messages=[{"role": "user", "content": [{"text": prompt}]}],
                inferenceConfig={"maxTokens": 4000, "temperature": 0.3},
            )
            content = response["output"]["message"]["content"][0]["text"]
            return content
        except Exception as e:
            return f"**Erro ao consultar Bedrock:** {e}"
