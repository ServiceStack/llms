# Multi-stage build for llms-py
FROM oven/bun:latest as bun
FROM python:3.11-slim as builder

# Set working directory
WORKDIR /app

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first for better caching
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Copy the entire project
COPY . .

# Build the package
RUN pip install --no-cache-dir build && \
    python -m build

# Final stage
FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Install system dependencies and dotnet-sdk 10.0
RUN apt-get update && apt-get install -y --no-install-recommends \
    wget \
    && wget https://packages.microsoft.com/config/debian/12/packages-microsoft-prod.deb -O packages-microsoft-prod.deb \
    && dpkg -i packages-microsoft-prod.deb \
    && rm packages-microsoft-prod.deb \
    && apt-get update \
    && apt-get install -y --no-install-recommends dotnet-sdk-10.0 \
    && rm -rf /var/lib/apt/lists/*

# Install bun
COPY --from=bun /usr/local/bin/bun /usr/local/bin/bun
RUN ln -s /usr/local/bin/bun /usr/local/bin/bunx

# Create a non-root user
RUN useradd -m -u 1000 llms && \
    mkdir -p /home/llms/.llms && \
    chown -R llms:llms /home/llms

# Copy the built wheel from builder
COPY --from=builder /app/dist/*.whl /tmp/

# Install the package
RUN pip install --no-cache-dir /tmp/*.whl && \
    rm -rf /tmp/*.whl

# Switch to non-root user
USER llms

# Set home directory
ENV HOME=/home/llms

# Expose default port
EXPOSE 8000

# Volume for persistent configuration and data
# Mount this to customize llms.json and providers.json or persist analytics data
VOLUME ["/home/llms/.llms"]

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000').read()" || exit 1

# Default command - run server on port 8000
CMD ["llms", "--serve", "8000"]

