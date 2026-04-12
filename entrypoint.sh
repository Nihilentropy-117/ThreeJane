#!/bin/sh
# Create venv if bind mount is empty
if [ ! -f /venv/bin/python3 ]; then
  python3 -m venv /venv
fi
exec "$@"
