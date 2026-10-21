#!/usr/bin/env sh
set -eu

ROOT="$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)"
OUT="${ROOT}/generated"
SPEC="${ROOT}/src/main/resources/openapi/marketplace.yaml"

rm -rf "${OUT}"
mkdir -p "${OUT}"

docker run --rm \
  --user "$(id -u):$(id -g)" \
  -v "${SPEC}:/spec/openapi.yaml:ro" \
  -v "${OUT}:/out" \
  openapitools/openapi-generator-cli:v7.12.0 generate \
  -i /spec/openapi.yaml \
  -g python-fastapi \
  -o /out \
  --package-name openapi_server \
  --additional-properties=packageName=openapi_server,fastapiImplementationPackage=impl,useExternalImplementationPackage=true,sourceFolder=src

python3 "${ROOT}/scripts/patch_generated.py" "${OUT}/src/openapi_server"
cp "${ROOT}/overrides/security_api.py" "${OUT}/src/openapi_server/security_api.py"

echo "generated -> ${OUT}"
