#!/usr/bin/env sh
set -e
cd "$(dirname "$0")/../.."
datamodel-codegen \
  --input contracts/openapi.yaml --input-file-type openapi \
  --output engine/ir_engine/schemas.py \
  --output-model-type pydantic_v2.BaseModel \
  --target-python-version 3.13 \
  --use-standard-collections --use-union-operator \
  --use-specialized-enum --use-annotated --field-constraints \
  --disable-timestamp --use-schema-description \
  --formatters ruff-format
