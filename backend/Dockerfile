# FROM python:3.11-slim

# # Set environment variables
# ENV PYTHONDONTWRITEBYTECODE=1
# ENV PYTHONUNBUFFERED=1
# ENV PYTHONPATH=/app

# # Set working directory
# WORKDIR /app

# # Install system dependencies
# RUN apt-get update && apt-get install -y \
#     gcc \
#     g++ \
#     libpq-dev \
#     curl \
#     wget \
#     && rm -rf /var/lib/apt/lists/* \
#     && apt-get clean

# # Create non-root user
# RUN useradd -m -u 1000 appuser && \
#     chown -R appuser:appuser /app

# # Copy requirements first for better Docker layer caching
# COPY requirements.txt .

# # Install Python dependencies
# # Install Python dependencies
# RUN pip install --no-cache-dir --upgrade pip && \
#     pip install --no-cache-dir -r requirements.txt && \
#     opentelemetry-bootstrap --action=install


# # Copy application code
# COPY app/ ./app/

# # Create necessary directories and set permissions
# RUN mkdir -p /tmp && \
#     chown -R appuser:appuser /app /tmp &&  apt-get update && apt-get install -y postgresql-client && rm -rf /var/lib/apt/lists/*


# # Switch to non-root user
# USER appuser

# # Environment variables for OpenTelemetry
# ENV OTEL_SERVICE_NAME=todo-api
# ENV OTEL_SERVICE_VERSION=1.0.0
# ENV OTEL_DEPLOYMENT_ENVIRONMENT=production
# ENV OTEL_EXPORTER_OTLP_ENDPOINT=otel-collector:4317
# ENV OTEL_EXPORTER_OTLP_INSECURE=true
# ENV OTEL_PYTHON_LOG_CORRELATION=true
# ENV OTEL_PYTHON_LOGGING_AUTO_INSTRUMENTATION_ENABLED=true

# # Expose port
# EXPOSE 8080

# # Health check
# HEALTHCHECK --interval=30s --timeout=30s --start-period=40s --retries=3 \
#     CMD python -c "import requests; requests.get('http://localhost:8080/health', timeout=10)" || exit 1

# # Run with OpenTelemetry auto-instrumentation
# CMD ["opentelemetry-instrument", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "1"]

FROM python:3.11

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app

# Set working directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    g++ \
    libpq-dev \
    curl \
    wget \
    postgresql-client \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user
RUN useradd -m -u 1000 appuser && \
    chown -R appuser:appuser /app

# Copy requirements first for better Docker layer caching
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt && \
    opentelemetry-bootstrap --action=install
# Copy application code
COPY app/ ./app/

# Create necessary directories and set permissions
RUN mkdir -p /tmp && \
    chown -R appuser:appuser /app /tmp

# Switch to non-root user
USER appuser

# Environment variables for OpenTelemetry
ENV OTEL_SERVICE_NAME=todo-api
ENV OTEL_SERVICE_VERSION=1.0.0
ENV OTEL_DEPLOYMENT_ENVIRONMENT=production
ENV OTEL_EXPORTER_OTLP_ENDPOINT=otel-collector:4317
ENV OTEL_EXPORTER_OTLP_INSECURE=true
ENV OTEL_PYTHON_LOG_CORRELATION=true
ENV OTEL_PYTHON_LOGGING_AUTO_INSTRUMENTATION_ENABLED=true

# Expose port
EXPOSE 8080

# Health check
HEALTHCHECK --interval=30s --timeout=30s --start-period=40s --retries=3 \
    CMD python -c "import requests; requests.get('http://localhost:8080/health', timeout=10)" || exit 1

# Run with OpenTelemetry auto-instrumentation
CMD ["opentelemetry-instrument", "python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "1"]
