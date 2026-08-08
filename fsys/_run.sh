#!/bin/bash
set -e
cd "$(dirname "$0")"
exec ./build/fsys "$@"
