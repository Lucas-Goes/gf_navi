import os
from pathlib import Path
from typing import Optional
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Server
    app_host: str = "0.0.0.0"
    app_port: int = 8000

    # Storage
    sqlite_path: str = str(Path(__file__).parent.parent / "data" / "memories.db")
    chroma_path: str = str(Path(__file__).parent.parent / "data" / "chroma")
    documents_path: str = str(Path(__file__).parent.parent / "data" / "documents")

    # Embedding
    embedding_model: str = "paraphrase-multilingual-MiniLM-L12-v2"

    # LLM Provider: "bedrock" | "openai"
    llm_provider: str = "openai"
    llm_model_id: str = "meta/llama-3.1-8b-instruct"
    llm_endpoint_url: Optional[str] = None
    llm_api_key: Optional[str] = None

    # AWS Bedrock (only used when llm_provider = "bedrock")
    aws_region: str = "us-east-1"
    aws_profile: str = "default"

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()
