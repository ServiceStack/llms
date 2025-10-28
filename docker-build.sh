#!/bin/bash

# Build script for llms-py Docker image
# Usage: ./docker-build.sh [tag]

set -e

# Default tag
TAG="${1:-latest}"
IMAGE_NAME="llms-py"

echo "Building Docker image: ${IMAGE_NAME}:${TAG}"
echo "=========================================="

# Build the image
docker build -t "${IMAGE_NAME}:${TAG}" .

echo ""
echo "=========================================="
echo "Build complete!"
echo "Image: ${IMAGE_NAME}:${TAG}"
echo ""
echo "To run the container:"
echo "  docker run -p 8000:8000 -e OPENROUTER_API_KEY=\$OPENROUTER_API_KEY ${IMAGE_NAME}:${TAG}"
echo ""
echo "Or use docker-compose:"
echo "  docker-compose up"
echo ""

