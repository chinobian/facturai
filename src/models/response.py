"""Modelos de respuesta enriquecidos para los endpoints."""

from pydantic import BaseModel

from src.models.invoice import InvoiceData
from src.validation.validators import ValidationResult


class ExtractionResponse(BaseModel):
    """Respuesta del endpoint /extract con validación y metadatos."""

    success: bool
    data: InvoiceData | None = None
    validation: ValidationResult | None = None
    processing_time_ms: int
    model_used: str
    error: str | None = None


class BatchItemResult(BaseModel):
    """Resultado de una factura individual dentro de un batch."""

    filename: str
    status: str  # "ok" | "error"
    data: InvoiceData | None = None
    validation: ValidationResult | None = None
    processing_time_ms: int
    error: str | None = None


class BatchSummary(BaseModel):
    """Resumen del procesamiento batch."""

    total: int
    ok: int
    errors: int
    total_processing_time_ms: int


class BatchResponse(BaseModel):
    """Respuesta del endpoint /extract/batch."""

    results: list[BatchItemResult]
    summary: BatchSummary
