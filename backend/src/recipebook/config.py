from pathlib import Path
from urllib.parse import urlparse
from pydantic import model_validator, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[3]

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / '.env', extra='ignore')
    app_env: str = 'development'
    database_url: str = 'sqlite:///./data/recipebook.db'
    public_app_url: str = 'http://localhost:5173'
    telegram_bot_token: str = ''
    telegram_allowed_user_id: int | None = None
    dev_auth_enabled: bool = False
    session_ttl_seconds: int = 86400
    telegram_auth_max_age_seconds: int = 300
    llm_provider: str = 'openai'
    llm_model: str = ''
    llm_api_key: str = ''
    llm_base_url: str | None = None
    llm_timeout_seconds: int = 60
    llm_max_output_tokens: int = 6000
    llm_max_conversation_tokens: int = 16000

    @field_validator('telegram_allowed_user_id', mode='before')
    @classmethod
    def blank_id(cls, value): return None if value == '' else value

    @model_validator(mode='after')
    def valid_production(self):
        if self.app_env == 'production':
            if self.dev_auth_enabled or not self.telegram_bot_token or not self.telegram_allowed_user_id:
                raise ValueError('Production requires Telegram credentials and disabled dev auth')
            if urlparse(self.public_app_url).scheme != 'https':
                raise ValueError('Production requires HTTPS')
        return self
