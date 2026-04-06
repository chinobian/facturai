"""Conversión de PDF a imágenes para enviar a Claude."""

import io
import logging

from pdf2image import convert_from_bytes
from pdf2image.exceptions import PDFPageCountError, PDFSyntaxError
from PIL import Image

logger = logging.getLogger(__name__)

_MAX_PAGES = 10
_MAX_WIDTH = 2000
_MAX_IMAGE_BYTES = 5 * 1024 * 1024  # 5MB
_DPI = 200


def pdf_to_images(pdf_bytes: bytes) -> list[tuple[bytes, str]]:
    """Convierte un PDF a lista de imágenes JPEG.

    Args:
        pdf_bytes: Bytes del archivo PDF.

    Returns:
        Lista de tuplas (image_bytes, media_type) por cada página.

    Raises:
        ValueError: Si el PDF está corrupto, protegido o tiene demasiadas páginas.
    """
    try:
        pages = convert_from_bytes(pdf_bytes, dpi=_DPI, fmt="jpeg")
    except PDFSyntaxError as e:
        raise ValueError(f"El PDF está corrupto o tiene un formato inválido: {e}") from e
    except PDFPageCountError as e:
        raise ValueError(f"No se pudo determinar la cantidad de páginas del PDF: {e}") from e
    except Exception as e:
        error_msg = str(e).lower()
        if "password" in error_msg or "encrypted" in error_msg:
            raise ValueError("El PDF está protegido con contraseña") from e
        raise ValueError(f"No se pudo procesar el PDF: {e}") from e

    if not pages:
        raise ValueError("El PDF no contiene páginas")

    if len(pages) > _MAX_PAGES:
        raise ValueError(
            f"El PDF tiene {len(pages)} páginas (máximo permitido: {_MAX_PAGES}). "
            "Las facturas no deberían tener más de 10 páginas."
        )

    images: list[tuple[bytes, str]] = []
    for i, page in enumerate(pages):
        # Resize si excede el ancho máximo
        if page.width > _MAX_WIDTH:
            ratio = _MAX_WIDTH / page.width
            new_height = int(page.height * ratio)
            page = page.resize((_MAX_WIDTH, new_height), Image.LANCZOS)

        # Convertir a JPEG con calidad inicial
        quality = 85
        image_bytes = _page_to_jpeg(page, quality)

        # Si excede 5MB, reducir calidad
        if len(image_bytes) > _MAX_IMAGE_BYTES:
            quality = 70
            image_bytes = _page_to_jpeg(page, quality)
            logger.debug("Página %d: calidad reducida a %d por tamaño", i + 1, quality)

        images.append((image_bytes, "image/jpeg"))
        logger.debug("Página %d convertida: %d bytes (quality=%d)", i + 1, len(image_bytes), quality)

    logger.info("PDF convertido: %d página(s)", len(images))
    return images


def _page_to_jpeg(page: Image.Image, quality: int) -> bytes:
    """Convierte una página PIL a bytes JPEG."""
    buffer = io.BytesIO()
    page.save(buffer, format="JPEG", quality=quality)
    return buffer.getvalue()
