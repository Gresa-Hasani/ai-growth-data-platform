"""API-specific configuration, separated from database connection settings
(app/db/session.py owns those, unchanged since Phase 1). Environment-driven,
no secrets, no hardcoded credentials - only request-validation defaults.
"""
from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class ApiSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="API_")

    default_page_size: int = 50
    max_page_size: int = 200
    default_date_window_days: int = 30
    max_date_window_days: int = 366


settings = ApiSettings()

# The only currencies this platform's synthetic data generates (see
# scripts/generate_data.py::CURRENCIES) - every currency-filtered endpoint
# validates against this set rather than accepting an arbitrary string.
SUPPORTED_CURRENCIES = ("USD", "EUR", "GBP")
