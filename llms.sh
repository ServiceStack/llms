#!/bin/bash

cp ./llms/providers-extra.json ~/.llms/providers-extra.json
cp ./llms/providers.json ~/.llms/providers.json
cp ./llms/llms.json ~/.llms/llms.json

if [ -d .venv ]; then
    # if not activated, activate
    if [ "$(basename "$VIRTUAL_ENV")" != "venv" ]; then
        source .venv/bin/activate
    fi
    if command -v uv &> /dev/null; then
        uv run -m llms "$@"
    fi
elif command -v python3 &> /dev/null; then
    python3 -m llms "$@"
elif command -v python &> /dev/null; then
    python -m llms "$@"
else
    echo "python or uv not found"
    exit 1
fi
