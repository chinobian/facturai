"""Rate limiting por API key con sliding window in-memory."""

import logging
import time
import threading

from fastapi import Depends, HTTPException, Response

from src.auth import verify_api_key
from src.config import Settings

logger = logging.getLogger(__name__)


class RateLimiter:
    """Rate limiter in-memory basado en sliding window por API key."""

    def __init__(self, per_minute: int, per_hour: int) -> None:
        self._per_minute = per_minute
        self._per_hour = per_hour
        self._requests: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def check(self, api_key: str | None) -> int | None:
        """Verifica si el request está dentro de los límites.

        Args:
            api_key: La API key del cliente. None = sin rate limit (dev mode).

        Returns:
            None si está permitido, o la cantidad de segundos para retry.
        """
        if api_key is None:
            return None

        now = time.time()
        window_hour = now - 3600
        window_minute = now - 60

        with self._lock:
            timestamps = self._requests.get(api_key, [])

            # Limpiar entries más viejas que 1 hora
            timestamps = [t for t in timestamps if t > window_hour]
            self._requests[api_key] = timestamps

            # Chequear límite por hora
            if len(timestamps) >= self._per_hour:
                oldest_in_hour = timestamps[0]
                retry_after = int(oldest_in_hour - window_hour) + 1
                return max(retry_after, 1)

            # Chequear límite por minuto
            recent = [t for t in timestamps if t > window_minute]
            if len(recent) >= self._per_minute:
                oldest_in_minute = recent[0]
                retry_after = int(oldest_in_minute - window_minute) + 1
                return max(retry_after, 1)

            # Permitido: registrar timestamp
            timestamps.append(now)
            return None

    @property
    def per_minute(self) -> int:
        return self._per_minute

    @property
    def per_hour(self) -> int:
        return self._per_hour

    def cleanup(self) -> None:
        """Limpia entries viejas de todas las keys. Llamar periódicamente."""
        cutoff = time.time() - 3600
        with self._lock:
            empty_keys = []
            for key, timestamps in self._requests.items():
                self._requests[key] = [t for t in timestamps if t > cutoff]
                if not self._requests[key]:
                    empty_keys.append(key)
            for key in empty_keys:
                del self._requests[key]


# Singleton — se inicializa desde main.py
_limiter: RateLimiter | None = None


def init_rate_limiter(settings: Settings) -> RateLimiter:
    """Inicializa el rate limiter global."""
    global _limiter
    _limiter = RateLimiter(
        per_minute=settings.rate_limit_per_minute,
        per_hour=settings.rate_limit_per_hour,
    )
    return _limiter


def get_rate_limiter() -> RateLimiter:
    """Devuelve el rate limiter global."""
    assert _limiter is not None, "Rate limiter no inicializado"
    return _limiter


def check_rate_limit(
    api_key: str | None = Depends(verify_api_key),
    limiter: RateLimiter = Depends(get_rate_limiter),
) -> None:
    """Dependency de FastAPI que verifica rate limits."""
    retry_after = limiter.check(api_key)
    if retry_after is not None:
        raise HTTPException(
            status_code=429,
            detail=f"Rate limit excedido. Máximo {limiter.per_minute} requests por minuto.",
            headers={"Retry-After": str(retry_after)},
        )
