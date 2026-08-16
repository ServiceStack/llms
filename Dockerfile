# Multi-stage build for llms-py
FROM oven/bun:latest AS bun
FROM python:3.11-slim AS builder

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

# System dependencies.
#
# .NET is installed below with dotnet-install.sh rather than from the Microsoft
# apt repo, so apt never pulls in its native dependencies — they have to be
# listed explicitly. Without libicu every .NET process aborts on startup with
# "Couldn't find a valid ICU package installed on the system".
#
# libicu-dev is used instead of a pinned libicuNN so this keeps working when the
# Python base image moves to a newer Debian (bookworm ships libicu72, trixie
# libicu76). libssl, libstdc++, zlib and libgcc already come with the base image.
# ffmpeg is needed by the voice extension, which converts the browser's webm
# recording to 16 kHz mono WAV before handing it to a speech-to-text tool.
# See https://learn.microsoft.com/dotnet/core/install/linux-debian
RUN apt-get update && apt-get install -y --no-install-recommends \
    wget \
    git \
    ca-certificates \
    xz-utils \
    ffmpeg \
    libicu-dev \
    libgssapi-krb5-2 \
    tzdata \
    fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

# Install dotnet-sdk 10.0 using install script to bypass GPG SHA1 issues
RUN wget https://dot.net/v1/dotnet-install.sh -O dotnet-install.sh \
    && chmod +x dotnet-install.sh \
    && ./dotnet-install.sh --channel 10.0 --install-dir /usr/share/dotnet \
    && rm dotnet-install.sh

# Set dotnet environment variables
ENV DOTNET_ROOT=/usr/share/dotnet
ENV PATH=$PATH:$DOTNET_ROOT
ENV DOTNET_NOLOGO=1
ENV DOTNET_CLI_TELEMETRY_OPTOUT=1

# Install typst — required by the PDF Studio extension (llms/extensions/pdf),
# which disables itself when `typst` isn't on PATH.
#
# The upstream musl builds are statically linked, so one binary works on any
# distro. TARGETARCH is supplied automatically by buildx for each platform in
# the multi-arch build. Bump TYPST_VERSION to update.
ARG TYPST_VERSION=0.15.1
ARG TARGETARCH
RUN set -eux; \
    case "${TARGETARCH:-amd64}" in \
        amd64) typst_arch=x86_64 ;; \
        arm64) typst_arch=aarch64 ;; \
        *) echo "unsupported TARGETARCH: ${TARGETARCH}" >&2; exit 1 ;; \
    esac; \
    tarball="typst-${typst_arch}-unknown-linux-musl"; \
    wget -qO /tmp/typst.tar.xz \
        "https://github.com/typst/typst/releases/download/v${TYPST_VERSION}/${tarball}.tar.xz"; \
    tar -xJf /tmp/typst.tar.xz -C /tmp; \
    install -m 0755 "/tmp/${tarball}/typst" /usr/local/bin/typst; \
    rm -rf /tmp/typst.tar.xz "/tmp/${tarball}"

# Install bun
COPY --from=bun /usr/local/bin/bun /usr/local/bin/bun
RUN ln -s /usr/local/bin/bun /usr/local/bin/bunx

# Fail the build if any runtime is broken rather than at the first user request.
# `dotnet --version` starts the CLI, which is itself a .NET app, so it exercises
# the globalization path that needs libicu.
RUN dotnet --version \
    && bun --version \
    && typst --version \
    && ffmpeg -version > /dev/null

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

# Don't buffer stdout/stderr, so `docker logs` shows output as it happens
ENV PYTHONUNBUFFERED=1

# Warm the .NET first-run cache as the llms user so the first C# request
# doesn't pay for it (and doesn't try to write to a read-only home).
RUN dotnet --version > /dev/null 2>&1 || true

# Expose default port
EXPOSE 8000

# Volume for persistent configuration and data
# Mount this to customize llms.json and providers.json or persist analytics data
VOLUME ["/home/llms/.llms"]

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000').read()" || exit 1

# Default command - run server on port 8000
# Set VERBOSE=1 and/or DEBUG=1 to get request logging (see DOCKER.md)
CMD ["llms", "--serve", "8000"]
