#!/usr/bin/env bash
# Thin staging smoke checks for the Loom API (P1 item 12).
# Usage:
#   ./scripts/staging_smoke.sh
#   API_BASE=https://api.example.com ./scripts/staging_smoke.sh
#   SMOKE_TOKEN='eyJ…' ./scripts/staging_smoke.sh
set -euo pipefail

API_BASE="${API_BASE:-http://localhost:8000}"
API_BASE="${API_BASE%/}"
FAIL=0

red() { printf '\033[31m%s\033[0m\n' "$*"; }
green() { printf '\033[32m%s\033[0m\n' "$*"; }
info() { printf '%s\n' "$*"; }

check() {
  local name="$1"
  local expect_code="$2"
  shift 2
  local code
  code="$(curl -sS -o /tmp/loom_smoke_body.json -w '%{http_code}' "$@" || true)"
  if [[ "$code" == "$expect_code" ]]; then
    green "PASS  $name (HTTP $code)"
  else
    red "FAIL  $name (expected HTTP $expect_code, got $code)"
    if [[ -s /tmp/loom_smoke_body.json ]]; then
      head -c 400 /tmp/loom_smoke_body.json
      echo
    fi
    FAIL=1
  fi
}

info "Smoke against ${API_BASE}"

check "GET /health" 200 \
  "${API_BASE}/health"

check "GET /ready" 200 \
  "${API_BASE}/ready"

check "GET /captures unauthenticated" 401 \
  "${API_BASE}/captures"

if [[ -n "${SMOKE_TOKEN:-}" ]]; then
  auth=(-H "Authorization: Bearer ${SMOKE_TOKEN}")
  check "GET /auth/me" 200 \
    "${auth[@]}" "${API_BASE}/auth/me"
  check "GET /org/summary" 200 \
    "${auth[@]}" "${API_BASE}/org/summary"
  check "GET /integrations" 200 \
    "${auth[@]}" "${API_BASE}/integrations"
else
  info "SKIP  authenticated checks (set SMOKE_TOKEN to enable)"
fi

if [[ "$FAIL" -ne 0 ]]; then
  red "Staging smoke failed"
  exit 1
fi
green "Staging smoke passed"
exit 0
