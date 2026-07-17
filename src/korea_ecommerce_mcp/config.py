from __future__ import annotations

from pathlib import Path

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="KEIC_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    allow_mutations: bool = False
    database_path: Path = Path("korea_ecommerce_integrated_channel.sqlite3")
    profile_directory: Path = Path("profiles")
    request_timeout_seconds: float = Field(default=30.0, ge=1, le=120)
    approval_ttl_seconds: int = Field(default=900, ge=60, le=3600)

    naver_client_id: str | None = None
    naver_client_secret: str | None = None
    naver_account_id: str | None = None
    naver_base_url: str = "https://api.commerce.naver.com/external"

    coupang_vendor_id: str | None = None
    coupang_access_key: str | None = None
    coupang_secret_key: str | None = None
    coupang_base_url: str = "https://api-gateway.coupang.com"

    elevenst_api_key: str | None = None
    elevenst_base_url: str = "https://api.11st.co.kr/rest"
    elevenst_create_path: str = "/prodservices/product"
    elevenst_update_path: str = "/prodservices/product/{external_id}"
    elevenst_delete_path: str = "/prodservices/product/{external_id}"
    elevenst_stop_path: str = "/prodstatservice/stat/stopdisplay/{external_id}"
    elevenst_resume_path: str = "/prodstatservice/stat/restartdisplay/{external_id}"

    esm_master_id: str | None = None
    esm_secret_key: str | None = None
    esm_gmarket_seller_id: str | None = None
    esm_auction_seller_id: str | None = None
    esm_issuer: str | None = None
    esm_base_url: str = "https://sa2.esmplus.com/item/v1"

    @model_validator(mode="after")
    def require_absolute_runtime_paths_for_mutations(self) -> Settings:
        if self.allow_mutations:
            relative = [
                name
                for name, path in (
                    ("database_path", self.database_path),
                    ("profile_directory", self.profile_directory),
                )
                if not path.is_absolute()
            ]
            if relative:
                raise ValueError("mutation mode requires absolute paths: " + ", ".join(relative))
        return self

    def mutations_enabled(self, *, dry_run: bool, confirm: str) -> bool:
        return self.allow_mutations and not dry_run and confirm == "EXECUTE"
