#!/usr/bin/env bash
#
# Ensure a conda environment exists for gencli, creating it if needed, then
# install the project into it via Poetry.
#
# Usage:
#   ./scripts/setup_conda_env.sh
#
# Configuration (env vars, all optional):
#   GENCLI_CONDA_ENV       Name of the conda environment (default: gencli)
#   GENCLI_PYTHON_VERSION  Python version to provision (default: 3.13)
#   GENCLI_POETRY_GROUPS   Poetry dependency groups to install
#                          (default: dev,docs,test)
#
# This script is idempotent: re-running it on an existing environment just
# verifies it and re-syncs dependencies, it never recreates the environment.

set -euo pipefail

ENV_NAME="${GENCLI_CONDA_ENV:-gencli}"
PYTHON_VERSION="${GENCLI_PYTHON_VERSION:-3.13}"
POETRY_GROUPS="${GENCLI_POETRY_GROUPS:-dev,docs,test}"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

log() { echo "[setup_conda_env] $*"; }
err() { echo "[setup_conda_env] error: $*" >&2; }

# --- 1. Make sure conda itself is available ---------------------------------
if ! command -v conda >/dev/null 2>&1; then
  err "conda was not found on PATH."
  err "Install Miniconda/Anaconda first: https://docs.conda.io/en/latest/miniconda.html"
  exit 1
fi

# `conda` ships a shell function for interactive use; make sure `conda
# activate` works in this non-interactive script too.
CONDA_BASE="$(conda info --base)"
# shellcheck disable=SC1091
source "$CONDA_BASE/etc/profile.d/conda.sh"

# --- 2. Check whether the environment already exists ------------------------
if conda env list | awk '{print $1}' | grep -Fxq "$ENV_NAME"; then
  log "Environment '$ENV_NAME' already exists — verifying it."
else
  log "Environment '$ENV_NAME' not found — creating it with python=$PYTHON_VERSION."
  conda create -y -n "$ENV_NAME" "python=$PYTHON_VERSION"
fi

conda activate "$ENV_NAME"

# --- 3. Verify the environment is usable and provision Poetry ---------------
ACTUAL_PYTHON_VERSION="$(python -c 'import platform; print(platform.python_version())')"
log "Using python $ACTUAL_PYTHON_VERSION from environment '$ENV_NAME'."

if ! command -v poetry >/dev/null 2>&1; then
  log "poetry not found in this environment — installing it."
  python -m pip install --upgrade pip poetry
fi

# --- 4. Install project dependencies -----------------------------------------
log "Installing project dependencies (groups: $POETRY_GROUPS)."
cd "$REPO_ROOT"
poetry env use python
poetry install --with "$POETRY_GROUPS"

log "Done. Activate the environment with:"
log "  conda activate $ENV_NAME"
log "Then run the CLI with:"
log "  poetry run gencli --help"
