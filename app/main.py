import logging
import sys

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.core.config import ConfigurationError, get_settings

logger = logging.getLogger(__name__)

# Settings validation is where every "unsafe to serve traffic" rule lives
# (missing/placeholder JWT secret, wildcard CORS in production, SQLite in
# production...). Failing here, loudly, is the entire point: a misconfigured
# money API must not boot and quietly accept forgeable tokens.
try:
    settings = get_settings()
except ConfigurationError as exc:
    print(f"FATAL: refusing to start — {exc}", file=sys.stderr)
    raise SystemExit(1) from exc

from app.core.middleware import MaxBodySizeMiddleware, SecurityHeadersMiddleware  # noqa: E402
from app.routers import (  # noqa: E402
    admin,
    auth,
    card_to_bank,
    funding,
    kyc,
    limits,
    operations,
    requests_inbox,
    support,
    transactions,
    users,
    wallet,
)

app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    # Never expose the interactive docs in production — they reveal the full
    # API surface and can be used to probe endpoints without any tooling.
    docs_url="/docs" if settings.docs_enabled else None,
    redoc_url="/redoc" if settings.docs_enabled else None,
    openapi_url="/openapi.json" if settings.docs_enabled else None,
)

# Middleware order: the LAST one added is the outermost. We want security
# headers stamped on every response (including rejections from the layers
# below), then host validation, then CORS, then the body-size guard closest
# to the application.
app.add_middleware(MaxBodySizeMiddleware, max_bytes=settings.max_request_body_mb * 1024 * 1024)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    # Enumerate only the verbs and headers the frontend actually sends —
    # * would accept anything, including headers injected by attackers.
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "Idempotency-Key", "X-Request-ID"],
    max_age=600,
)

if "*" not in settings.trusted_hosts:
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.trusted_hosts)

app.add_middleware(SecurityHeadersMiddleware, settings=settings)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    field_errors: dict[str, str] = {}
    for error in exc.errors():
        loc = [str(part) for part in error["loc"] if part not in ("body", "query")]
        field_errors[".".join(loc) or "value"] = error["msg"]
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={"message": "Please check the highlighted fields and try again.", "fieldErrors": field_errors},
    )


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content={"message": exc.detail}, headers=exc.headers)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    # Log server-side with the stack trace; return nothing specific to the
    # caller. An error body that echoes the exception is free reconnaissance.
    logger.exception("Unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"message": "Something went wrong while processing your request. Please try again."},
    )


API_PREFIX = "/api/v1"
app.include_router(auth.router, prefix=API_PREFIX)
app.include_router(users.router, prefix=API_PREFIX)
app.include_router(wallet.router, prefix=API_PREFIX)
app.include_router(kyc.router, prefix=API_PREFIX)
app.include_router(funding.router, prefix=API_PREFIX)
app.include_router(card_to_bank.router, prefix=API_PREFIX)
app.include_router(funding.public_router, prefix=API_PREFIX)
app.include_router(transactions.router, prefix=API_PREFIX)
app.include_router(support.router, prefix=API_PREFIX)
app.include_router(limits.router, prefix=API_PREFIX)
app.include_router(requests_inbox.router, prefix=API_PREFIX)
app.include_router(operations.router, prefix=API_PREFIX)
app.include_router(admin.router, prefix=API_PREFIX)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}
