from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, details: dict | None = None):
        self.status = status
        self.code = code
        self.message = message
        self.details = details


def validation(field: str, message: str) -> ApiError:
    return ApiError(400, "VALIDATION_ERROR", "Validation failed", {"violations": [{"field": field, "message": message}]})


def install(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def api_error(_: Request, exc: ApiError) -> JSONResponse:
        body: dict = {"error_code": exc.code, "message": exc.message}
        if exc.details:
            body["details"] = exc.details
        return JSONResponse(status_code=exc.status, content=body)

    @app.exception_handler(RequestValidationError)
    async def invalid(_: Request, exc: RequestValidationError) -> JSONResponse:
        violations = []
        for error in exc.errors():
            parts = [str(item) for item in error.get("loc", []) if item not in ("body", "query", "path")]
            violations.append({"field": ".".join(parts) or "body", "message": error.get("msg", "invalid")})
        return JSONResponse(
            status_code=400,
            content={
                "error_code": "VALIDATION_ERROR",
                "message": "Validation failed",
                "details": {"violations": violations},
            },
        )
