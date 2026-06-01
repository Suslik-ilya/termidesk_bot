from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Optional

class Settings(BaseSettings):
    # LLM Settings
    llm_api_key: str = ""
    proxy_api_url: str = "https://api.proxyapi.ru/google/v1"
    
    # Qdrant Settings
    qdrant_host: str = "localhost"
    qdrant_port: int = 6333
    
    # Redis Settings
    redis_host: str = "localhost"
    redis_port: int = 6379
    
    # ELK Settings
    logstash_host: str = "localhost"
    logstash_port: int = 50000
    
    # Jira Settings
    jira_url: Optional[str] = None
    jira_user: Optional[str] = None
    jira_api_token: Optional[str] = None
    jira_project_key: str = "TDSK"
    
    # Telegram
    telegram_bot_token: Optional[str] = None
    
    # App
    fastapi_host: str = "0.0.0.0"
    fastapi_port: int = 8000

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

settings = Settings()
