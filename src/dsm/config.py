"""Application configuration.

Loads settings from environment variables prefixed with DSM_ or from
a .env file. All values can be overridden at runtime.

Example .env:
    DSM_SENSOR_POLL_INTERVAL=3
    DSM_DB_PATH=/var/lib/dsm/dsm.db
    DSM_ENCRYPTION_KEY=abcdef1234567890abcdef1234567890
"""

from pathlib import Path

from pydantic import Field
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
        allow_inf_nan=False,
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
    sensor_poll_interval: int = Field(default=3, ge=1, le=3600)

    # Fan control evaluation interval in seconds
    fan_control_interval: int = Field(default=10, ge=1, le=3600)

    # Automatic fan control tuning.  These are deliberately conservative at
    # steady state, but allow a bounded fast response when a CPU is heating.
    fan_control_rise_gain_percent_per_c: float = Field(default=3.0, ge=0.0, le=100.0)
    fan_control_rise_rate_gain_percent_per_c_per_sec: float = Field(default=12.0, ge=0.0, le=100.0)
    fan_control_fall_gain_percent_per_c: float = Field(default=1.0, ge=0.0, le=100.0)
    fan_control_rise_activation_margin_c: float = Field(default=3.0, ge=0.0, le=100.0)
    fan_control_temp_deadband_c: float = Field(default=1.0, ge=0.0, le=100.0)
    fan_control_rate_deadband_c_per_sec: float = Field(default=0.05, ge=0.0, le=100.0)
    fan_control_max_rise_step_percent: int = Field(default=12, ge=0, le=100)
    fan_control_max_fall_step_percent: int = Field(default=3, ge=0, le=100)
    fan_control_min_command_interval_seconds: int = Field(default=15, ge=0, le=3600)
    # Bypass the normal command dwell only when CPU heat is clearly dangerous:
    # either this far above its configured maximum, or rising this quickly.
    # These deliberately conservative values still let a hot chassis respond
    # before a short polling dwell delays the next bounded rise.
    fan_control_emergency_cpu_overtemp_c: float = Field(default=10.0, ge=0.0, le=100.0)
    fan_control_emergency_cpu_rate_c_per_sec: float = Field(default=1.0, ge=0.0, le=100.0)
    # Ignore a derivative after a polling outage rather than treating a large
    # elapsed interval as a weak but current thermal trend.
    fan_control_max_temperature_sample_gap_seconds: int = Field(default=120, ge=1, le=86400)
    fan_control_idrac7_refresh_interval_seconds: int = Field(default=60, ge=1, le=86400)

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
