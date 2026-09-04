#!/usr/bin/env bash

KMP_DUPLICATE_LIB_OK=TRUE UV_CACHE_DIR="$PWD/.uv-cache" \
  uv run pytest tests/ -v --full-trace \
    --cov=src \
    --cov-branch \
    --cov-report=term-missing \
    --cov-report=xml \
    --cov-report=html \
    --cov-report=json:coverage.json

