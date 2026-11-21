"""Transport-level hardening applied to every response."""

from __future__ import annotations

from starlette.datastructures import MutableHeaders
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.config import Settings

# This service returns JSON and file downloads only — it never renders HTML
# that a browser should execute. The tightest possible policy is therefore
# also the correct one: no scripts, no embedding, no outbound connections.
# (The SPA is served by a separate static host and needs its own, different
# CSP — see Frontend/serve.json.)
_API_CSP = (
    "default-src 'none'; "
    "frame-ancestors 'none'; "
    "base-uri 'none'; "
    "form-action 'none'; "
    "sandbox"
)

# Relaxed just enough for Swagger UI / ReDoc to work when docs are enabled.
_DOCS_CSP = (
    "default-src 'none'; "
    "script-src 'self' https://cdn.jsdelivr.net 'unsafe-inline'; "
    "style-src 'self' https://cdn.jsdelivr.net 'unsafe-inline'; "
    "img-src 'self' https://fastapi.tiangolo.com data:; "
    "font-src 'self' https://cdn.jsdelivr.net; "
    "connect-src 'self'; "
    "frame-ancestors 'none'; "
    "base-uri 'none'"
)

_DOCS_PATHS = ("/docs", "/redoc", "/openapi.json", "/docs/oauth2-redirect")


class SecurityHeadersMiddleware:
    """Adds the standard browser-hardening headers.

    Written against the raw ASGI interface rather than BaseHTTPMiddleware so
    it also covers streaming responses (FileResponse for KYC documents) and
    does not interfere with the exception handlers in app.main.
    """

    def __init__(self, app: ASGIApp, settings: Settings) -> None:
        self.app = app
        self.settings = settings

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path: str = scope.get("path", "")
        is_docs = path in _DOCS_PATHS

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers.setdefault("x-content-type-options", "nosniff")
                headers.setdefault("x-frame-options", "DENY")
                headers.setdefault("referrer-policy", "no-referrer")
                headers.setdefault("content-security-policy", _DOCS_CSP if is_docs else _API_CSP)
                headers.setdefault("cross-origin-opener-policy", "same-origin")
                headers.setdefault("cross-origin-resource-policy", "same-site")
                headers.setdefault("x-permitted-cross-domain-policies", "none")
                headers.setdefault(
                    "permissions-policy",
                    "accelerometer=(), camera=(), geolocation=(), gyroscope=(), "
                    "magnetometer=(), microphone=(), payment=(), usb=()",
                )
                # Authenticated JSON must never sit in a shared or browser
                # cache; KYC documents and wallet balances especially.
                headers.setdefault("cache-control", "no-store")
                if self.settings.is_production:
                    headers.setdefault(
                        "strict-transport-security",
                        "max-age=31536000; includeSubDomains",
                    )
                # Hide the exact server/framework build from fingerprinting.
                if "server" in headers:
                    del headers["server"]
            await send(message)

        await self.app(scope, receive, send_with_headers)


class MaxBodySizeMiddleware:
    """Rejects oversized request bodies.

    Checks Content-Length first (cheap), then counts bytes as they stream in
    so a chunked request without a declared length cannot slip past. Without
    this, an upload is fully buffered before any handler-level size check can
    look at it.
    """

    def __init__(self, app: ASGIApp, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("method") in ("GET", "HEAD", "OPTIONS"):
            await self.app(scope, receive, send)
            return

        request = Request(scope)
        declared = request.headers.get("content-length")
        if declared is not None:
            try:
                if int(declared) > self.max_bytes:
                    await self._too_large(scope, receive, send)
                    return
            except ValueError:
                pass  # Malformed header; the byte counter below still applies.

        received = 0
        too_large = False

        async def counting_receive() -> Message:
            nonlocal received, too_large
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    too_large = True
                    # Stop feeding the body to the app.
                    return {"type": "http.disconnect"}
            return message

        sent_start = False

        async def guarded_send(message: Message) -> None:
            nonlocal sent_start
            if message["type"] == "http.response.start":
                sent_start = True
            await send(message)

        await self.app(scope, counting_receive, guarded_send)

        if too_large and not sent_start:
            await self._too_large(scope, receive, send)

    async def _too_large(self, scope: Scope, receive: Receive, send: Send) -> None:
        megabytes = self.max_bytes / (1024 * 1024)
        response = JSONResponse(
            status_code=413,
            content={"message": f"Request is too large. The maximum accepted size is {megabytes:.0f}MB."},
        )
        await response(scope, receive, send)


def no_store(response: Response) -> Response:
    """Belt-and-braces helper for handlers returning especially sensitive
    payloads (tokens, KYC documents)."""
    response.headers["cache-control"] = "no-store"
    response.headers["pragma"] = "no-cache"
    return response
