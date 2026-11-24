#!/usr/bin/env bash
set -euo pipefail

if [[ ${CLAUDE_CODE_REMOTE:-} != true ]]; then
    exit 0
fi

CDPATH= cd -- "${BASH_SOURCE[0]%/*}/../.."

# Keep the spaCy model installed outside uv.lock on subsequent sessions.
uv sync --extra dev --inexact --quiet

if ! uv run python -c "import en_core_web_lg" >/dev/null 2>&1; then
    output=$(uv run python -m spacy download en_core_web_lg 2>&1) || {
        status=$?
        printf '%s\n' "$output" >&2
        exit "$status"
    }
fi
