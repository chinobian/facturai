"""Modelos Pydantic para facturas argentinas."""

from typing import Literal, Optional

from pydantic import BaseModel, Field


class Emisor(BaseModel):
    """Datos del emisor de la factura."""

    razon_social: Optional[str] = None
    cuit: Optional[str] = None
    condicion_iva: Optional[str] = None
    domicilio: Optional[str] = None
    ingresos_brutos: Optional[str] = None
    inicio_actividades: Optional[str] = None


class Receptor(BaseModel):
    """Datos del receptor de la factura."""

    razon_social: Optional[str] = None
    cuit: Optional[str] = None
    condicion_iva: Optional[str] = None
    domicilio: Optional[str] = None


class InvoiceItem(BaseModel):
    """Línea individual del detalle de la factura."""

    codigo: Optional[str] = None
    descripcion: str
    cantidad: float
    unidad: str
    precio_unitario: float
    bonificacion: float = 0
    subtotal: float
    alicuota_iva: float
    iva: float


class Totales(BaseModel):
    """Totales de la factura."""

    neto_gravado: float = 0
    no_gravado: float = 0
    exento: float = 0
    iva_10_5: float = 0
    iva_21: float = 0
    iva_27: float = 0
    otros_tributos: float = 0
    percepciones: float = 0
    iibb: float = 0
    total: float = 0


class ExtractionMeta(BaseModel):
    """Metadatos de la extracción."""

    confianza: Literal["alta", "media", "baja"]
    campos_dudosos: list[str] = Field(default_factory=list)
    notas: str = ""


class InvoiceData(BaseModel):
    """Datos completos de una factura argentina."""

    model_config = {"populate_by_name": True, "serialize_by_alias": True}

    tipo_comprobante: Optional[str] = None
    punto_venta: Optional[str] = None
    numero_comprobante: Optional[str] = None
    fecha_emision: Optional[str] = None
    fecha_vencimiento: Optional[str] = None
    condicion_venta: Optional[str] = None
    emisor: Emisor = Field(default_factory=Emisor)
    receptor: Receptor = Field(default_factory=Receptor)
    items: list[InvoiceItem] = Field(default_factory=list)
    totales: Totales = Field(default_factory=Totales)
    cae: Optional[str] = None
    cae_vencimiento: Optional[str] = None
    meta: ExtractionMeta = Field(alias="_meta", default_factory=lambda: ExtractionMeta(confianza="baja"))
