#!/bin/bash

# Script to extract default llms.json and ui.json from the Docker container
# Usage: ./docker-extract-configs.sh [output-directory]

set -e

OUTPUT_DIR="${1:-./config}"
IMAGE="${LLMS_DOCKER_IMAGE:-ghcr.io/servicestack/llms:latest}"

echo "Extracting default configuration files from llms-py Docker image"
echo "================================================================"
echo "Image: $IMAGE"
echo "Output directory: $OUTPUT_DIR"
echo ""

# Create output directory
mkdir -p "$OUTPUT_DIR"

# Pull the latest image
echo "Pulling Docker image..."
docker pull "$IMAGE"

echo ""
echo "Extracting configuration files..."

# Create a temporary container and initialize configs
CONTAINER_ID=$(docker create "$IMAGE" llms --init)

# Start the container briefly to initialize configs
docker start "$CONTAINER_ID" || true
sleep 2
docker stop "$CONTAINER_ID" || true

# Copy the config files
echo "Copying llms.json..."
docker cp "$CONTAINER_ID:/home/llms/.llms/llms.json" "$OUTPUT_DIR/llms.json" 2>/dev/null || {
    echo "Warning: Could not extract llms.json from container, trying alternative method..."
    # Alternative: Run container with init and copy
    docker run --rm -v "$(pwd)/$OUTPUT_DIR:/output" "$IMAGE" sh -c "llms --init && cp /home/llms/.llms/llms.json /output/llms.json" || {
        echo "Error: Could not extract llms.json"
        docker rm -f "$CONTAINER_ID" 2>/dev/null || true
        exit 1
    }
}

echo "Copying ui.json..."
docker cp "$CONTAINER_ID:/home/llms/.llms/ui.json" "$OUTPUT_DIR/ui.json" 2>/dev/null || {
    echo "Warning: Could not extract ui.json from container, trying alternative method..."
    docker run --rm -v "$(pwd)/$OUTPUT_DIR:/output" "$IMAGE" sh -c "llms --init && cp /home/llms/.llms/ui.json /output/ui.json" || {
        echo "Error: Could not extract ui.json"
        docker rm -f "$CONTAINER_ID" 2>/dev/null || true
        exit 1
    }
}

# Clean up
docker rm -f "$CONTAINER_ID" 2>/dev/null || true

echo ""
echo "================================================================"
echo "Configuration files extracted successfully!"
echo ""
echo "Files created:"
echo "  - $OUTPUT_DIR/llms.json"
echo "  - $OUTPUT_DIR/ui.json"
echo ""
echo "You can now edit these files and mount them in your container:"
echo ""
echo "  docker run -p 8000:8000 \\"
echo "    -v \$(pwd)/$OUTPUT_DIR:/home/llms/.llms \\"
echo "    -e OPENROUTER_API_KEY=\"your-key\" \\"
echo "    $IMAGE"
echo ""
echo "Or update your docker-compose.yml volumes section:"
echo ""
echo "  volumes:"
echo "    - ./$OUTPUT_DIR:/home/llms/.llms"
echo ""

