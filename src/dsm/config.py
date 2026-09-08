"""Application configuration.

Loads settings from environment variables prefixed with DSM_ or from
a .env file. All values can be overridden at runtime.

Example .env:
    DSM_SENSOR_POLL_INTERVAL=3
    DSM_DB_PATH=/var/lib/dsm/dsm.db
    DSM_ENCRYPTION_KEY=abcdef1234567890abcdef1234567890
"""

from pathlib import Path
from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """DSM application settings.

    All fields have sensible defaults. Override via:
    - Environment variables: DSM_<FIELD_NAME> (uppercase)
    - .env file in the working directory
    """

    model_config = SettingsConfigDict(
        env_prefix="DSM_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Database path — SQLite file location
    db_path: str = "~/.dsm/dsm.db"

    # Web server
    host: str = "0.0.0.0"
    port: int = 8000
    debug: bool = False

    # HTTPS / TLS
    ssl_cert: str = ""
    ssl_key: str = ""
    ssl_self_signed: bool = False

    # Sensor polling interval in seconds (default: 3 seconds for near-real-time monitoring)
    sensor_poll_interval: int = 3

    # Fan control evaluation interval in seconds
    fan_control_interval: int = 10

    # Default temperature ranges for fan control (°C)
    default_cpu_temp_min: float = 45.0
    default_cpu_temp_max: float = 70.0
    default_disk_temp_min: float = 32.0
    default_disk_temp_max: float = 45.0

    # AES-256 encryption key for storing iDRAC passwords at rest
    # Must be 32 hex characters (16 bytes). Change this in production!
    encryption_key: str = "00000000000000000000000000000000"

    # iDRAC defaults
    drac_default_user: str = "root"
    drac_webui_port: int = 443

    # MCP server settings
    mcp_enabled: bool = True
    mcp_port: int = 8101

    @property
    def data_dir(self) -> Path:
        """Parent directory of the database file."""
        db = Path(self.db_path).expanduser()
        return db.parent if db.parent != Path("/") else db.parent

    @property
    def data_dir_exists(self) -> bool:
        """Whether the data directory exists on disk."""
        return self.data_dir.exists()


# Singleton — imported throughout the app
settings = Settings()
