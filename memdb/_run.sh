#!/bin/bash
set -e
cd "$(dirname "$0")"
exec ./build/memdb --instance "${INSTANCE:-memdb}" --log-level "${LOG_LEVEL:-info}" "$@"