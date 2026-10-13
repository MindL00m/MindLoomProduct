#!/bin/sh
set -e

PORT="${PORT:-80}"

# Railway routes the public domain to $PORT (often 8080). Compose leaves PORT
# unset so nginx stays on 80.
sed -i "s/listen [0-9]*;/listen 0.0.0.0:${PORT};/" /etc/nginx/conf.d/default.conf

# Optional: Railway private/public API origin, e.g. http://mindloom-api.railway.internal:8080
if [ -n "${API_UPSTREAM:-}" ]; then
  escaped=$(printf '%s' "$API_UPSTREAM" | sed 's/[&/\]/\\&/g')
  sed -i "s|http://api:8000|${escaped}|" /etc/nginx/conf.d/default.conf
fi

exec nginx -g "daemon off;"
