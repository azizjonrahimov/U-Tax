"""Contract error shape: non-2xx with {"error": {"code": "<snake_case>", "message": "..."}}."""

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

_STATUS_CODES = {
    400: "bad_request",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    409: "conflict",
    422: "validation_error",
    429: "rate_limited",
    500: "internal_error",
}


class APIError(Exception):
    """Raise from any route to return the contract error shape."""

    def __init__(self, status_code: int, code: str, message: str,
                 details: dict | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details or {}  # extra fields in the error object (contract-named)


def error_response(status_code: int, code: str, message: str,
                   details: dict | None = None) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"error": {**(details or {}), "code": code, "message": message}},
    )


def _code_for(status_code: int) -> str:
    return _STATUS_CODES.get(status_code, "error" if status_code < 500 else "internal_error")


async def _api_error(_: Request, exc: APIError) -> JSONResponse:
    return error_response(exc.status_code, exc.code, exc.message, exc.details)


async def _http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
    message = exc.detail if isinstance(exc.detail, str) else "Request failed"
    response = error_response(exc.status_code, _code_for(exc.status_code), message)
    if exc.headers:
        response.headers.update(exc.headers)
    return response


async def _validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
    first = exc.errors()[0] if exc.errors() else {}
    where = ".".join(str(p) for p in first.get("loc", []))
    detail = first.get("msg", "Invalid request")
    message = f"{where}: {detail}" if where else detail
    return error_response(422, "validation_error", message)


async def _unhandled_error(_: Request, __: Exception) -> JSONResponse:
    return error_response(500, "internal_error", "Internal server error")


def install_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(APIError, _api_error)
    app.add_exception_handler(StarletteHTTPException, _http_error)
    app.add_exception_handler(RequestValidationError, _validation_error)
    app.add_exception_handler(Exception, _unhandled_error)
