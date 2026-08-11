#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")" || exit

make clear && make $@

make install
