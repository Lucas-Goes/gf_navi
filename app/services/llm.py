from __future__ import annotations

from abc import ABC, abstractmethod
from typing import AsyncGenerator, Generator, Optional

import requests


class LLMProvider(ABC):
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
        self.model_id = model_id
        try:
            session = boto3.Session(profile_name=profile)
            self.client = session.client("bedrock-runtime", region_name=region)
        except Exception:
            print(f"   ⚠️  Perfil AWS '{profile}' não encontrado. Usando perfil padrão.")
            session = boto3.Session()
            self.client = session.client("bedrock-runtime", region_name=region)

    def invoke(self, prompt: str, system_prompt: str = "",
               max_tokens: int = 2000, temperature: float = 0.1) -> str:
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": [{"text": system_prompt}]})
        messages.append({"role": "user", "content": [{"text": prompt}]})

        response = self.client.converse(
            modelId=self.model_id,
            messages=messages,
            inferenceConfig={"maxTokens": max_tokens, "temperature": temperature},
        )
        return response["output"]["message"]["content"][0]["text"]


class OpenAICompatibleProvider(LLMProvider):
    def __init__(self, model_id: str, endpoint_url: str,
                 api_key: Optional[str] = None):
        self.model_id = model_id
        self.endpoint_url = endpoint_url.rstrip("/")
        self.api_key = api_key

    def invoke(self, prompt: str, system_prompt: str = "",
               max_tokens: int = 2000, temperature: float = 0.1) -> str:
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        payload = {
            "model": self.model_id,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }

        resp = requests.post(
            f"{self.endpoint_url}/chat/completions",
            headers=headers,
            json=payload,
            timeout=120,
        )
        resp.raise_for_status()
        data = resp.json()
        choices = data.get("choices")
        if not choices:
            raise RuntimeError(f"LLM retornou resposta sem choices: {data}")
        message = choices[0].get("message")
        if not message:
            raise RuntimeError(f"LLM retornou choice sem message: {choices[0]}")
        content = message.get("content")
        if content is None:
            raise RuntimeError(f"LLM retornou message sem content: {message}")
        return content


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
