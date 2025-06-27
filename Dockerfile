# Use Python 3.11 slim image as base
FROM python:3.11-slim

# Set metadata
LABEL maintainer="Trickle Relay Service"
LABEL description="Standalone microservice bridging trickle HTTP segments and WebRTC"
LABEL version="1.0.0"

# Set environment variables
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Install system dependencies required for PyAV, WebRTC, and networking
RUN apt-get update && apt-get install -y \
    # FFmpeg and media libraries for PyAV
    ffmpeg \
    libavcodec-dev \
    libavformat-dev \
    libavutil-dev \
    libswscale-dev \
    libswresample-dev \
    # Audio libraries for WebRTC
    libasound2-dev \
    libpulse-dev \
    # Network and SSL libraries
    libssl-dev \
    libffi-dev \
    # Build tools (needed for some Python packages)
    build-essential \
    pkg-config \
    # System utilities
    curl \
    # Clean up to reduce image size
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/* \
    && rm -rf /tmp/* \
    && rm -rf /var/tmp/*

# Create a non-root user for security
RUN groupadd --gid 1000 appuser \
    && useradd --uid 1000 --gid appuser --shell /bin/bash --create-home appuser

# Set working directory
WORKDIR /app

# Copy requirements first for better Docker layer caching
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy application files
COPY trickle_relay_service.py .
COPY trickle_components.py .
COPY example_usage.py .
COPY README.md .

# Copy any additional configuration files if they exist
COPY .env.template* ./

# Change ownership of the app directory to the non-root user
RUN chown -R appuser:appuser /app

# Switch to non-root user
USER appuser

# Create directories for logs and data (if needed)
RUN mkdir -p /app/logs /app/data

# Expose the default port
EXPOSE 8890

# Health check to ensure the service is running
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8890/health || exit 1

# Set the default command
CMD ["python", "trickle_relay_service.py", "--host", "0.0.0.0", "--port", "8890", "--log-level", "INFO"]

# Alternative entrypoint for development/debugging
# ENTRYPOINT ["python", "trickle_relay_service.py"]
