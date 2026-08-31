#!/usr/bin/env bash
# Convenience wrapper: ./triage.sh <command>  runs the CLI inside the venv.
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$DIR/.venv/bin/activate"
python -m triage.cli "$@"
