"""Optimización de imágenes antes de enviar a Claude API."""

import io
import logging

from PIL import Image

logger = logging.getLogger(__name__)

# Máximo recomendado por Anthropic para visión
_MAX_DIMENSION = 1568
_JPEG_QUALITY = 85


def optimize_for_extraction(image_bytes: bytes, media_type: str) -> tuple[bytes, str]:
    """Optimiza una imagen para reducir tokens de input en Claude.

    - Resize proporcional si supera 1568px en cualquier dimensión
    - Convierte PNG sin transparencia a JPEG (más liviano)
    - Calidad JPEG 85

    Args:
        image_bytes: Bytes de la imagen original.
        media_type: MIME type original.

    Returns:
        Tupla (bytes optimizados, media_type actualizado).
    """
    img = Image.open(io.BytesIO(image_bytes))
    original_size = len(image_bytes)
    needs_save = False
    output_format = "JPEG"
    output_media_type = "image/jpeg"

    # Resize si excede el máximo en cualquier dimensión
    if img.width > _MAX_DIMENSION or img.height > _MAX_DIMENSION:
        ratio = min(_MAX_DIMENSION / img.width, _MAX_DIMENSION / img.height)
        new_width = int(img.width * ratio)
        new_height = int(img.height * ratio)
        img = img.resize((new_width, new_height), Image.LANCZOS)
        needs_save = True
        logger.debug(
            "Imagen redimensionada: %dx%d → %dx%d",
            img.width, img.height, new_width, new_height,
        )

    # Convertir PNG a JPEG si no tiene transparencia
    if media_type == "image/png":
        if img.mode in ("RGBA", "LA", "PA"):
            # Verificar si realmente usa transparencia
            if img.mode == "RGBA":
                alpha = img.getchannel("A")
                if alpha.getextrema() == (255, 255):
                    # No usa transparencia, convertir a RGB
                    img = img.convert("RGB")
                    needs_save = True
                else:
                    # Tiene transparencia real, mantener como PNG
                    output_format = "PNG"
                    output_media_type = "image/png"
            else:
                output_format = "PNG"
                output_media_type = "image/png"
        else:
            # Sin canal alpha, convertir a JPEG
            if img.mode != "RGB":
                img = img.convert("RGB")
            needs_save = True
    elif media_type == "image/webp":
        # Convertir webp a JPEG
        if img.mode != "RGB":
            img = img.convert("RGB")
        needs_save = True

    if not needs_save:
        return image_bytes, media_type

    # Guardar imagen optimizada
    buffer = io.BytesIO()
    if output_format == "JPEG":
        if img.mode != "RGB":
            img = img.convert("RGB")
        img.save(buffer, format="JPEG", quality=_JPEG_QUALITY)
    else:
        img.save(buffer, format=output_format)

    optimized = buffer.getvalue()
    logger.debug(
        "Imagen optimizada: %d → %d bytes (%.0f%% reducción)",
        original_size, len(optimized),
        (1 - len(optimized) / original_size) * 100 if original_size > 0 else 0,
    )

    return optimized, output_media_type
