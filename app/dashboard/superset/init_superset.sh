#!/bin/bash
set -euo pipefail

superset db upgrade
superset fab create-admin \
  --username "${SUPERSET_ADMIN_USERNAME:-admin}" \
  --firstname "Superset" \
  --lastname "Admin" \
  --email "admin@example.com" \
  --password "${SUPERSET_ADMIN_PASSWORD:-admin}" || true
superset init
python /app/bootstrap_superset.py

echo "Superset initialized."
