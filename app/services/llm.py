"""
Provedores de LLM (Large Language Model).

Hierarquia:
  LLMProvider (ABC)
    ├── BedrockProvider          → AWS Bedrock (Converse API)
    └── OpenAICompatibleProvider → qualquer API compatível com OpenAI (NVIDIA, Ollama, vLLM, etc.)

Uso:
  provider = create_provider(config)
  resposta = provider.invoke(prompt="...", system_prompt="...", max_tokens=2000)
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from typing import Generator, Optional

import requests

from app.services.logger import logger


class LLMProvider(ABC):
    """Classe base abstrata para todos os provedores de LLM."""

    @abstractmethod
    def invoke(self, prompt: str, system_prompt: str = "",
               max_tokens: int = 2000, temperature: float = 0.1) -> str:
        ...

    def invoke_stream(self, prompt: str, system_prompt: str = "",
                      max_tokens: int = 2000, temperature: float = 0.3
                      ) -> Generator[str, None, None]:
        yield self.invoke(prompt, system_prompt, max_tokens, temperature)


class BedrockProvider(LLMProvider):
    def __init__(self, model_id: str, region: str = "us-east-1",
                 profile: str = "default"):
        import boto3
        from botocore.exceptions import NoCredentialsError, ProfileNotFound
        self.model_id = model_id
        try:
            session = boto3.Session(profile_name=profile)
            self.client = session.client("bedrock-runtime", region_name=region)
        except (ProfileNotFound, Exception):
            logger.warning("Perfil AWS '%s' não encontrado. Usando perfil padrão.", profile)
            session = boto3.Session()
            self.client = session.client("bedrock-runtime", region_name=region)

    def invoke(self, prompt: str, system_prompt: str = "",
               max_tokens: int = 2000, temperature: float = 0.1) -> str:
        messages = [{"role": "user", "content": [{"text": prompt}]}]

        kwargs = {
            "modelId": self.model_id,
            "messages": messages,
            "inferenceConfig": {"maxTokens": max_tokens, "temperature": temperature},
        }
        if system_prompt:
            kwargs["system"] = [{"text": system_prompt}]

        response = self.client.converse(**kwargs)
        return response["output"]["message"]["content"][0]["text"]

    def invoke_stream(self, prompt: str, system_prompt: str = "",
                      max_tokens: int = 2000, temperature: float = 0.3
                      ) -> Generator[str, None, None]:
        messages = [{"role": "user", "content": [{"text": prompt}]}]

        kwargs = {
            "modelId": self.model_id,
            "messages": messages,
            "inferenceConfig": {"maxTokens": max_tokens, "temperature": temperature},
        }
        if system_prompt:
            kwargs["system"] = [{"text": system_prompt}]

        streaming_response = self.client.converse_stream(**kwargs)
        stream = streaming_response.get("stream", [])
        for event in stream:
            if "contentBlockDelta" in event:
                delta = event["contentBlockDelta"]["delta"].get("text", "")
                if delta:
                    yield delta


class OpenAICompatibleProvider(LLMProvider):
    def __init__(self, model_id: str, endpoint_url: str,
                 api_key: Optional[str] = None):
        self.model_id = model_id
        self.endpoint_url = endpoint_url.rstrip("/")
        self.api_key = api_key

    def _build_headers(self) -> dict:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def _build_payload(self, prompt: str, system_prompt: str = "",
                       max_tokens: int = 2000, temperature: float = 0.1,
                       stream: bool = False) -> dict:
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        return {
            "model": self.model_id,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": stream,
        }

    def _parse_choice(self, data: dict) -> str:
        choices = data.get("choices")
        if not choices:
            raise RuntimeError(f"LLM retornou resposta sem choices: {data}")
        message = choices[0].get("message") or choices[0].get("delta", {})
        content = message.get("content")
        if content is None:
            raise RuntimeError(f"LLM retornou message sem content: {message}")
        return content

    def invoke(self, prompt: str, system_prompt: str = "",
               max_tokens: int = 2000, temperature: float = 0.1) -> str:
        payload = self._build_payload(prompt, system_prompt, max_tokens, temperature, stream=False)
        resp = requests.post(
            f"{self.endpoint_url}/chat/completions",
            headers=self._build_headers(),
            json=payload,
            timeout=(15, 90),
        )
        resp.raise_for_status()
        return self._parse_choice(resp.json())

    def invoke_stream(self, prompt: str, system_prompt: str = "",
                      max_tokens: int = 2000, temperature: float = 0.3
                      ) -> Generator[str, None, None]:
        payload = self._build_payload(prompt, system_prompt, max_tokens, temperature, stream=True)
        payload.pop("stream", None)

        try:
            resp = requests.post(
                f"{self.endpoint_url}/chat/completions",
                headers=self._build_headers(),
                json={**payload, "stream": True},
                stream=True,
                timeout=(15, 90),
            )
            resp.raise_for_status()

            for line in resp.iter_lines(decode_unicode=True):
                if not line:
                    continue
                if line == "data: [DONE]":
                    break
                if line.startswith("data: "):
                    try:
                        chunk = json.loads(line[6:])
                        delta = chunk["choices"][0].get("delta", {}).get("content", "")
                        if delta:
                            yield delta
                    except (json.JSONDecodeError, KeyError, IndexError):
                        continue
        except requests.exceptions.Timeout:
            yield "\n\n❌ O serviço LLM não respondeu a tempo. Verifique se a API key é válida ou tente outro provedor."
        except Exception as e:
            yield f"\n\n❌ Erro no LLM: {e}"


def create_provider(config) -> LLMProvider:
    provider_type = config.llm_provider

    if provider_type == "bedrock":
        return BedrockProvider(
            model_id=config.llm_model_id,
            region=config.aws_region,
            profile=config.aws_profile,
        )
    elif provider_type == "openai":
        return OpenAICompatibleProvider(
            model_id=config.llm_model_id,
            endpoint_url=config.llm_endpoint_url or "http://localhost:8000/v1",
            api_key=config.llm_api_key,
        )
    else:
        raise ValueError(f"Unknown LLM provider: {provider_type}")
