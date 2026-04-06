"""Autenticación por API Key."""

from fastapi import Depends, HTTPException, Security
from fastapi.security import APIKeyHeader

from src.config import Settings

_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def get_settings() -> Settings:
    """Devuelve la instancia de Settings (se sobreescribe en main)."""
    return Settings()


def verify_api_key(
    api_key: str | None = Security(_api_key_header),
    settings: Settings = Depends(get_settings),
) -> str | None:
    """Valida el API key del header X-API-Key contra las keys configuradas."""
    allowed_keys = settings.api_keys_list

    # Dev mode: si no hay keys configuradas, auth deshabilitada
    if not allowed_keys:
        return None

    if not api_key:
        raise HTTPException(status_code=401, detail="Falta el header X-API-Key")

    if api_key not in allowed_keys:
        raise HTTPException(status_code=401, detail="API key inválida")

    return api_key
