FROM openapitools/openapi-generator-cli:v7.12.0 AS generate

WORKDIR /work
COPY src/main/resources/openapi/marketplace.yaml /work/openapi.yaml
RUN docker-entrypoint.sh generate \
      -i /work/openapi.yaml \
      -g python-fastapi \
      -o /work/generated \
      --package-name openapi_server \
      --additional-properties=packageName=openapi_server,fastapiImplementationPackage=impl,useExternalImplementationPackage=true,sourceFolder=src

FROM python:3.12-slim AS runtime

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY --from=generate /work/generated/src/openapi_server /app/openapi_server
COPY scripts/patch_generated.py /tmp/patch_generated.py
COPY overrides/security_api.py /app/openapi_server/security_api.py
RUN python /tmp/patch_generated.py /app/openapi_server

COPY app /app/app
COPY impl /app/impl

ENV PYTHONPATH=/app
EXPOSE 8080
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
