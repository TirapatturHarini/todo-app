import os
import logging
from typing import Dict, Any, Optional
from contextlib import contextmanager
from fastapi import FastAPI
import time
from opentelemetry.sdk.metrics import Histogram, Counter
from opentelemetry import trace, metrics, _logs
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.grpc._log_exporter import OTLPLogExporter
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.sdk.resources import Resource
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
from opentelemetry.instrumentation.requests import RequestsInstrumentor
from opentelemetry.trace.status import Status, StatusCode


def init_telemetry(app: FastAPI):
    FastAPIInstrumentor().instrument_app(app)
    SQLAlchemyInstrumentor().instrument()
    RequestsInstrumentor().instrument()


class TraceIdSpanIdFilter(logging.Filter):
    def filter(self, record):
        current_span = trace.get_current_span()
        if current_span and current_span.is_recording():
            span_context = current_span.get_span_context()
            record.otelTraceID = format(span_context.trace_id, "032x")
            record.otelSpanID = format(span_context.span_id, "016x")
        else:
            record.otelTraceID = ""
            record.otelSpanID = ""
        
        # CRITICAL FIX: Don't override existing status from application code
        # Only set status if it's completely missing AND not already set by StatusEnsureFormatter
        if not hasattr(record, "status") or record.status is None or record.status == "":
            # Check if this is a log with extra data that might have status
            if hasattr(record, 'extra') and isinstance(record.extra, dict) and 'status' in record.extra:
                record.status = record.extra['status']
            else:
                record.status = "unknown"
        
        return True


def setup_telemetry(app: FastAPI):
    service_name = os.getenv("SERVICE_NAME", "todo-api")
    service_version = os.getenv("SERVICE_VERSION", "1.0.0")
    environment = os.getenv("ENVIRONMENT", "development")
    otel_endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://otel-collector:4317")

    resource = Resource.create(
        {
            "service.name": service_name,
            "service.version": service_version,
            "deployment.environment": environment,
            "service.instance.id": os.getenv("HOSTNAME", "unknown"),
        }
    )

    # Tracing
    trace.set_tracer_provider(TracerProvider(resource=resource))
    tracer_provider = trace.get_tracer_provider()
    otlp_span_exporter = OTLPSpanExporter(endpoint=otel_endpoint, insecure=True)
    tracer_provider.add_span_processor(BatchSpanProcessor(otlp_span_exporter))

    # Metrics
    metric_reader = PeriodicExportingMetricReader(
        OTLPMetricExporter(endpoint=otel_endpoint, insecure=True),
        export_interval_millis=30000,
    )
    metrics.set_meter_provider(MeterProvider(resource=resource, metric_readers=[metric_reader]))

    # Logging with enhanced configuration for Loki labels
    _logs.set_logger_provider(LoggerProvider(resource=resource))
    logger_provider = _logs.get_logger_provider()
    otlp_log_exporter = OTLPLogExporter(endpoint=otel_endpoint, insecure=True)
    logger_provider.add_log_record_processor(BatchLogRecordProcessor(otlp_log_exporter))

    otel_handler = LoggingHandler(logger_provider=logger_provider)
    logging.getLogger().addHandler(otel_handler)

    # IMPORTANT: Apply the trace filter to existing handlers
    # This needs to happen AFTER your main.py setup_logging() has run
    trace_filter = TraceIdSpanIdFilter()
    for handler in logging.getLogger().handlers:
        handler.addFilter(trace_filter)

    init_telemetry(app)


def get_tracer():
    return trace.get_tracer(__name__)


def get_meter():
    return metrics.get_meter(__name__)


@contextmanager
def trace_todo_operation(operation: str, todo_id: Optional[str] = None, **attributes):
    tracer = get_tracer()
    span_attributes = {"todo.operation": operation, "service.name": "todo-api"}
    if todo_id:
        span_attributes["todo.id"] = todo_id
    span_attributes.update(attributes)

    with tracer.start_as_current_span(f"todo_{operation}", attributes=span_attributes) as span:
        try:
            yield span
        except Exception as e:
            span.record_exception(e)
            span.set_status(Status(StatusCode.ERROR, str(e)))
            raise


def log_todo_event(operation: str, todo_id: Optional[str] = None, status: str = "success", details: Optional[Dict[str, Any]] = None):
    """Enhanced logging function that ensures proper status labels for Loki"""
    logger = logging.getLogger(__name__)
    
    # Extract operation type for better categorization
    operation_type = details.get("operation", operation) if details else operation   
    message = f"Todo {operation_type}" + (f" (ID: {todo_id})" if todo_id else "")
    
    # Build comprehensive extra data for Loki labels
    extra_data = {
        "status": status,  # This is crucial for Loki filtering
        "operation": operation,
        "operation_type": operation_type,
        "service": "todo-api",
        "component": "todo-handler"
    }
    
    # Add trace context
    trace_id = get_current_trace_id()
    span_id = get_current_span_id()
    if trace_id:
        extra_data["trace_id"] = trace_id
        extra_data["traceId"] = trace_id 
    if span_id:
        extra_data["span_id"] = span_id
    if todo_id:
        extra_data["todo_id"] = todo_id
    
    # Merge additional details
    if details:
        # Don't override the main status with details status
        details_copy = details.copy()
        if "status" in details_copy:
            details_copy["details_status"] = details_copy.pop("status")
        extra_data.update(details_copy)
    
    # Log with appropriate level based on status
    if status == "error":
        logger.error(message, extra=extra_data)
    elif status in ["warning", "warn"]:
        logger.warning(message, extra=extra_data)
    else:
        logger.info(message, extra=extra_data)


def get_current_trace_id() -> Optional[str]:
    span = trace.get_current_span()
    return format(span.get_span_context().trace_id, "032x") if span and span.is_recording() else None


def get_current_span_id() -> Optional[str]:
    span = trace.get_current_span()
    return format(span.get_span_context().span_id, "016x") if span and span.is_recording() else None


@contextmanager
def trace_business_operation(operation_name: str, **attributes):
    """Enhanced tracing for business operations with better categorization"""
    tracer = get_tracer()
    span_attributes = {
        "business.operation": operation_name,
        "service.name": "todo-api",
        "operation.category": _get_operation_category(operation_name)
    }
    span_attributes.update(attributes)

    with tracer.start_as_current_span(f"business_{operation_name}", attributes=span_attributes) as span:
        try:
            yield span
        except Exception as e:
            span.record_exception(e)
            span.set_status(Status(StatusCode.ERROR, str(e)))
            raise


def _get_operation_category(operation_name: str) -> str:
    """Categorize operations for better observability"""
    categories = {
        "create_todo": "creation",
        "modify_todo": "modification", 
        "delete_todo": "deletion",
        "marked_as_done": "completion",
        "marked_as_uncompleted": "completion",
        "uncomplete_todo": "completion",
        "all_todos": "retrieval",
        "get_todo": "retrieval"
    }
    return categories.get(operation_name, "general")


def create_exemplar_histogram(name: str, description: str, unit: str = "s") -> Histogram:
    """Create a histogram that can generate exemplars"""
    meter = get_meter()
    return meter.create_histogram(name, description=description, unit=unit)


def create_exemplar_counter(name: str, description: str, unit: str = "1") -> Counter:
    """Create a counter that can generate exemplars"""  
    meter = get_meter()
    return meter.create_counter(name, description=description, unit=unit)


def record_exemplar_histogram(histogram: Histogram, value: float, attributes: dict, trace_id: str = None, span_id: str = None):
    """Record histogram with exemplar data"""
    if not trace_id:
        trace_id = get_current_trace_id()
    if not span_id:
        span_id = get_current_span_id()
    
    # Add trace context to attributes for exemplars
    if trace_id and span_id:
        exemplar_attributes = attributes.copy()
        exemplar_attributes.update({
            "trace_id": trace_id,
            "span_id": span_id
        })
        histogram.record(value, exemplar_attributes)
    else:
        histogram.record(value, attributes)


def record_exemplar_counter(counter: Counter, value: int, attributes: dict, trace_id: str = None, span_id: str = None):
    """Record counter with exemplar data"""
    if not trace_id:
        trace_id = get_current_trace_id()
    if not span_id:
        span_id = get_current_span_id()
    
    # Add trace context to attributes for exemplars
    if trace_id and span_id:
        exemplar_attributes = attributes.copy()
        exemplar_attributes.update({
            "trace_id": trace_id,
            "span_id": span_id
        })
        counter.add(value, exemplar_attributes)
    else:
        counter.add(value, attributes)


@contextmanager
def trace_todo_operation_with_exemplars(operation: str, todo_id: Optional[str] = None, **attributes):
    tracer = get_tracer()
    span_attributes = {
        "todo.operation": operation, 
        "service.name": "todo-api",
        "operation": operation  # Important for exemplars
    }
    if todo_id:
        span_attributes["todo.id"] = todo_id
    span_attributes.update(attributes)

    start_time = time.time()
    
    with tracer.start_as_current_span(f"todo_{operation}", attributes=span_attributes) as span:
        try:
            yield span
        except Exception as e:
            span.record_exception(e)
            span.set_status(Status(StatusCode.ERROR, str(e)))
            raise
        finally:
            # Record exemplar metrics
            duration = time.time() - start_time
            trace_id = get_current_trace_id()
            span_id = get_current_span_id()
            
            # Add exemplar data to span
            if trace_id and span_id:
                span.set_attribute("exemplar.trace_id", trace_id)
                span.set_attribute("exemplar.span_id", span_id)
                span.set_attribute("exemplar.duration", duration)


def add_business_labels(labels: Dict[str, str]):
    """Add business labels to current span and log structured event"""
    current_span = trace.get_current_span()
    if current_span and current_span.is_recording():
        for key, value in labels.items():
            current_span.set_attribute(f"business.{key}", value)
    
    operation = labels.get("operation", "unknown")
    status = labels.get("status", "success")
    
    # Use the enhanced log_todo_event function for consistency
    log_todo_event(
        operation=operation,
        status=status,
        details={"business_labels": labels, "source": "business_labels"}
    )


def add_business_labels_with_exemplars(labels: Dict[str, str]):
    """Add business labels with exemplar context"""
    current_span = trace.get_current_span()
    if current_span and current_span.is_recording():
        # Add trace context for exemplars
        trace_id = get_current_trace_id()
        span_id = get_current_span_id()
        
        for key, value in labels.items():
            current_span.set_attribute(f"business.{key}", value)
        
        # Add exemplar attributes
        if trace_id and span_id:
            current_span.set_attribute("exemplar.trace_id", trace_id)
            current_span.set_attribute("exemplar.span_id", span_id)
    
    operation = labels.get("operation", "unknown")
    status = labels.get("status", "success")
    
    # Use the enhanced log_todo_event function with exemplar context
    exemplar_details = {
        "business_labels": labels, 
        "source": "business_labels_exemplars"
    }
    
    trace_id = get_current_trace_id()
    span_id = get_current_span_id()
    if trace_id:
        exemplar_details["exemplar_trace_id"] = trace_id
    if span_id:
        exemplar_details["exemplar_span_id"] = span_id
    
    log_todo_event(
        operation=operation,
        status=status,
        details=exemplar_details
    )


# Utility function to ensure status is always set in log records
def ensure_status_label(record, default_status="unknown"):
    """Ensure every log record has a status for Loki filtering"""
    if not hasattr(record, "status") or not record.status:
        record.status = default_status
    return record


# Enhanced logging function for application startup/shutdown
def log_application_event(event: str, status: str = "success", details: Optional[Dict[str, Any]] = None):
    """Log application lifecycle events with proper status labels"""
    logger = logging.getLogger(__name__)
    
    extra_data = {
        "status": status,
        "operation": event,
        "service": "todo-api",
        "component": "application",
        "event_type": "lifecycle"
    }
    
    if details:
        extra_data.update(details)
    
    if status == "error":
        logger.error(f"Application {event}", extra=extra_data)
    elif status in ["warning", "warn"]:
        logger.warning(f"Application {event}", extra=extra_data)
    else:
        logger.info(f"Application {event}", extra=extra_data)