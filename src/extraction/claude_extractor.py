"""Extracción de datos de facturas usando Claude API."""

import json
import logging
import re
import time

import anthropic

from src.config import Settings
from src.extraction.image_optimizer import optimize_for_extraction
from src.extraction.pdf_processor import pdf_to_images
from src.extraction.prompt import SYSTEM_PROMPT, build_extraction_messages
from src.models.invoice import InvoiceData
from src.models.response import ExtractionResponse
from src.stats import StatsCollector
from src.validation.validators import run_all_validations

logger = logging.getLogger(__name__)

# Errores que ameritan retry
_RETRYABLE_ERRORS = (
    anthropic.APITimeoutError,
    anthropic.APIConnectionError,
    anthropic.InternalServerError,
    anthropic.RateLimitError,
)

_MAX_RETRIES = 3
_BASE_DELAY = 1.0  # segundos
_API_TIMEOUT = 60.0  # segundos

# System prompt con cache_control para prompt caching
_CACHED_SYSTEM = [
    {
        "type": "text",
        "text": SYSTEM_PROMPT,
        "cache_control": {"type": "ephemeral"},
    }
]


class ClaudeExtractor:
    """Extrae datos estructurados de facturas usando Claude."""

    def __init__(self, settings: Settings, stats: StatsCollector | None = None) -> None:
        self._client = anthropic.Anthropic(
            api_key=settings.anthropic_api_key,
            timeout=_API_TIMEOUT,
        )
        self._model = settings.claude_model
        self._stats = stats

    def extract(self, file_bytes: bytes, content_type: str) -> ExtractionResponse:
        """Extrae datos de una factura (PDF o imagen) con validación.

        Args:
            file_bytes: Bytes del archivo.
            content_type: MIME type del archivo.

        Returns:
            ExtractionResponse con datos, validación y metadatos.

        Raises:
            ValueError: Si el archivo no se puede procesar o el JSON es inválido.
            anthropic.APIError: Si hay un error no recuperable en la API de Claude.
        """
        start = time.perf_counter()
        num_pages = 1

        # Convertir a imágenes
        if content_type == "application/pdf":
            images = pdf_to_images(file_bytes)
            num_pages = len(images)
        else:
            images = [(file_bytes, content_type)]

        # Optimizar imágenes antes de enviar a Claude
        images = [optimize_for_extraction(img, mt) for img, mt in images]

        # Armar mensajes y llamar a Claude con retry
        messages = build_extraction_messages(images)
        logger.info(
            "Enviando a Claude | modelo=%s | páginas=%d | tamaño=%d bytes",
            self._model, num_pages, len(file_bytes),
        )

        claude_start = time.perf_counter()
        response = self._call_claude_with_retry(messages)
        claude_ms = int((time.perf_counter() - claude_start) * 1000)

        # Logear info de cache
        usage = response.usage
        cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
        cache_creation = getattr(usage, "cache_creation_input_tokens", 0) or 0
        cache_hit = cache_read > 0

        logger.info(
            "Respuesta de Claude en %d ms | input_tokens=%d | cache_read=%d | cache_creation=%d",
            claude_ms, usage.input_tokens, cache_read, cache_creation,
        )

        if self._stats:
            self._stats.record_cache(hit=cache_hit)

        # Extraer y limpiar JSON
        raw_text = response.content[0].text
        logger.debug("Respuesta cruda de Claude: %s", raw_text[:200])
        cleaned = _clean_json_response(raw_text)

        # Parsear JSON
        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError as e:
            logger.error("JSON inválido de Claude: %s", e)
            raise ValueError(f"Claude devolvió JSON inválido: {e}") from e

        # Validar contra el modelo Pydantic
        try:
            invoice = InvoiceData.model_validate(data)
        except Exception as e:
            logger.error("Error de validación Pydantic: %s", e)
            raise ValueError(f"Los datos extraídos no cumplen el esquema: {e}") from e

        # Validaciones cruzadas
        validation = run_all_validations(invoice)
        total_ms = int((time.perf_counter() - start) * 1000)

        logger.info(
            "Extracción completa | factura=%s %s-%s | válida=%s | warnings=%d | total=%d ms",
            invoice.tipo_comprobante, invoice.punto_venta, invoice.numero_comprobante,
            validation.is_valid, len(validation.warnings), total_ms,
        )

        return ExtractionResponse(
            success=True,
            data=invoice,
            validation=validation,
            processing_time_ms=total_ms,
            model_used=self._model,
        )

    def _call_claude_with_retry(self, messages: list[dict]) -> anthropic.types.Message:
        """Llama a Claude API con retry, backoff exponencial y prompt caching."""
        last_error: Exception | None = None

        for attempt in range(1, _MAX_RETRIES + 1):
            try:
                return self._client.messages.create(
                    model=self._model,
                    max_tokens=4096,
                    system=_CACHED_SYSTEM,
                    messages=messages,
                )
            except _RETRYABLE_ERRORS as e:
                last_error = e
                if attempt < _MAX_RETRIES:
                    delay = _BASE_DELAY * (2 ** (attempt - 1))
                    logger.warning(
                        "Error en Claude API (intento %d/%d): %s. Reintentando en %.1fs...",
                        attempt, _MAX_RETRIES, type(e).__name__, delay,
                    )
                    time.sleep(delay)
                else:
                    logger.error(
                        "Error en Claude API tras %d intentos: %s",
                        _MAX_RETRIES, e,
                    )

        raise last_error  # type: ignore[misc]


def _clean_json_response(text: str) -> str:
    """Limpia la respuesta de Claude removiendo backticks, markdown y texto extra."""
    text = text.strip()

    # Remover bloques ```json ... ```
    match = re.search(r"```(?:json)?\s*\n?(.*?)\n?\s*```", text, re.DOTALL)
    if match:
        return match.group(1).strip()

    # Buscar primer { y último } para extraer el JSON
    first_brace = text.find("{")
    last_brace = text.rfind("}")
    if first_brace != -1 and last_brace != -1 and last_brace > first_brace:
        return text[first_brace:last_brace + 1]

    return text
