#!/usr/bin/env bash
set -euo pipefail

# Entrypoint wrapper for the container.
# Usage:
#  - Run the CLI: docker run --rm <image> --help
#  - Run any command: docker run --rm <image> config list

exec poetry run gencli "$@"
