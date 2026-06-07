# File: continuum/harness/ci-mirror.Dockerfile
# Rule 3: Verify locally what CI verifies remotely
# This image is used both locally (make verify) and in CI (Azure Pipelines)
# Keeping them identical prevents drift.

FROM python:3.10-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    git \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy source code
COPY . .

# Install dev tools (for make verify)
RUN pip install --no-cache-dir \
    ruff==0.1.8 \
    mypy==1.7.0 \
    pytest==7.4.0 \
    pytest-asyncio==0.21.1 \
    pytest-cov==4.1.0

# Set entrypoint to bash (for local) or CI can override
ENTRYPOINT ["/bin/bash"]
