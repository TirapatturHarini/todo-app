import os
import logging
from typing import Dict, Any, Optional
from contextlib import contextmanager
from fastapi import FastAPI
import time
from opentelemetry import trace, metrics

try:
    from opentelemetry import _logs
except Exception:
    _logs = None

try:
    from opentelemetry.trace.status import Status, StatusCode
except Exception:
    class StatusCode:
        ERROR = "ERROR"

    class Status:
        def __init__(self, *args, **kwargs):
            pass


from statsd import StatsClient

from prometheus_client import Counter, start_http_server

# Expose HTTP metrics
start_http_server(9102)

REQUESTS = Counter("myapp_requests_total", "Total requests")

def record_request():
    REQUESTS.inc()


def init_telemetry(app: FastAPI):
    try:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
        from opentelemetry.instrumentation.requests import RequestsInstrumentor

        FastAPIInstrumentor().instrument_app(app)
        SQLAlchemyInstrumentor().instrument()
        RequestsInstrumentor().instrument()
    except Exception as exc:
        logging.getLogger(__name__).warning(
            "OpenTelemetry auto-instrumentation disabled due to import/runtime mismatch: %s",
            exc,
        )


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
        
        # FIXED: Don't override status if it's already properly set
        # Only set default if completely missing
        if not hasattr(record, "status"):
            record.status = "unknown"
        
        return True


class StatusPreservingFormatter(logging.Formatter):
    """Custom formatter that preserves status from extra data"""
    
    def format(self, record):
        # CRITICAL FIX: Extract status from extra data BEFORE formatting
        if hasattr(record, 'extra') and isinstance(record.extra, dict):
            # Copy all extra fields to record attributes for Loki labels
            for key, value in record.extra.items():
                setattr(record, key, value)
        
        # Ensure status is always present (fallback only)
        if not hasattr(record, 'status') or record.status in [None, ""]:
            record.status = "unknown"
        
        return super().format(record)


def setup_telemetry(app: FastAPI):
    service_name = os.getenv("SERVICE_NAME", "todo-api")
    service_version = os.getenv("SERVICE_VERSION", "1.0.0")
    environment = os.getenv("ENVIRONMENT", "development")
    otel_endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://otel-collector:4317")

    try:
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
        from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
        from opentelemetry.exporter.otlp.proto.grpc._log_exporter import OTLPLogExporter
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        from opentelemetry.sdk.metrics import MeterProvider
        from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.metrics.view import ExplicitBucketHistogramAggregation, View

        resource = Resource.create(
            {
                "service.name": service_name,
                "service.version": service_version,
                "deployment.environment": environment,
                "service.instance.id": os.getenv("HOSTNAME", "unknown"),
            }
        )

        trace.set_tracer_provider(TracerProvider(resource=resource))
        tracer_provider = trace.get_tracer_provider()
        otlp_span_exporter = OTLPSpanExporter(endpoint=otel_endpoint, insecure=True)
        tracer_provider.add_span_processor(BatchSpanProcessor(otlp_span_exporter))

        metric_reader = PeriodicExportingMetricReader(
            OTLPMetricExporter(endpoint=otel_endpoint, insecure=True),
            export_interval_millis=10000,
        )

        histogram_buckets = [0.005, 0.01, 0.025, 0.05, 0.075, 0.1, 0.25, 0.5, 0.75, 1.0, 2.5, 5.0, 7.5, 10.0]
        views = [
            View(
                instrument_name="http_request_duration_seconds",
                aggregation=ExplicitBucketHistogramAggregation(boundaries=histogram_buckets),
            ),
            View(
                instrument_name="todo_created_duration_seconds",
                aggregation=ExplicitBucketHistogramAggregation(boundaries=histogram_buckets),
            ),
            View(
                instrument_name="todo_updated_duration_seconds",
                aggregation=ExplicitBucketHistogramAggregation(boundaries=histogram_buckets),
            ),
            View(
                instrument_name="todo_deleted_duration_seconds",
                aggregation=ExplicitBucketHistogramAggregation(boundaries=histogram_buckets),
            ),
        ]

        metrics.set_meter_provider(
            MeterProvider(resource=resource, metric_readers=[metric_reader], views=views)
        )

        if _logs is not None:
            from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
            from opentelemetry.sdk._logs.export import BatchLogRecordProcessor

            _logs.set_logger_provider(LoggerProvider(resource=resource))
            logger_provider = _logs.get_logger_provider()
            otlp_log_exporter = OTLPLogExporter(endpoint=otel_endpoint, insecure=True)
            logger_provider.add_log_record_processor(BatchLogRecordProcessor(otlp_log_exporter))

            otel_handler = LoggingHandler(logger_provider=logger_provider)
            logging.getLogger().addHandler(otel_handler)

        trace_filter = TraceIdSpanIdFilter()
        for handler in logging.getLogger().handlers:
            handler.addFilter(trace_filter)

    except Exception as exc:
        logging.getLogger(__name__).warning(
            "OpenTelemetry SDK/exporter setup skipped due to version mismatch: %s",
            exc,
        )

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
    """FIXED: Enhanced logging function that ensures proper status labels for Loki"""
    logger = logging.getLogger(__name__)
    
    # Extract operation type for better categorization
    operation_type = details.get("operation", operation) if details else operation   
    message = f"Todo {operation_type}" + (f" (ID: {todo_id})" if todo_id else "")
    
    # CRITICAL FIX: Build comprehensive extra data for Loki labels
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
    
    # Merge additional details (don't override status)
    if details:
        details_copy = details.copy()
        # Preserve the main status
        if "status" in details_copy and details_copy["status"] != status:
            details_copy["details_status"] = details_copy.pop("status")
        extra_data.update(details_copy)
    
    # FIXED: Use LogRecord constructor properly to ensure extra data is preserved
    if status == "error":
        logger.error(message, extra=extra_data, stacklevel=2)
    elif status in ["warning", "warn"]:
        logger.warning(message, extra=extra_data, stacklevel=2)
    else:
        logger.info(message, extra=extra_data, stacklevel=2)


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


import time
from prometheus_client import Histogram, Counter, CollectorRegistry, generate_latest, CONTENT_TYPE_LATEST
from opentelemetry import trace

# Create custom registry for exemplars
exemplar_registry = CollectorRegistry()

# Define histograms with exemplar support
todo_duration_histogram = Histogram(
    'todo_created_duration_seconds',
    'Time to create todos',
    buckets=[0.005, 0.01, 0.025, 0.05, 0.075, 0.1, 0.25, 0.5, 0.75, 1.0, 2.5, 5.0, 7.5, 10.0],
    registry=exemplar_registry
)

http_request_histogram = Histogram(
    'http_request_duration_seconds', 
    'HTTP request duration',
    labelnames=['method', 'endpoint', 'status_code'],
    buckets=[0.005, 0.01, 0.025, 0.05, 0.075, 0.1, 0.25, 0.5, 0.75, 1.0, 2.5, 5.0, 7.5, 10.0],
    registry=exemplar_registry
)

todo_operations_counter = Counter(
    'todo_operations_total',
    'Total todo operations',
    labelnames=['operation', 'status'],
    registry=exemplar_registry
)

def record_histogram_with_exemplar(histogram, value, labels=None, exemplar_labels=None):
    """Record histogram with exemplar (trace context)"""
    # Get current trace context
    current_span = trace.get_current_span()
    if current_span and current_span.is_recording():
        span_context = current_span.get_span_context()
        trace_id = format(span_context.trace_id, "032x")
        span_id = format(span_context.span_id, "016x")
        
        # Create exemplar with trace context
        exemplar = {
            "traceID": trace_id,
            "spanID": span_id,
            **(exemplar_labels or {})
        }
        
        if labels:
            histogram.labels(**labels).observe(value, exemplar=exemplar)
        else:
            histogram.observe(value, exemplar=exemplar)
    else:
        # Fallback without exemplar
        if labels:
            histogram.labels(**labels).observe(value)
        else:
            histogram.observe(value)

def record_counter_with_exemplar(counter, value, labels=None, exemplar_labels=None):
    """Record counter with exemplar (trace context)"""
    current_span = trace.get_current_span()
    if current_span and current_span.is_recording():
        span_context = current_span.get_span_context()
        trace_id = format(span_context.trace_id, "032x")
        span_id = format(span_context.span_id, "016x")
        
        exemplar = {
            "traceID": trace_id,
            "spanID": span_id,
            **(exemplar_labels or {})
        }
        
        if labels:
            counter.labels(**labels).inc(value, exemplar=exemplar)
        else:
            counter.inc(value, exemplar=exemplar)
    else:
        if labels:
            counter.labels(**labels).inc(value)
        else:
            counter.inc(value)

# Expose metrics endpoint
def get_prometheus_metrics():
    """Return Prometheus metrics with exemplars"""
    return generate_latest(exemplar_registry)



def create_exemplar_histogram(name: str, description: str, unit: str = "s") -> Histogram:
    """Create a histogram that can generate exemplars"""
    meter = get_meter()
    return meter.create_histogram(name, description=description, unit=unit)


def create_exemplar_counter(name: str, description: str, unit: str = "1") -> Counter:
    """Create a counter that can generate exemplars"""  
    meter = get_meter()
    return meter.create_counter(name, description=description, unit=unit)


def record_exemplar_histogram(histogram: Histogram, value: float, attributes: dict, trace_id: str = None, span_id: str = None):
    """FIXED: Record histogram with proper exemplar context"""
    if not trace_id:
        trace_id = get_current_trace_id()
    if not span_id:
        span_id = get_current_span_id()
    metric_name = getattr(histogram, "name", None)

    # CRITICAL: OpenTelemetry Python SDK requires exemplar context to be set on the current span
    current_span = trace.get_current_span()
    if current_span and current_span.is_recording() and trace_id and span_id:
        # Set exemplar-specific attributes on the span for OTEL correlation
        current_span.set_attribute("exemplar.metric_name", metric_name)
        current_span.set_attribute("exemplar.metric_value", value)
        current_span.set_attribute("exemplar.metric_type", "histogram")
        
        # Merge attributes for recording
        recording_attributes = attributes.copy()
        # Don't add trace/span to metric attributes as they're automatically captured by OTEL
        
        # Record the histogram - OTEL SDK will automatically correlate with current span for exemplars
        histogram.record(value, recording_attributes)
    else:
        # Fallback - record without exemplar context
        histogram.record(value, attributes)


def record_exemplar_counter(counter: Counter, value: int, attributes: dict, trace_id: str = None, span_id: str = None):
    """FIXED: Record counter with proper exemplar context"""
    if not trace_id:
        trace_id = get_current_trace_id()
    if not span_id:
        span_id = get_current_span_id()
    
    # CRITICAL: OpenTelemetry Python SDK requires exemplar context to be set on the current span
    current_span = trace.get_current_span()
    metric_name = getattr(counter, "name", None)
    if current_span and current_span.is_recording() and trace_id and span_id:
        # Set exemplar-specific attributes on the span for OTEL correlation
        current_span.set_attribute("exemplar.metric_name", metric_name)
        current_span.set_attribute("exemplar.metric_value", value)
        current_span.set_attribute("exemplar.metric_type", "counter")
        
        # Merge attributes for recording
        recording_attributes = attributes.copy()
        # Don't add trace/span to metric attributes as they're automatically captured by OTEL
        
        # Record the counter - OTEL SDK will automatically correlate with current span for exemplars
        counter.add(value, recording_attributes)
    else:
        # Fallback - record without exemplar context
        counter.add(value, attributes)


@contextmanager
def trace_todo_operation_with_exemplars(operation: str, todo_id: Optional[str] = None, **attributes):
    """FIXED: Enhanced tracing that ensures proper exemplar correlation"""
    tracer = get_tracer()
    span_attributes = {
        "todo.operation": operation, 
        "service.name": "todo-api",
        "operation": operation,  # Important for exemplars
        "operation.type": _get_operation_category(operation)
    }
    if todo_id:
        span_attributes["todo.id"] = todo_id
    span_attributes.update(attributes)

    start_time = time.time()
    
    with tracer.start_as_current_span(f"todo_{operation}", attributes=span_attributes) as span:
        try:
            # Set exemplar correlation attributes on span
            trace_id = get_current_trace_id()
            span_id = get_current_span_id()
            if trace_id and span_id:
                span.set_attribute("exemplar.operation", operation)
                span.set_attribute("exemplar.trace_id", trace_id)
                span.set_attribute("exemplar.span_id", span_id)
            
            yield span
        except Exception as e:
            span.record_exception(e)
            span.set_status(Status(StatusCode.ERROR, str(e)))
            raise
        finally:
            # Add final exemplar timing
            duration = time.time() - start_time
            span.set_attribute("exemplar.duration", duration)


def add_business_labels(labels: Dict[str, str]):
    """FIXED: Add business labels to current span and log structured event"""
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
        details={
            "business_labels": labels, 
            "source": "business_labels",
            # Include all business labels as top-level fields for Loki
            **{f"business_{k}": v for k, v in labels.items()}
        }
    )


def add_business_labels_with_exemplars(labels: Dict[str, str]):
    """FIXED: Add business labels with proper exemplar context"""
    current_span = trace.get_current_span()
    if current_span and current_span.is_recording():
        # Add trace context for exemplars
        trace_id = get_current_trace_id()
        span_id = get_current_span_id()
        
        for key, value in labels.items():
            current_span.set_attribute(f"business.{key}", value)
        
        # CRITICAL: Add exemplar correlation attributes
        if trace_id and span_id:
            current_span.set_attribute("exemplar.business_operation", labels.get("operation", "unknown"))
            current_span.set_attribute("exemplar.business_status", labels.get("status", "success"))
            current_span.set_attribute("exemplar.trace_id", trace_id)
            current_span.set_attribute("exemplar.span_id", span_id)
            current_span.set_attribute("exemplar.timestamp", time.time())
    
    operation = labels.get("operation", "unknown")
    status = labels.get("status", "success")
    
    # Use the enhanced log_todo_event function with exemplar context
    exemplar_details = {
        "business_labels": labels, 
        "source": "business_labels_exemplars",
        # Include all business labels as top-level fields for Loki
        **{f"business_{k}": v for k, v in labels.items()}
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


# FIXED: Enhanced logging function for application startup/shutdown
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
        logger.error(f"Application {event}", extra=extra_data, stacklevel=2)
    elif status in ["warning", "warn"]:
        logger.warning(f"Application {event}", extra=extra_data, stacklevel=2)
    else:
        logger.info(f"Application {event}", extra=extra_data, stacklevel=2)