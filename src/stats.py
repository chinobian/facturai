"""Recolección de métricas de uso in-memory."""

import threading
import time
from collections import deque

from pydantic import BaseModel


class StatsResponse(BaseModel):
    """Respuesta del endpoint /stats."""

    uptime_seconds: int
    total_requests: int
    total_extractions_ok: int
    total_extractions_error: int
    requests_last_hour: int
    avg_processing_time_ms: int
    cache_hit_rate: float
    rate_limits: dict[str, int]


class StatsCollector:
    """Recolecta métricas de uso del servicio. Thread-safe."""

    def __init__(self) -> None:
        self._start_time = time.time()
        self._lock = threading.Lock()
        self._total_requests = 0
        self._ok = 0
        self._errors = 0
        self._processing_times: deque[tuple[float, int]] = deque(maxlen=1000)  # (timestamp, ms)
        self._cache_hits = 0
        self._cache_misses = 0

    def record_extraction(self, *, success: bool, processing_time_ms: int) -> None:
        """Registra una extracción completada."""
        with self._lock:
            self._total_requests += 1
            if success:
                self._ok += 1
            else:
                self._errors += 1
            self._processing_times.append((time.time(), processing_time_ms))

    def record_cache(self, *, hit: bool) -> None:
        """Registra un hit o miss de prompt cache."""
        with self._lock:
            if hit:
                self._cache_hits += 1
            else:
                self._cache_misses += 1

    def get_stats(self, rate_limit_per_minute: int, rate_limit_per_hour: int) -> StatsResponse:
        """Genera las estadísticas actuales."""
        now = time.time()
        hour_ago = now - 3600

        with self._lock:
            requests_last_hour = sum(
                1 for ts, _ in self._processing_times if ts > hour_ago
            )
            avg_ms = 0
            if self._processing_times:
                avg_ms = int(
                    sum(ms for _, ms in self._processing_times) / len(self._processing_times)
                )
            total_cache = self._cache_hits + self._cache_misses
            cache_hit_rate = self._cache_hits / total_cache if total_cache > 0 else 0.0

            return StatsResponse(
                uptime_seconds=int(now - self._start_time),
                total_requests=self._total_requests,
                total_extractions_ok=self._ok,
                total_extractions_error=self._errors,
                requests_last_hour=requests_last_hour,
                avg_processing_time_ms=avg_ms,
                cache_hit_rate=round(cache_hit_rate, 2),
                rate_limits={
                    "per_minute": rate_limit_per_minute,
                    "per_hour": rate_limit_per_hour,
                },
            )
