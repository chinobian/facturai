# FacturAI

## Qué es
Servicio FastAPI (API REST) para extracción de datos de facturas argentinas
usando Claude API (Sonnet 4.6). Reemplazo de AI Builder de Microsoft.
Deploy en Railway con Docker.

## Stack
- Python 3.12 / FastAPI / Uvicorn
- Anthropic SDK (Claude Sonnet 4.6)
- Pydantic v2 para modelos y validación
- pdf2image + poppler para PDFs
- Docker para deploy

## Cómo correr local
```bash
cp .env.example .env  # completar ANTHROPIC_API_KEY
pip install -e .
uvicorn src.main:app --reload --port 8000
```

## Cómo correr con Docker
```bash
docker build -t facturai .
docker run -p 8000:8000 --env-file .env facturai
```

## Estructura
- src/main.py — FastAPI app + endpoints
- src/config.py — Settings (pydantic-settings)
- src/auth.py — Autenticación por API Key
- src/rate_limiter.py — Rate limiting por API key (sliding window in-memory)
- src/stats.py — Recolección de métricas de uso
- src/models/invoice.py — Modelos Pydantic de factura
- src/models/response.py — ExtractionResponse, BatchResponse, ValidationResult
- src/extraction/claude_extractor.py — Lógica de extracción con retry y prompt caching
- src/extraction/prompt.py — System prompt con few-shot examples y message builders
- src/extraction/pdf_processor.py — PDF → imágenes (con optimización)
- src/extraction/image_optimizer.py — Optimización de imágenes para Claude API
- src/validation/validators.py — Validaciones cruzadas de datos

## Convenciones
- Código en inglés, logs/comentarios en español argentino
- Type hints estrictos en todo
- Async/await para I/O
- Números como float, fechas como "dd/mm/aaaa"
- CUIT con guiones: XX-XXXXXXXX-X
- Punto de venta y número de comprobante con ceros a la izquierda

## Endpoints
- POST /extract — Extrae datos de una factura (requiere X-API-Key). Devuelve ExtractionResponse
- POST /extract/batch — Extrae datos de múltiples facturas en paralelo. Devuelve BatchResponse
- POST /extract/validate-only — Valida un JSON de InvoiceData sin llamar a Claude
- GET /stats — Estadísticas de uso del servicio (requiere X-API-Key)
- GET /health — Health check
- GET /docs — Swagger UI automática

## Respuesta de /extract (ExtractionResponse)
- success: bool
- data: InvoiceData | null
- validation: {is_valid, warnings[], errors[]}
- processing_time_ms: int
- model_used: string
- error: string | null

## Batch (/extract/batch)
- Recibe múltiples archivos via multipart/form-data (campo "files")
- Máximo 20 archivos por request (configurable: BATCH_MAX_FILES)
- Máximo 5 concurrentes a Claude API (configurable: BATCH_MAX_CONCURRENT)
- Cada factura se procesa independientemente — si una falla, las demás continúan
- Ejemplo curl:
```bash
curl -X POST https://facturai.up.railway.app/extract/batch \
    -H "X-API-Key: fai_xxx" \
    -F "files=@factura1.pdf" \
    -F "files=@factura2.jpg" \
    -F "files=@factura3.png"
```

## Rate Limiting
- Sliding window in-memory por API key
- Default: 30 req/min, 500 req/hora (configurable via env vars)
- Respuesta 429 con header Retry-After
- Se resetea al reiniciar el servicio (in-memory)
- Sin API keys configuradas (dev mode): rate limiting deshabilitado

## Prompt Caching
- El system prompt usa cache_control ephemeral de Anthropic
- El prompt incluye few-shot examples para superar el mínimo de 1024 tokens
- Se logea cache_read_input_tokens y cache_creation_input_tokens
- Ahorro estimado: ~90% en tokens de system prompt después del primer request

## Estadísticas (/stats)
- Métricas in-memory (se pierden al reiniciar)
- Incluye: uptime, requests totales, ok/error, promedio de tiempo, cache hit rate
- Requiere autenticación

## Validaciones cruzadas
Las validaciones NO rechazan la extracción, agregan warnings:
- Consistencia de totales vs suma de ítems (tolerancia ±1.00)
- Formato y dígito verificador de CUIT (módulo 11)
- Formato y coherencia de fechas (dd/mm/aaaa, no futuras)
- CAE con 14 dígitos numéricos

## Retry y resiliencia
- Claude API: retry hasta 3 veces con backoff exponencial (1s, 2s, 4s)
- Retryable: timeout, connection, internal server, rate limit
- No retryable: auth, bad request
- Timeout por request: 60 segundos
- PDF: máximo 10 páginas, DPI 200, imágenes max 2000px ancho

## Optimización de imágenes
- Resize a máximo 1568px (recomendación Anthropic para visión)
- PNG sin transparencia se convierte a JPEG
- WebP se convierte a JPEG
- Calidad JPEG: 85

## Variables de entorno
- ANTHROPIC_API_KEY — API key de Anthropic (requerida)
- API_KEYS — API keys permitidas, comma-separated (vacío = dev mode sin auth)
- CLAUDE_MODEL — Modelo de Claude (default: claude-sonnet-4-6)
- MAX_FILE_SIZE_MB — Tamaño máximo de archivo en MB (default: 10)
- LOG_LEVEL — Nivel de logging: debug, info, warning, error (default: info)
- RATE_LIMIT_PER_MINUTE — Requests por minuto por API key (default: 30)
- RATE_LIMIT_PER_HOUR — Requests por hora por API key (default: 500)
- BATCH_MAX_FILES — Máximo de archivos por batch (default: 20)
- BATCH_MAX_CONCURRENT — Máximo de extracciones concurrentes en batch (default: 5)

## Logging
- Formato JSON estructurado (para Railway/producción)
- Nivel configurable via LOG_LEVEL env var
- Se logea: archivo, content_type, tamaño, páginas, duración, resultado validación, cache info
- NO se logea: contenido del archivo ni respuesta completa de Claude
