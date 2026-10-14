from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    bot_token: str = ""
    database_url: str = "postgresql+asyncpg://dkmarket:change_me@db:5432/dkmarket"
    admin_ids: str = ""
    store_name: str = "DKMARKET"
    support_contact: str = "@support"
    rules_text: str = "Перед оформлением проверьте название, регион и цену выбранного товара."
    legal_document_date: str = "12 октября 2026 года"
    legal_operator_name: str = ""
    legal_contact_email: str = ""
    resellcodes_api_key: str = ""
    usd_to_rub_minor: int | None = None
    steam_gift_limit: int = 100
    import_steam_keys: bool = False
    gift_card_queries: str = "Steam,PlayStation,Xbox,Nintendo,Roblox,App Store,Google Play,Spotify"
    gift_card_prefixes: str = "steam_,playstation_,xbox_,nintendo_,roblox_,app_store_itunes_,google_play_,spotify_"
    gift_card_regions: str = "RU,GLOBAL,CIS"
    steam_gift_regions: str = "RU,CIS"

    @field_validator("usd_to_rub_minor", mode="before")
    @classmethod
    def empty_rate_is_none(cls, value):
        return None if value in ("", None) else value

    @property
    def admins(self) -> set[int]:
        return {int(item) for item in self.admin_ids.split(",") if item.strip().isdigit()}

    @property
    def gift_card_search_queries(self) -> tuple[str, ...]:
        return tuple(item.strip() for item in self.gift_card_queries.split(",") if item.strip())

    @property
    def gift_card_id_prefixes(self) -> tuple[str, ...]:
        return tuple(item.strip().casefold() for item in self.gift_card_prefixes.split(",") if item.strip())

    @property
    def gift_card_region_suffixes(self) -> tuple[str, ...]:
        return tuple("_" + item.strip().casefold() for item in self.gift_card_regions.split(",") if item.strip())

    @property
    def steam_gift_region_codes(self) -> set[str]:
        return {item.strip().upper() for item in self.steam_gift_regions.split(",") if item.strip()}

    @property
    def has_public_support_contact(self) -> bool:
        return bool(self.support_contact.strip() and self.support_contact.strip() not in {"@support", "@your_support"})


@lru_cache
def get_settings() -> Settings:
    return Settings()
