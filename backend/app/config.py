"""Central application configuration (env driven)."""
import os
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

# Load .env BEFORE reading any variable (avoids stale/missing config bugs)
ROOT_DIR = Path(__file__).resolve().parent.parent
load_dotenv(ROOT_DIR / ".env")


class Settings:
    # --- Database -------------------------------------------------------
    DATABASE_URL: str = os.environ.get(
        "DATABASE_URL", "postgresql+psycopg2://postgres:aiia_ctms_pg@127.0.0.1:5432/aiia_ctms"
    )

    # --- Auth -----------------------------------------------------------
    JWT_SECRET: str = os.environ.get("JWT_SECRET", "aiia-ctms-dev-secret-change-me")
    JWT_ALGORITHM: str = os.environ.get("JWT_ALGORITHM", "HS256")
    ACCESS_TOKEN_EXPIRE_MINUTES: int = int(os.environ.get("ACCESS_TOKEN_EXPIRE_MINUTES", "720"))

    # --- Audit anchoring ------------------------------------------------
    # LOCAL         -> merkle root stored in DB only (works out of the box)
    # POLYGON_AMOY  -> merkle root committed on-chain via web3.py
    ANCHOR_MODE: str = os.environ.get("ANCHOR_MODE", "LOCAL").upper()
    ANCHOR_BATCH_SIZE: int = int(os.environ.get("ANCHOR_BATCH_SIZE", "50"))
    POLYGON_RPC_URL: str = os.environ.get("POLYGON_RPC_URL", "")
    POLYGON_PRIVATE_KEY: str = os.environ.get("POLYGON_PRIVATE_KEY", "")
    POLYGON_CONTRACT_ADDRESS: str = os.environ.get("POLYGON_CONTRACT_ADDRESS", "")
    POLYGON_CHAIN_ID: int = int(os.environ.get("POLYGON_CHAIN_ID", "80002"))

    # --- Demo / hackathon switches --------------------------------------
    ALLOW_TAMPER_SIM: bool = os.environ.get("ALLOW_TAMPER_SIM", "true").lower() == "true"
    ENABLE_SCHEDULER: bool = os.environ.get("ENABLE_SCHEDULER", "true").lower() == "true"

    # --- Misc -----------------------------------------------------------
    CORS_ORIGINS: str = os.environ.get("CORS_ORIGINS", "*")
    FHIR_BASE_URL: str = os.environ.get("FHIR_BASE_URL", "/api/fhir")
    APP_NAME: str = "AIIA CTMS Backend"
    APP_VERSION: str = "1.0.0"

    # Regulatory deadline windows (hours)
    SAE_FATAL_WINDOW_HOURS: int = 24
    SAE_FATAL_FOLLOWUP_DAYS: int = 14
    SAE_OTHER_WINDOW_DAYS: int = 15
    NON_SERIOUS_WINDOW_DAYS: int = 30


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
