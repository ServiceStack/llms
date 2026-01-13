#!/bin/bash

# Test out single flie CLI functionality

cp ../llms/main.py .
LLMS_DISABLE=xmas,duckduckgo,gemini ./main.py "$@"
rm main.py