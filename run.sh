#!/usr/bin/env bash
# Run without Docker (needs uv: https://docs.astral.sh/uv/). Opens on http://127.0.0.1:7860
cd "$(dirname "$0")"
exec uv run uvicorn server:app --host "${HOST:-127.0.0.1}" --port "${PORT:-7860}"
