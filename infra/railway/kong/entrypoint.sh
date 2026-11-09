#!/bin/sh
set -e

# Validate required environment variables without printing their values
MISSING=0
for VAR_NAME in USER_SERVICE_URL OPERATION_SERVICE_URL REPORT_SERVICE_URL CORS_ORIGIN; do
  eval "VAL=\${$VAR_NAME:-}"
  if [ -z "$VAL" ]; then
    echo "[KONG ENTRYPOINT ERROR] Required environment variable is missing or empty: $VAR_NAME" >&2
    MISSING=1
  fi
done

if [ "$MISSING" -eq 1 ]; then
  echo "[KONG ENTRYPOINT ERROR] Set the missing variables in Railway service settings." >&2
  exit 1
fi

# Render declarative template to /tmp/kong.yml using sed
# Note: delimiter '|' is used to accommodate 'http://' and 'https://' URLs without collisions.
sed \
  -e "s|\${USER_SERVICE_URL}|${USER_SERVICE_URL}|g" \
  -e "s|\${OPERATION_SERVICE_URL}|${OPERATION_SERVICE_URL}|g" \
  -e "s|\${REPORT_SERVICE_URL}|${REPORT_SERVICE_URL}|g" \
  -e "s|\${CORS_ORIGIN}|${CORS_ORIGIN}|g" \
  /kong/declarative/kong.template.yml > /tmp/kong.yml

# Parse and validate declarative config
echo "[KONG ENTRYPOINT] Validating rendered declarative config..."
kong config parse /tmp/kong.yml >/dev/null

# Configure Kong runtime settings
export KONG_DATABASE="off"
export KONG_DECLARATIVE_CONFIG="/tmp/kong.yml"
export KONG_ADMIN_LISTEN="off"
export KONG_PROXY_LISTEN="0.0.0.0:${PORT:-8000}"
export KONG_PROXY_ACCESS_LOG="/dev/stdout"
export KONG_ADMIN_ACCESS_LOG="/dev/stdout"
export KONG_PROXY_ERROR_LOG="/dev/stderr"
export KONG_ADMIN_ERROR_LOG="/dev/stderr"
export KONG_NGINX_HTTP_CLIENT_MAX_BODY_SIZE="${KONG_NGINX_HTTP_CLIENT_MAX_BODY_SIZE:-6m}"

# Client IP & Forwarding behind frontend reverse proxy
export KONG_TRUSTED_IPS="${KONG_TRUSTED_IPS:-0.0.0.0/0,::/0}"
export KONG_REAL_IP_HEADER="${KONG_REAL_IP_HEADER:-X-Forwarded-For}"
export KONG_REAL_IP_RECURSIVE="${KONG_REAL_IP_RECURSIVE:-on}"

echo "[KONG ENTRYPOINT] Configuration valid. Starting Kong Gateway in DB-less mode on port ${PORT:-8000}..."
exec /docker-entrypoint.sh "$@"
