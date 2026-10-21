import json
import logging
import time
import uuid
from datetime import datetime, timezone

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

log = logging.getLogger("marketplace.api")
SECRETS = {"password", "refresh_token", "access_token"}
MUTATING = {"POST", "PUT", "DELETE"}


class RequestLogMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        request_id = str(uuid.uuid4())
        raw = await request.body() if request.method in MUTATING else b""
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            self._log(request, request_id, 500, started, raw)
            raise
        response.headers["X-Request-Id"] = request_id
        self._log(request, request_id, response.status_code, started, raw)
        return response

    def _log(self, request: Request, request_id: str, status_code: int, started: float, raw: bytes) -> None:
        entry = {
            "request_id": request_id,
            "method": request.method,
            "endpoint": request.url.path,
            "status_code": status_code,
            "duration_ms": int((time.perf_counter() - started) * 1000),
            "user_id": getattr(request.state, "user_id", None),
            "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        }
        if request.method in MUTATING:
            entry["body"] = mask(raw)
        log.info(json.dumps(entry, ensure_ascii=False))


def mask(raw: bytes):
    if not raw:
        return None
    text = raw.decode("utf-8", errors="replace")
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return text
    return json.dumps(_mask(payload), ensure_ascii=False)


def _mask(node):
    if isinstance(node, dict):
        return {key: "***" if key in SECRETS else _mask(value) for key, value in node.items()}
    if isinstance(node, list):
        return [_mask(item) for item in node]
    return node


def install() -> None:
    log.setLevel(logging.INFO)
    if not log.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(message)s"))
        log.addHandler(handler)
    log.propagate = False
