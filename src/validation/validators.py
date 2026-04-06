"""Validaciones cruzadas de datos extraídos de facturas."""

import re
from datetime import datetime

from pydantic import BaseModel

from src.models.invoice import InvoiceData

_TOLERANCE = 1.00
_CUIT_PATTERN = re.compile(r"^\d{2}-\d{8}-\d$")
_CUIT_MULTIPLIERS = [5, 4, 3, 2, 7, 6, 5, 4, 3, 2]
_DATE_PATTERN = re.compile(r"^\d{2}/\d{2}/\d{4}$")


class ValidationResult(BaseModel):
    """Resultado de las validaciones cruzadas."""

    is_valid: bool
    warnings: list[str] = []
    errors: list[str] = []


def validate_totals(data: InvoiceData) -> list[str]:
    """Verifica consistencia entre ítems y totales."""
    warnings: list[str] = []
    t = data.totales

    if data.items:
        sum_subtotales = sum(item.subtotal for item in data.items)
        if t.neto_gravado and abs(sum_subtotales - t.neto_gravado) > _TOLERANCE:
            warnings.append(
                f"Suma de subtotales de ítems ({sum_subtotales:.2f}) "
                f"no coincide con neto_gravado ({t.neto_gravado:.2f})"
            )

        sum_iva = sum(item.iva for item in data.items)
        total_iva = t.iva_21 + t.iva_10_5 + t.iva_27
        if total_iva and abs(sum_iva - total_iva) > _TOLERANCE:
            warnings.append(
                f"Suma de IVA de ítems ({sum_iva:.2f}) "
                f"no coincide con IVA en totales ({total_iva:.2f})"
            )

    # Verificar suma total
    computed_total = (
        t.neto_gravado + t.no_gravado + t.exento
        + t.iva_21 + t.iva_10_5 + t.iva_27
        + t.otros_tributos + t.percepciones + t.iibb
    )
    if t.total and abs(computed_total - t.total) > _TOLERANCE:
        warnings.append(
            f"Suma de componentes ({computed_total:.2f}) "
            f"no coincide con total ({t.total:.2f})"
        )

    return warnings


def validate_cuit(cuit: str | None) -> bool:
    """Verifica formato y dígito verificador de CUIT argentino (módulo 11)."""
    if not cuit:
        return True  # None es válido (campo opcional)

    if not _CUIT_PATTERN.match(cuit):
        return False

    digits = cuit.replace("-", "")
    checksum = sum(int(d) * m for d, m in zip(digits[:10], _CUIT_MULTIPLIERS))
    remainder = 11 - (checksum % 11)
    if remainder == 11:
        expected = 0
    elif remainder == 10:
        expected = 9
    else:
        expected = remainder

    return int(digits[10]) == expected


def validate_dates(data: InvoiceData) -> list[str]:
    """Verifica formato y coherencia de fechas."""
    warnings: list[str] = []
    now = datetime.now()

    fecha_emision_dt = _parse_date(data.fecha_emision)
    if data.fecha_emision and not fecha_emision_dt:
        warnings.append(f"fecha_emision con formato inválido: {data.fecha_emision}")

    if fecha_emision_dt and fecha_emision_dt > now:
        warnings.append(f"fecha_emision es futura: {data.fecha_emision}")

    if data.fecha_vencimiento and not _parse_date(data.fecha_vencimiento):
        warnings.append(f"fecha_vencimiento con formato inválido: {data.fecha_vencimiento}")

    cae_venc_dt = _parse_date(data.cae_vencimiento)
    if data.cae_vencimiento and not cae_venc_dt:
        warnings.append(f"cae_vencimiento con formato inválido: {data.cae_vencimiento}")

    if fecha_emision_dt and cae_venc_dt and cae_venc_dt < fecha_emision_dt:
        warnings.append(
            f"cae_vencimiento ({data.cae_vencimiento}) "
            f"es anterior a fecha_emision ({data.fecha_emision})"
        )

    return warnings


def validate_cae(cae: str | None) -> list[str]:
    """Verifica que el CAE tenga 14 dígitos numéricos."""
    if not cae:
        return []
    if not re.match(r"^\d{14}$", cae):
        return [f"CAE debe tener 14 dígitos numéricos, recibido: '{cae}'"]
    return []


def run_all_validations(data: InvoiceData) -> ValidationResult:
    """Ejecuta todas las validaciones y retorna el resultado consolidado."""
    warnings: list[str] = []
    errors: list[str] = []

    # Totales
    warnings.extend(validate_totals(data))

    # CUITs
    if not validate_cuit(data.emisor.cuit):
        warnings.append(f"CUIT del emisor inválido: {data.emisor.cuit}")
    if not validate_cuit(data.receptor.cuit):
        warnings.append(f"CUIT del receptor inválido: {data.receptor.cuit}")

    # Fechas
    warnings.extend(validate_dates(data))

    # CAE
    warnings.extend(validate_cae(data.cae))

    return ValidationResult(
        is_valid=len(errors) == 0,
        warnings=warnings,
        errors=errors,
    )


def _parse_date(date_str: str | None) -> datetime | None:
    """Parsea una fecha en formato dd/mm/aaaa."""
    if not date_str or not _DATE_PATTERN.match(date_str):
        return None
    try:
        return datetime.strptime(date_str, "%d/%m/%Y")
    except ValueError:
        return None
