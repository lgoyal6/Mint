from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=False)

    apollo_api_key: str | None = Field(default=None, alias="APOLLO_API_KEY")
    exa_api_key: str | None = Field(default=None, alias="EXA_API_KEY")
    gmi_api_key: str | None = Field(default=None, alias="GMI_API_KEY")
    gmi_base_url: str = Field(default="https://api.gmi-serving.com", alias="GMI_BASE_URL")
    gmi_api_format: str = Field(default="anthropic", alias="GMI_API_FORMAT")
    gmi_classifier_model: str = Field(default="anthropic/claude-haiku-4.5", alias="GMI_CLASSIFIER_MODEL")
    gmi_agent_model: str = Field(default="anthropic/claude-sonnet-4-6", alias="GMI_AGENT_MODEL")
    gmi_reviewer_model: str = Field(default="anthropic/claude-opus-4.5", alias="GMI_REVIEWER_MODEL")
    database_url: str | None = Field(default=None, alias="DATABASE_URL")
    mint_test_database_url: str | None = Field(default=None, alias="MINT_TEST_DATABASE_URL")
    luma_api_key: str | None = Field(default=None, alias="LUMA_API_KEY")
    hubspot_access_token: str | None = Field(default=None, alias="HUBSPOT_ACCESS_TOKEN")
    clay_webhook_url: str | None = Field(default=None, alias="CLAY_WEBHOOK_URL")
    clay_callback_token: str | None = Field(default=None, alias="CLAY_CALLBACK_TOKEN")
    mint_sync_token: str | None = Field(default=None, alias="MINT_SYNC_TOKEN")
    mint_auth_enabled: bool = Field(default=False, alias="MINT_AUTH_ENABLED")
    mint_auth_password: str | None = Field(default=None, alias="MINT_AUTH_PASSWORD")
    mint_session_secret: str | None = Field(default=None, alias="MINT_SESSION_SECRET")
    mint_session_cookie_name: str = Field(default="mint_session", alias="MINT_SESSION_COOKIE_NAME")
    mint_session_max_age_seconds: int = Field(default=60 * 60 * 24 * 7, alias="MINT_SESSION_MAX_AGE_SECONDS")
    mint_cookie_secure: bool = Field(default=True, alias="MINT_COOKIE_SECURE")
    mint_cookie_samesite: str = Field(default="none", alias="MINT_COOKIE_SAMESITE")
    mint_frontend_origin: str | None = Field(default=None, alias="MINT_FRONTEND_ORIGIN")
    icp_config_path: str = Field(default="config/icp.yaml", alias="ICP_CONFIG_PATH")
    competitor_terms_path: str = Field(default="config/competitor_terms.txt", alias="COMPETITOR_TERMS_PATH")
    bigtech_terms_path: str = Field(default="config/bigtech_terms.txt", alias="BIGTECH_TERMS_PATH")

    apollo_base_url: str = Field(default="https://api.apollo.io/api/v1", alias="APOLLO_BASE_URL")
    apollo_credit_cost_usd: float | None = Field(default=None, alias="APOLLO_CREDIT_COST_USD")
    exa_base_url: str = Field(default="https://api.exa.ai", alias="EXA_BASE_URL")
    hubspot_base_url: str = Field(default="https://api.hubapi.com", alias="HUBSPOT_BASE_URL")
    exa_rate_limit_per_second: float = Field(default=1.5, alias="EXA_RATE_LIMIT_PER_SECOND")
    exa_rate_limit_burst: int = Field(default=10, alias="EXA_RATE_LIMIT_BURST")

    @field_validator("gmi_base_url", "apollo_base_url", "exa_base_url", "hubspot_base_url")
    @classmethod
    def normalize_base_url(cls, value: str) -> str:
        return value.rstrip("/")

    @field_validator("gmi_api_format")
    @classmethod
    def normalize_gmi_api_format(cls, value: str) -> str:
        normalized = value.strip().lower()
        return normalized if normalized in {"anthropic", "openai"} else "anthropic"
