from app.errors import install as install_errors
from app.logging import RequestLogMiddleware
from app.logging import install as install_logging
from openapi_server.main import app

install_logging()
install_errors(app)
app.add_middleware(RequestLogMiddleware)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}
