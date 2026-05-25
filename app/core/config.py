from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_env: str = "dev"
    database_url: str = "postgresql+psycopg://postgres:postgres@localhost:5432/quiz_db"
    llm_provider: str = "google"
    embed_provider: str = "google"
    retrieval_provider: str = "google"
    ollama_base_url: str = "http://localhost:11434"
    ollama_chat_model: str = "qwen3:1.7b"
    ollama_embed_model: str = "nomic-embed-text"
    google_cloud_project: str | None = None
    google_cloud_project_id: str | None = None
    google_cloud_location: str = "us-central1"
    google_genai_api_key: str | None = None
    google_chat_model: str = "gemini-2.5-flash"
    google_embed_model: str = "text-embedding-005"
    google_discovery_engine_id: str | None = None
    google_discovery_data_store_id: str | None = None
    google_discovery_serving_config: str = "default_search"
    google_application_credentials: str | None = None
    google_agent_search_page_size: int = 4
    secret_key: str = "change-me"
    access_token_expire_hours: int = 24

    # CORS
    cors_origins: list[str] = ["*"]

    # Hybrid retrieval settings
    retrieval_semantic_weight: float = 0.6
    retrieval_bm25_weight: float = 0.4
    retrieval_candidate_multiplier: int = 3
    retrieval_rrf_k: int = 60
    retrieval_mmr_lambda: float = 0.7
    retrieval_fts_config: str = "portuguese_unaccent"
    retrieval_max_distance: float = 0.7

    # Feedback pipeline settings
    feedback_max_regeneration_attempts: int = 2
    feedback_relevance_threshold: int = 4
    feedback_enable_validator: bool = False
    feedback_enable_relevance_filter: bool = True
    max_concurrent_embeddings: int = 2
    max_concurrent_retrievals: int = 4
    max_concurrent_llm_calls: int = 2
    max_concurrent_validations: int = 1
    question_feedback_timeout_seconds: int = 120
    max_quota_errors_per_attempt: int = 3
    enable_bm25_fallback: bool = True

    model_config = SettingsConfigDict(env_file=".env", case_sensitive=False)


@lru_cache
def get_settings() -> Settings:
    return Settings()
