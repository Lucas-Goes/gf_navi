import os
from pathlib import Path
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    sqlite_path: str = str(Path(__file__).parent.parent / "data" / "memories.db")
    chroma_path: str = str(Path(__file__).parent.parent / "data" / "chroma")
    embedding_model: str = "paraphrase-multilingual-MiniLM-L12-v2"
    aws_region: str = "us-east-1"
    aws_profile: str = "default"
    bedrock_model_id: str = "anthropic.claude-3-opus-20240229-v1:0"
    documents_path: str = str(Path(__file__).parent.parent / "data" / "documents")

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()


def get_bedrock_client():
    import boto3
    try:
        session = boto3.Session(profile_name=settings.aws_profile)
        return session.client("bedrock-runtime", region_name=settings.aws_region)
    except Exception:
        session = boto3.Session()
        return session.client("bedrock-runtime", region_name=settings.aws_region)
