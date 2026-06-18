import json
from pathlib import Path
from typing import Optional
from pydantic_settings import BaseSettings

PROVIDERS_FILE = Path(__file__).parent.parent / "providers.json"


class Settings(BaseSettings):
    # Server
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    app_reload: bool = True

    # CORS
    cors_origins: str = "*"

    # Storage
    sqlite_path: str = str(Path(__file__).parent.parent / "data" / "memories.db")
    chroma_path: str = str(Path(__file__).parent.parent / "data" / "chroma")
    documents_path: str = str(Path(__file__).parent.parent / "data" / "documents")

    # Embedding
    embedding_model: str = "paraphrase-multilingual-MiniLM-L12-v2"

    # Provider (lido do providers.json, sobrescrito pelo .env se existir)
    llm_active_provider: str = "nvidia"
    llm_provider: str = "openai"
    llm_model_id: str = "meta/llama-3.1-8b-instruct"
    llm_endpoint_url: Optional[str] = None
    llm_api_key: Optional[str] = None

    # AWS Bedrock (only used when type = "bedrock")
    aws_region: str = "us-east-1"
    aws_profile: str = "default"

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._resolve_provider()

    def _resolve_provider(self):
        """Carrega o preset ativo do providers.json e faz merge com .env"""
        try:
            providers = json.loads(PROVIDERS_FILE.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            return

        active = providers.get("active", self.llm_active_provider)
        preset = providers.get(active, {})

        mapping = {
            "type": "llm_provider",
            "model": "llm_model_id",
            "endpoint": "llm_endpoint_url",
            "region": "aws_region",
            "profile": "aws_profile",
        }

        for preset_key, attr in mapping.items():
            if preset_key in preset:
                current = getattr(self, attr, None)
                if not current:
                    setattr(self, attr, preset[preset_key])


settings = Settings()
