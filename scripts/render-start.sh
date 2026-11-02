#!/usr/bin/env bash
set -euo pipefail
# Free web services do not provide a separate pre-deploy command.
# Run migrations once before starting the single web-service instance.
python manage.py migrate --noinput
exec gunicorn config.wsgi:application --bind "0.0.0.0:${PORT:-10000}" --workers 2 --timeout 60 --access-logfile -
