import os
import os
from typing import List
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field


class Settings(BaseSettings):
    # Pydantic v2 settings config (do NOT also declare inner Config)
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra='ignore',  # ignore unknown keys in .env to prevent startup crashes
    )
    data_dir: str = Field(default="data")
    cache_dir: str = Field(default="data/cache")
    dataset_path: str = Field(default="data/dataset.jsonl")
    results_path: str = Field(default="data/results.jsonl")

    # llm options
    llm_provider: str = Field(default="local")  # local or openai
    llm_api_base: str = Field(default="http://127.0.0.1:1234/v1")
    llm_model: str = Field(default="gpt-4o-mini")
    llm_temperature: float = Field(default=0.0)
    openai_api_key: str = Field(default_factory=lambda: os.getenv("OPENAI_API_KEY", ""))

    # action safety
    actions_per_15min: int = Field(default=800)
    min_action_jitter_ms: int = Field(default=800)
    max_action_jitter_ms: int = Field(default=1700)

    headless: bool = Field(default=False)

    # browser tuning
    user_agent: str = Field(
        default=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        )
    )
    chromium_args: List[str] = Field(
        default_factory=lambda: [
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
            "--disable-dev-shm-usage",
        ]
    )
    user_data_dir: str = Field(default="data/pw_user")
    slow_mo_ms: int = Field(default=0)

    # debug
    debug_screenshots: bool = Field(default=False)
    screenshot_dir: str = Field(default="data/debug")

    # scraping scroll tuning
    scrape_scroll_wait_ms: int = Field(default=950)      # wait after each scroll
    scrape_scroll_step_px: int = Field(default=500)     # how far to scroll each step
    scrape_scroll_max_iters: int = Field(default=250)    # max scroll attempts per search
    scrape_scroll_stable_iters: int = Field(default=7)   # stop if no new cells after N steps

    # api server bind
    api_host: str = Field(default_factory=lambda: os.getenv("API_HOST", "127.0.0.1"))
    api_port: int = Field(default_factory=lambda: int(os.getenv("API_PORT", "8000")))

    # Note: for Pydantic v1, use a separate branch to define Config.


settings = Settings()
