"""FastAPI app para extracción de datos de facturas argentinas."""

import asyncio
import json
import logging
import time

import anthropic
from fastapi import Depends, FastAPI, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from src.auth import get_settings, verify_api_key
from src.config import Settings
from src.extraction.claude_extractor import ClaudeExtractor
from src.models.invoice import InvoiceData
from src.models.response import (
    Base64ExtractionRequest,
    BatchItemResult,
    BatchResponse,
    BatchSummary,
    ExtractionResponse,
)
from src.rate_limiter import check_rate_limit, init_rate_limiter
from src.stats import StatsCollector, StatsResponse
from src.validation.validators import ValidationResult, run_all_validations

# --- Config y logging ---
settings = Settings()


class _JsonFormatter(logging.Formatter):
    """Formateador JSON para logs estructurados en producción."""

    def format(self, record: logging.LogRecord) -> str:
        log_data = {
            "timestamp": self.formatTime(record),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info and record.exc_info[0]:
            log_data["exception"] = self.formatException(record.exc_info)
        return json.dumps(log_data, ensure_ascii=False)


_handler = logging.StreamHandler()
_handler.setFormatter(_JsonFormatter())
logging.root.handlers = [_handler]
logging.root.setLevel(getattr(logging, settings.log_level.upper(), logging.INFO))
logger = logging.getLogger(__name__)

# --- Singletons ---
stats_collector = StatsCollector()
extractor = ClaudeExtractor(settings, stats=stats_collector)
rate_limiter = init_rate_limiter(settings)

# --- App ---
app = FastAPI(
    title="FacturAI",
    description="Extracción de datos de facturas argentinas con Claude API",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

ALLOWED_CONTENT_TYPES = {
    "image/jpeg",
    "image/png",
    "image/webp",
    "application/pdf",
}


# --- Override de settings para auth ---
def _get_settings() -> Settings:
    return settings


app.dependency_overrides[get_settings] = _get_settings


def _error_response(error: str, status_code: int) -> JSONResponse:
    """Genera una respuesta de error consistente."""
    return JSONResponse(
        status_code=status_code,
        content=ExtractionResponse(
            success=False,
            processing_time_ms=0,
            model_used=settings.claude_model,
            error=error,
        ).model_dump(),
    )


# --- Exception handlers globales ---
@app.exception_handler(HTTPException)
async def http_exception_handler(_request: Request, exc: HTTPException) -> JSONResponse:
    """Maneja HTTPExceptions devolviendo formato ExtractionResponse."""
    response = _error_response(str(exc.detail), exc.status_code)
    # Preservar headers custom (ej: Retry-After)
    if hasattr(exc, "headers") and exc.headers:
        for key, value in exc.headers.items():
            response.headers[key] = value
    return response


@app.exception_handler(Exception)
async def generic_exception_handler(_request: Request, exc: Exception) -> JSONResponse:
    """Maneja errores no esperados."""
    logger.error("Error interno no manejado: %s", exc, exc_info=True)
    return _error_response("Error interno del servidor", 500)


# --- Endpoints ---
@app.get("/health")
def health() -> dict:
    """Health check."""
    return {"status": "ok", "model": settings.claude_model, "version": "1.0.0"}


@app.get("/stats", response_model=StatsResponse)
async def get_stats(
    _api_key: str | None = Depends(verify_api_key),
) -> StatsResponse:
    """Estadísticas de uso del servicio."""
    return stats_collector.get_stats(
        rate_limit_per_minute=settings.rate_limit_per_minute,
        rate_limit_per_hour=settings.rate_limit_per_hour,
    )


@app.post("/extract", response_model=ExtractionResponse)
async def extract_invoice(
    file: UploadFile,
    _api_key: str | None = Depends(verify_api_key),
    _rate_limit: None = Depends(check_rate_limit),
) -> ExtractionResponse:
    """Extrae datos estructurados de una factura (PDF o imagen)."""
    # Validar content type
    if file.content_type not in ALLOWED_CONTENT_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"Tipo de archivo no soportado: {file.content_type}. "
            f"Soportados: {', '.join(sorted(ALLOWED_CONTENT_TYPES))}",
        )

    # Leer y validar tamaño
    file_bytes = await file.read()
    max_bytes = settings.max_file_size_mb * 1024 * 1024

    if len(file_bytes) > max_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"Archivo demasiado grande ({len(file_bytes)} bytes). "
            f"Máximo: {settings.max_file_size_mb} MB",
        )

    logger.info(
        "Request recibido | archivo=%s | content_type=%s | tamaño=%d bytes",
        file.filename, file.content_type, len(file_bytes),
    )

    # Extraer datos
    try:
        response = extractor.extract(file_bytes, file.content_type)
        stats_collector.record_extraction(success=True, processing_time_ms=response.processing_time_ms)
        return response
    except ValueError as e:
        stats_collector.record_extraction(success=False, processing_time_ms=0)
        raise HTTPException(status_code=422, detail=str(e))
    except anthropic.RateLimitError:
        stats_collector.record_extraction(success=False, processing_time_ms=0)
        raise HTTPException(status_code=429, detail="Rate limit de Claude API alcanzado. Intentá de nuevo en unos segundos.")
    except anthropic.APITimeoutError:
        stats_collector.record_extraction(success=False, processing_time_ms=0)
        raise HTTPException(status_code=504, detail="Timeout al procesar la factura con Claude")
    except anthropic.APIError as e:
        stats_collector.record_extraction(success=False, processing_time_ms=0)
        logger.error("Error de API de Claude: %s", e)
        raise HTTPException(status_code=502, detail="Error al comunicarse con Claude API")


@app.post("/extract/base64", response_model=ExtractionResponse)
async def extract_invoice_base64(
    request: Base64ExtractionRequest,
    _api_key: str | None = Depends(verify_api_key),
    _rate_limit: None = Depends(check_rate_limit),
) -> ExtractionResponse:
    """Extrae datos de una factura enviada como base64 (ideal para Power Automate)."""
    import base64

    # Validar content type
    if request.content_type not in ALLOWED_CONTENT_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"Tipo de archivo no soportado: {request.content_type}. "
            f"Soportados: {', '.join(sorted(ALLOWED_CONTENT_TYPES))}",
        )

    # Decodificar base64
    try:
        file_bytes = base64.b64decode(request.data)
    except Exception:
        raise HTTPException(status_code=400, detail="El campo 'data' no es base64 válido")

    # Validar tamaño
    max_bytes = settings.max_file_size_mb * 1024 * 1024
    if len(file_bytes) > max_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"Archivo demasiado grande ({len(file_bytes)} bytes). "
            f"Máximo: {settings.max_file_size_mb} MB",
        )

    logger.info(
        "Request base64 recibido | archivo=%s | content_type=%s | tamaño=%d bytes",
        request.filename, request.content_type, len(file_bytes),
    )

    # Extraer datos
    try:
        response = extractor.extract(file_bytes, request.content_type)
        stats_collector.record_extraction(success=True, processing_time_ms=response.processing_time_ms)
        return response
    except ValueError as e:
        stats_collector.record_extraction(success=False, processing_time_ms=0)
        raise HTTPException(status_code=422, detail=str(e))
    except anthropic.RateLimitError:
        stats_collector.record_extraction(success=False, processing_time_ms=0)
        raise HTTPException(status_code=429, detail="Rate limit de Claude API alcanzado. Intentá de nuevo en unos segundos.")
    except anthropic.APITimeoutError:
        stats_collector.record_extraction(success=False, processing_time_ms=0)
        raise HTTPException(status_code=504, detail="Timeout al procesar la factura con Claude")
    except anthropic.APIError as e:
        stats_collector.record_extraction(success=False, processing_time_ms=0)
        logger.error("Error de API de Claude: %s", e)
        raise HTTPException(status_code=502, detail="Error al comunicarse con Claude API")


@app.post("/extract/batch", response_model=BatchResponse)
async def extract_batch(
    files: list[UploadFile],
    _api_key: str | None = Depends(verify_api_key),
    _rate_limit: None = Depends(check_rate_limit),
) -> BatchResponse:
    """Extrae datos de múltiples facturas en paralelo."""
    if not files:
        raise HTTPException(status_code=400, detail="No se enviaron archivos")

    if len(files) > settings.batch_max_files:
        raise HTTPException(
            status_code=400,
            detail=f"Demasiados archivos ({len(files)}). Máximo: {settings.batch_max_files}",
        )

    batch_start = time.perf_counter()
    max_bytes = settings.max_file_size_mb * 1024 * 1024
    semaphore = asyncio.Semaphore(settings.batch_max_concurrent)

    # Pre-leer y validar todos los archivos
    file_data: list[tuple[str, bytes, str]] = []
    for f in files:
        if f.content_type not in ALLOWED_CONTENT_TYPES:
            file_data.append((f.filename or "unknown", b"", f"Tipo no soportado: {f.content_type}"))
            continue
        data = await f.read()
        if len(data) > max_bytes:
            file_data.append((f.filename or "unknown", b"", f"Archivo demasiado grande ({len(data)} bytes)"))
            continue
        file_data.append((f.filename or "unknown", data, f.content_type or ""))

    async def process_one(filename: str, file_bytes: bytes, content_type: str) -> BatchItemResult:
        """Procesa una factura individual dentro del batch."""
        # Si ya tiene error de validación previa
        if not file_bytes and content_type.startswith("Tipo no soportado"):
            return BatchItemResult(filename=filename, status="error", processing_time_ms=0, error=content_type)
        if not file_bytes:
            return BatchItemResult(filename=filename, status="error", processing_time_ms=0, error=content_type)

        async with semaphore:
            try:
                loop = asyncio.get_event_loop()
                response = await loop.run_in_executor(None, extractor.extract, file_bytes, content_type)
                stats_collector.record_extraction(success=True, processing_time_ms=response.processing_time_ms)
                return BatchItemResult(
                    filename=filename,
                    status="ok",
                    data=response.data,
                    validation=response.validation,
                    processing_time_ms=response.processing_time_ms,
                )
            except Exception as e:
                stats_collector.record_extraction(success=False, processing_time_ms=0)
                logger.error("Error procesando %s en batch: %s", filename, e)
                return BatchItemResult(
                    filename=filename,
                    status="error",
                    processing_time_ms=0,
                    error=str(e),
                )

    # Procesar en paralelo
    tasks = [process_one(fn, fb, ct) for fn, fb, ct in file_data]
    results = await asyncio.gather(*tasks)

    total_ms = int((time.perf_counter() - batch_start) * 1000)
    ok_count = sum(1 for r in results if r.status == "ok")
    error_count = sum(1 for r in results if r.status == "error")

    logger.info(
        "Batch completo | total=%d | ok=%d | errors=%d | tiempo=%d ms",
        len(results), ok_count, error_count, total_ms,
    )

    return BatchResponse(
        results=list(results),
        summary=BatchSummary(
            total=len(results),
            ok=ok_count,
            errors=error_count,
            total_processing_time_ms=total_ms,
        ),
    )


@app.post("/extract/validate-only", response_model=ValidationResult)
async def validate_invoice(
    data: InvoiceData,
    _api_key: str | None = Depends(verify_api_key),
) -> ValidationResult:
    """Valida datos de una factura ya extraída sin llamar a Claude."""
    return run_all_validations(data)
