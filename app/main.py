import sys

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from app.core.config import get_settings
from app.core.rate_limiter import limiter
from app.routers import admin, auth, card_to_bank, funding, requests_inbox, kyc, limits, operations, support, transactions, users, wallet

settings = get_settings()

# Refuse to start in production with the insecure default secret.
if settings.environment == "production" and settings.jwt_secret == "insecure-dev-secret-change-me-before-production-use":
    print("FATAL: JWT_SECRET is set to the insecure default. Set a strong random secret before deploying.", file=sys.stderr)
    sys.exit(1)

app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    # Never expose the interactive docs in production — they reveal the full
    # API surface and can be used to probe endpoints without any tooling.
    docs_url="/docs" if settings.environment != "production" else None,
    redoc_url="/redoc" if settings.environment != "production" else None,
    openapi_url="/openapi.json" if settings.environment != "production" else None,
)

# Attach rate-limiter state so slowapi can find it on the app instance.
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    # Enumerate only the verbs and headers the frontend actually sends —
    # * would accept anything, including headers injected by attackers.
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "Idempotency-Key", "X-Request-ID"],
)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    if settings.environment == "production":
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response


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
