"""Configuración del servicio mediante variables de entorno."""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Configuración cargada desde variables de entorno."""

    anthropic_api_key: str
    api_keys: str = ""
    claude_model: str = "claude-sonnet-4-6"
    max_file_size_mb: int = 10
    log_level: str = "info"
    rate_limit_per_minute: int = 30
    rate_limit_per_hour: int = 500
    batch_max_files: int = 20
    batch_max_concurrent: int = 5

    @property
    def api_keys_list(self) -> list[str]:
        """Devuelve las API keys como lista, filtrando vacíos."""
        if not self.api_keys.strip():
            return []
        return [k.strip() for k in self.api_keys.split(",") if k.strip()]

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}
