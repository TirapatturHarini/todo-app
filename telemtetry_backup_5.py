import os
import logging
from typing import Optional, Dict, Any

from opentelemetry import trace, metrics
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.semconv.resource import ResourceAttributes

# Instrumentations
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
from opentelemetry.instrumentation.psycopg2 import Psycopg2Instrumentor
from opentelemetry.instrumentation.requests import RequestsInstrumentor
from opentelemetry.instrumentation.logging import LoggingInstrumentor

logger = logging.getLogger(__name__)
from opentelemetry._logs import set_logger_provider

def setup_logging(resource: Resource, otlp_endpoint: str):
    # Create log provider
    logger_provider = LoggerProvider(resource=resource)
    set_logger_provider(logger_provider)

    # OTLP Log Exporter
    otlp_log_exporter = OTLPLogExporter(endpoint=otlp_endpoint, insecure=True)
    logger_provider.add_log_record_processor(
        BatchLogRecordProcessor(otlp_log_exporter)
    )

    # Attach Python logging to OpenTelemetry
    handler = LoggingHandler(level=logging.INFO, logger_provider=logger_provider)
    logging.getLogger().addHandler(handler)
    logging.getLogger().setLevel(logging.INFO)

    return logger_provider

def setup_telemetry(app=None) -> None:
    """Configure OpenTelemetry providers and exporters, then instrument libraries."""
    try:
        service_name = os.getenv("OTEL_SERVICE_NAME", "todo-api")
        service_version = os.getenv("OTEL_SERVICE_VERSION", "1.0.0")
        deployment_environment = os.getenv("OTEL_DEPLOYMENT_ENVIRONMENT", "development")
        otlp_endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://otel-collector:4317")

        resource = Resource.create(
            {
                ResourceAttributes.SERVICE_NAME: service_name,
                ResourceAttributes.SERVICE_VERSION: service_version,
                ResourceAttributes.DEPLOYMENT_ENVIRONMENT: deployment_environment,
                ResourceAttributes.SERVICE_INSTANCE_ID: os.getenv("HOSTNAME", "unknown"),
            }
        )

        # ---- Traces ----
        trace_provider = TracerProvider(resource=resource)
        trace.set_tracer_provider(trace_provider)
        try:
            span_exporter = OTLPSpanExporter(endpoint=otlp_endpoint, insecure=True)
            trace_provider.add_span_processor(BatchSpanProcessor(span_exporter))
            logger.info("OTLP trace exporter configured: %s", otlp_endpoint)
        except Exception as e:
            logger.warning("Failed to configure OTLP trace exporter: %s", e)

        # ---- Metrics ----
        try:
            metric_exporter = OTLPMetricExporter(endpoint=otlp_endpoint, insecure=True)
            metric_reader = PeriodicExportingMetricReader(
                exporter=metric_exporter, export_interval_millis=10_000
            )
            meter_provider = MeterProvider(resource=resource, metric_readers=[metric_reader])
            metrics.set_meter_provider(meter_provider)
            logger.info("OTLP metric exporter configured: %s", otlp_endpoint)
        except Exception as e:
            logger.warning("Failed to configure OTLP metric exporter: %s", e)
        #logs 
        setup_logging(resource, otlp_endpoint)
        
        # ---- Auto-instrumentation ----
        setup_auto_instrumentation(app)
        logger.info("OpenTelemetry initialized (service=%s)", service_name)

    except Exception as e:
        logger.error("OpenTelemetry setup failed: %s", e)


def setup_auto_instrumentation(app=None) -> None:
    """Safely instrument frameworks/libraries."""
    try:
        if app is not None:
            FastAPIInstrumentor().instrument_app(app)
        else:
            FastAPIInstrumentor().instrument()
    except Exception as e:
        logger.debug("FastAPI instrumentation skipped: %s", e)

    try:
        SQLAlchemyInstrumentor().instrument()
    except Exception as e:
        logger.debug("SQLAlchemy instrumentation skipped: %s", e)

    try:
        Psycopg2Instrumentor().instrument()
    except Exception as e:
        logger.debug("psycopg2 instrumentation skipped: %s", e)

    try:
        RequestsInstrumentor().instrument()
    except Exception as e:
        logger.debug("requests instrumentation skipped: %s", e)

    try:
        LoggingInstrumentor().instrument(
            set_logging_format=True,
            logging_format="%(asctime)s %(levelname)s [trace_id=%(otelTraceID)s span_id=%(otelSpanID)s] %(message)s status=%(status)s ",
        )
    except Exception as e:
        logger.debug("logging instrumentation skipped: %s", e)


def get_tracer():
    return trace.get_tracer(__name__)


def get_meter():
    return metrics.get_meter(__name__)


def add_span_attributes(**attributes):
    """Helper to add custom attributes to the current span."""
    try:
        current_span = trace.get_current_span()
        if current_span and current_span.is_recording():
            for k, v in attributes.items():
                current_span.set_attribute(k, v)
    except Exception:
        pass
BUSINESS_LABELS: Dict[str, Any] = {}


def add_business_labels(labels: Dict[str, Any]):
    """
    Attach application-specific labels (like todo status) to the current span.
    Example: add_business_labels({"status": "completed"})
    """
    global BUSINESS_LABELS
    BUSINESS_LABELS.update(labels)
    add_span_attributes(**labels)


def log_with_trace_context(logger_instance, level: str, message: str, **kwargs):
    """
    Log a message and inject current trace/span IDs + custom labels.
    """
    try:
        span = trace.get_current_span()
        if span and span.is_recording():
            ctx = span.get_span_context()
            kwargs = {
                **kwargs,
                "trace_id": f"{ctx.trace_id:032x}",
                "span_id": f"{ctx.span_id:016x}",
                **BUSINESS_LABELS, 
            }
        getattr(logger_instance, level)(message, extra=kwargs)
    except Exception:
        getattr(logger_instance, level)(message, extra=kwargs)
