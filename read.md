import os
import logging
import time
from contextlib import asynccontextmanager
from typing import List

from fastapi import FastAPI, HTTPException, Depends, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.orm import Session
from fastapi.responses import Response
from prometheus_client import CONTENT_TYPE_LATEST


from .telemetry import (
    setup_telemetry, get_tracer, get_meter, add_business_labels,
    trace_todo_operation, log_todo_event, get_current_trace_id,
    create_exemplar_histogram, create_exemplar_counter,
    record_exemplar_histogram, record_exemplar_counter,
    trace_todo_operation_with_exemplars, add_business_labels_with_exemplars,
    log_application_event, StatusPreservingFormatter,
    record_histogram_with_exemplar, record_counter_with_exemplar,
    todo_duration_histogram, http_request_histogram, todo_operations_counter,
    get_prometheus_metrics
)
from .database import create_tables, get_db, TodoDB
from .models import TodoCreate, TodoUpdate, TodoResponse, TodoBase



def setup_logging():
    """FIXED: Setup logging with proper status preservation for Loki"""
    # Clear any existing handlers first
    root_logger = logging.getLogger()
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)
    
    # FIXED: Create the StatusPreservingFormatter that properly handles extra data
    formatter = StatusPreservingFormatter(
        "%(asctime)s %(levelname)s [trace_id=%(otelTraceID)s span_id=%(otelSpanID)s status=%(status)s] %(message)s"
    )
    
    # Setup handlers with the custom formatter
    handlers = []
    
    # Console handler
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    handlers.append(console_handler)
    
    # File handler (if /tmp exists)
    if os.path.exists("/tmp"):
        file_handler = logging.FileHandler("/tmp/app.log")
        file_handler.setFormatter(formatter)
        handlers.append(file_handler)
    
    # Configure root logger
    logging.basicConfig(
        level=logging.INFO,
        handlers=handlers,
        force=True
    )
    
    return logging.getLogger(__name__)

# Setup logging ONCE
logger = setup_logging()

# Lifespan handler with proper status logging
@asynccontextmanager
async def lifespan(app: FastAPI):
    log_application_event("startup", "starting", {"version": "1.0.0"})
    try:
        create_tables()
        log_application_event("database_initialization", "success", {"message": "Database tables created/verified"})
    except Exception as e:
        log_application_event("database_initialization", "error", {"error": str(e)})
        # Don't raise here if you want the app to continue without DB
    yield
    log_application_event("shutdown", "success", {"message": "Application shutdown completed"})


# FastAPI app
app = FastAPI(
    title="Todo API with OpenTelemetry",
    description="A simple todo API with full observability",
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
)

# Middleware
app.add_middleware(TrustedHostMiddleware, allowed_hosts=["*"])
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # adjust in prod
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Telemetry setup
setup_telemetry(app)
tracer = get_tracer()
meter = get_meter()

# Metrics
# EXEMPLAR-ENABLED METRICS
todo_counter = create_exemplar_counter("todos_total", "Total todos created")
todo_operations = create_exemplar_counter("todo_operations_total", "Total todo operations") 
request_duration = create_exemplar_histogram("http_request_duration_seconds", "HTTP request duration", "s")

# New exemplar metrics for specific operations
todo_created_duration = create_exemplar_histogram("todo_created_duration_seconds", "Time to create todos", "s")
todo_updated_duration = create_exemplar_histogram("todo_updated_duration_seconds", "Time to update todos", "s")
todo_deleted_duration = create_exemplar_histogram("todo_deleted_duration_seconds", "Time to delete todos", "s")


@app.get("/metrics/prometheus")
async def prometheus_metrics():
    """Expose Prometheus metrics with exemplars - for direct scraping"""
    return Response(content=get_prometheus_metrics(), media_type=CONTENT_TYPE_LATEST)


# FIXED: Middleware to measure request time and add trace context
@app.middleware("http")
async def add_process_time_header(request: Request, call_next):
    start = time.time()  
    try:
        response = await call_next(request)
        elapsed = time.time() - start
        response.headers["X-Process-Time"] = f"{elapsed:.6f}"
        
        # Add trace ID to response headers
        trace_id = get_current_trace_id()
        if trace_id:
            response.headers["X-Trace-ID"] = trace_id
        
        # Log request completion with proper status
        status = "success" if 200 <= response.status_code < 400 else "error"
        
        # FIXED: Log with structured data for Loki - ensure proper extra data handling
        logger.info(
            f"HTTP {request.method} {request.url.path} - {response.status_code}",
            extra={
                "status": status,
                "operation": "http_request",
                "method": request.method,
                "path": request.url.path,
                "status_code": response.status_code,
                "duration": elapsed,
                "trace_id": trace_id,
                "component": "middleware"
            }
        )
        
        
        # RECORD EXEMPLAR METRICS
        record_histogram_with_exemplar(
            http_request_histogram, 
            elapsed, 
            labels={
                "method": request.method,
                "endpoint": request.url.path,
                "status_code": str(response.status_code)
            },
            exemplar_labels={"endpoint": request.url.path}
        )
        
        return response
        
        
    except Exception as e:
        elapsed = time.time() - start
        logger.error(f"HTTP {request.method} {request.url.path} - Middleware Error", ...)        
        raise

        
        # FIXED: Log middleware error with proper status
        logger.error(
            f"HTTP {request.method} {request.url.path} - Middleware Error",
            extra={
                "status": "error",
                "operation": "http_request",
                "method": request.method,
                "path": request.url.path,
                "duration": elapsed,
                "error": str(e),
                "component": "middleware"
            },
            exc_info=True
        )        
        raise


# --- Health & Readiness ---
@app.get("/health")
async def health_check(db: Session = Depends(get_db)):
    """Health check endpoint"""
    with trace_todo_operation("health_check"):
        logger.info("Health check requested", extra={"status": "checking", "operation": "health_check"})
        
        status = {
            "status": "healthy",
            "service": "todo-api",
            "version": "1.0.0",
            "timestamp": time.time(),
        }

        try:
            db.execute(text("SELECT 1"))
            status["database"] = "connected"
            add_business_labels({"status": "healthy", "operation": "health_check"})
            
            logger.info("Health check completed successfully", 
                       extra={"status": "success", "operation": "health_check", "db_status": "connected"})
        except Exception as e:
            logger.error("Database health check failed", 
                        extra={"status": "error", "operation": "health_check", "error": str(e)}, 
                        exc_info=True)
            status["database"] = "disconnected"
            status["status"] = "degraded"
            add_business_labels({"status": "degraded", "operation": "health_check"})

        # Add trace ID to response
        trace_id = get_current_trace_id()
        if trace_id:
            status["trace_id"] = trace_id

        return status

@app.get("/ready")
async def readiness_check(db: Session = Depends(get_db)):
    """Readiness check for Kubernetes"""
    with trace_todo_operation("readiness_check"):
        try:
            db.execute(text("SELECT 1"))
            add_business_labels({"status": "ready", "operation": "readiness_check"})
            
            logger.info("Readiness check passed", 
                       extra={"status": "success", "operation": "readiness_check"})
            
            return {"status": "ready"}
        except Exception as e:
            logger.error("Readiness check failed", 
                        extra={"status": "error", "operation": "readiness_check", "error": str(e)}, 
                        exc_info=True)
            add_business_labels({"status": "not_ready", "operation": "readiness_check"})
            raise HTTPException(status_code=503, detail="Service not ready")


# --- CRUD Endpoints ---

@app.post("/todos/{todo_id}/uncomplete")
async def uncomplete_todo(todo_id: int, db: Session = Depends(get_db)):
    """Mark a todo as uncompleted - dedicated endpoint for better tracing"""
    with trace_todo_operation("uncomplete_todo", todo_id=str(todo_id)):
        logger.info(
            f"Uncompleting todo with ID: {todo_id}",
            extra={
                "status": "processing",
                "operation": "uncomplete_todo",
                "todo_id": todo_id,
                "component": "todo_handler"
            }
        )
        
        try:
            db_todo = db.query(TodoDB).filter(TodoDB.id == todo_id).first()
            
            if not db_todo:
                logger.warning(
                    f"Todo not found for uncompleting: {todo_id}",
                    extra={
                        "status": "not_found",
                        "operation": "uncomplete_todo",
                        "todo_id": todo_id,
                        "component": "todo_handler"
                    }
                )
                log_todo_event("uncomplete_todo", todo_id=str(todo_id), status="not_found")
                add_business_labels({"status": "not_found", "operation": "uncomplete_todo"})
                raise HTTPException(status_code=404, detail="Todo not found")

            if not db_todo.completed:
                logger.info(
                    f"Todo {todo_id} already uncompleted",
                    extra={
                        "status": "already_uncompleted",
                        "operation": "uncomplete_todo",
                        "todo_id": todo_id,
                        "title": db_todo.title,
                        "component": "todo_handler"
                    }
                )
                log_todo_event("uncomplete_todo", todo_id=str(todo_id), status="already_uncompleted")
                add_business_labels({"status": "already_uncompleted", "operation": "uncomplete_todo"})
                return {"message": "Todo already uncompleted", "id": todo_id}

            db_todo.completed = False
            db.commit()
            db.refresh(db_todo)
            
            todo_operations.add(1, {"operation": "uncomplete"})
            add_business_labels({"status": "success", "operation": "uncomplete_todo"})
            
            log_todo_event("uncomplete_todo", todo_id=str(todo_id), status="success",
                          details={"title": db_todo.title, "operation": "uncompleted"})
            
            logger.info(
                f"Todo {todo_id} uncompleted successfully",
                extra={
                    "status": "success",
                    "operation": "uncomplete_todo",
                    "todo_id": todo_id,
                    "title": db_todo.title,
                    "component": "todo_handler"
                }
            )
            
            return {
                "message": "Todo marked as uncompleted successfully",
                "id": todo_id,
                "title": db_todo.title,
                "completed": False,
                "trace_id": get_current_trace_id()
            }
            
        except HTTPException:
            raise
        except Exception as e:
            db.rollback()
            logger.error(
                f"Error uncompleting todo {todo_id}",
                extra={
                    "status": "error",
                    "operation": "uncomplete_todo",
                    "todo_id": todo_id,
                    "error": str(e),
                    "component": "todo_handler"
                },
                exc_info=True
            )
            add_business_labels({"status": "error", "operation": "uncomplete_todo"})
            log_todo_event("uncomplete_todo", todo_id=str(todo_id), status="error",
                          details={"error": str(e), "operation": "uncomplete_todo"})
            raise HTTPException(status_code=500, detail="Internal server error")


@app.post("/todos", response_model=TodoResponse, status_code=201)
async def create_todo(todo: TodoCreate, db: Session = Depends(get_db)):
    start_time = time.time()
    
    with trace_todo_operation_with_exemplars("create_todo", title=todo.title):
        logger.info(
            f"Creating new todo: {todo.title}", 
            extra={
                "status": "processing",
                "operation": "create_todo",
                "title": todo.title,
                "component": "todo_handler"
            }
        )
        
        try:
            db_todo = TodoDB(title=todo.title, description=todo.description)
            db.add(db_todo)
            db.commit()
            db.refresh(db_todo)
            
            # FIXED: Record exemplars with direct Prometheus
            duration = time.time() - start_time
            
            # Record histogram with exemplar
            record_histogram_with_exemplar(
                todo_duration_histogram, 
                duration,
                exemplar_labels={"operation": "create", "todo_id": str(db_todo.id)}
            )
            
            # Record counter with exemplar  
            record_counter_with_exemplar(
                todo_operations_counter,
                1,
                labels={"operation": "create", "status": "success"},
                exemplar_labels={"todo_title": todo.title[:20]}  # Truncate for exemplar
            )
            
            add_business_labels_with_exemplars({"status": "success", "operation": "create_todo"})
            
            log_todo_event("create_todo", todo_id=str(db_todo.id), status="success",
                          details={"title": todo.title, "description": todo.description, "operation": "created_todo"})
            
            logger.info(
                f"Todo created successfully with ID: {db_todo.id}",
                extra={
                    "status": "success",
                    "operation": "create_todo",
                    "todo_id": db_todo.id,
                    "title": todo.title,
                    "duration": duration,
                    "component": "todo_handler"
                }
            )
            
            return db_todo
        except Exception as e:
            # Record error exemplar
            duration = time.time() - start_time
            record_histogram_with_exemplar(
                todo_duration_histogram, 
                duration,
                exemplar_labels={"operation": "create_error", "error": str(e)[:50]}
            )
            record_counter_with_exemplar(
                todo_operations_counter,
                1,
                labels={"operation": "create", "status": "error"}
            )
            
            logger.error(
                f"Error creating todo: {todo.title}",
                extra={
                    "status": "error",
                    "operation": "create_todo",
                    "title": todo.title,
                    "error": str(e),
                    "duration": duration,
                    "component": "todo_handler"
                },
                exc_info=True
            )
            
            add_business_labels_with_exemplars({"status": "error", "operation": "create_todo"})
            log_todo_event("create_todo", status="error", details={"error": str(e), "operation": "create_todo"})
            raise HTTPException(status_code=500, detail="Internal server error")


@app.get("/todos", response_model=List[TodoResponse])
async def get_todos(skip: int = 0, limit: int = 100, db: Session = Depends(get_db)):
    with trace_todo_operation("get_todos", skip=skip, limit=limit):
        logger.info(
            f"Fetching todos with skip={skip}, limit={limit}",
            extra={
                "status": "processing",
                "operation": "get_todos",
                "skip": skip,
                "limit": limit,
                "component": "todo_handler"
            }
        )
        
        # Validation with proper error logging
        if skip < 0:
            logger.error(
                "Invalid skip parameter provided",
                extra={
                    "status": "error",
                    "operation": "get_todos",
                    "skip": skip,
                    "error": "Invalid skip parameter",
                    "component": "todo_handler"
                }
            )
            log_todo_event("get_todos", status="error", 
                          details={"error": "Invalid skip parameter", "operation": "all_todos"})
            raise HTTPException(status_code=400, detail="Skip must be non-negative")
        
        if limit <= 0 or limit > 1000:
            logger.error(
                "Invalid limit parameter provided",
                extra={
                    "status": "error",
                    "operation": "get_todos",
                    "limit": limit,
                    "error": "Invalid limit parameter",
                    "component": "todo_handler"
                }
            )
            log_todo_event("get_todos", status="error", 
                          details={"error": "Invalid limit parameter", "operation": "all_todos"})
            raise HTTPException(status_code=400, detail="Limit must be between 1 and 1000")

        try:
            todos = db.query(TodoDB).offset(skip).limit(limit).all()
            
            todo_operations.add(1, {"operation": "read_all", "count": len(todos)})
            add_business_labels({"status": "success", "operation": "get_todos"})
            
            log_todo_event("get_todos", status="success", 
                          details={"count": len(todos), "skip": skip, "limit": limit, "operation": "all_todos"})
            
            logger.info(
                f"Fetched {len(todos)} todos successfully",
                extra={
                    "status": "success",
                    "operation": "get_todos",
                    "count": len(todos),
                    "skip": skip,
                    "limit": limit,
                    "component": "todo_handler"
                }
            )
            
            return todos
            
        except Exception as e:
            logger.error(
                "Error fetching todos from database",
                extra={
                    "status": "error",
                    "operation": "get_todos",
                    "skip": skip,
                    "limit": limit,
                    "error": str(e),
                    "component": "todo_handler"
                },
                exc_info=True
            )        
            add_business_labels({"status": "error", "operation": "get_todos"})
            log_todo_event("get_todos", status="error", details={"error": str(e), "operation": "all_todos"})
            raise HTTPException(status_code=500, detail="Internal server error")


@app.put("/todos/{todo_id}", response_model=TodoResponse)
async def update_todo(todo_id: int, todo_update: TodoUpdate, db: Session = Depends(get_db)):
    start_time = time.time()
    
    with trace_todo_operation_with_exemplars("update_todo", todo_id=str(todo_id)):
        logger.info(
            f"Updating todo with ID: {todo_id}",
            extra={
                "status": "processing",
                "operation": "update_todo",
                "todo_id": todo_id,
                "component": "todo_handler"
            }
        )
        
        try:
            db_todo = db.query(TodoDB).filter(TodoDB.id == todo_id).first()
            
            if not db_todo:
                logger.warning(
                    f"Todo not found for update: {todo_id}",
                    extra={
                        "status": "not_found",
                        "operation": "update_todo",
                        "todo_id": todo_id,
                        "component": "todo_handler"
                    }
                )
                log_todo_event("update_todo", todo_id=str(todo_id), status="not_found")
                add_business_labels_with_exemplars({"status": "not_found", "operation": "update_todo"})
                raise HTTPException(status_code=404, detail="Todo not found")

            updated = False
            update_details = {"operation": "modify_todo"}
            operation_type = "modify_todo"
            
            # Track what's being updated
            updates = []
            if todo_update.title is not None:
                db_todo.title = todo_update.title
                update_details["title"] = todo_update.title
                updates.append("title")
                updated = True
            if todo_update.description is not None:
                db_todo.description = todo_update.description
                update_details["description"] = todo_update.description
                updates.append("description")
                updated = True
            if todo_update.completed is not None:
                db_todo.completed = todo_update.completed
                update_details["completed"] = todo_update.completed
                updates.append("completed")
                updated = True
                operation_type = "marked_as_done" if todo_update.completed else "marked_as_uncompleted"
                update_details["operation"] = operation_type

            if not updated:
                logger.warning(
                    f"No fields to update for todo: {todo_id}",
                    extra={
                        "status": "no_changes",
                        "operation": "update_todo",
                        "todo_id": todo_id,
                        "error": "No fields to update",
                        "component": "todo_handler"
                    }
                )
                log_todo_event("update_todo", todo_id=str(todo_id), status="error",
                              details={"error": "No fields to update", "operation": "modify_todo"})
                add_business_labels_with_exemplars({"status": "no_changes", "operation": "update_todo"})
                raise HTTPException(status_code=400, detail="No fields to update")

            db.commit()
            db.refresh(db_todo)
            
            # RECORD EXEMPLAR METRICS
            duration = time.time() - start_time
            record_exemplar_counter(todo_operations, 1, {"operation": operation_type})
            record_exemplar_histogram(todo_updated_duration, duration, {
                "operation": operation_type,
                "status": "success"
            })
            
            add_business_labels_with_exemplars({"status": "success", "operation": operation_type})
            
            log_todo_event("update_todo", todo_id=str(todo_id), status="success",
                          details=update_details)
            
            logger.info(
                f"Todo {todo_id} updated successfully",
                extra={
                    "status": "success",
                    "operation": "update_todo",
                    "todo_id": todo_id,
                    "operation_type": operation_type,
                    "updated_fields": updates,
                    "duration": duration,
                    "title": db_todo.title,
                    "completed": db_todo.completed,
                    "component": "todo_handler"
                }
            )
            
            return db_todo
            
        except HTTPException:
            raise
        except Exception as e:
            db.rollback()
            duration = time.time() - start_time
            record_exemplar_histogram(todo_updated_duration, duration, {
                "operation": operation_type if 'operation_type' in locals() else "update_todo",
                "status": "error"
            })
            
            logger.error(
                f"Error updating todo {todo_id}",
                extra={
                    "status": "error",
                    "operation": "update_todo",
                    "todo_id": todo_id,
                    "error": str(e),
                    "duration": duration,
                    "component": "todo_handler"
                },
                exc_info=True
            )
            
            add_business_labels_with_exemplars({"status": "error", "operation": "update_todo"})
            log_todo_event("update_todo", todo_id=str(todo_id), status="error",
                          details={"error": str(e), "operation": "update_todo"})
            raise HTTPException(status_code=500, detail="Internal server error")


@app.delete("/todos/{todo_id}")
async def delete_todo(todo_id: int, db: Session = Depends(get_db)):
    start_time = time.time()
    
    with trace_todo_operation_with_exemplars("delete_todo", todo_id=str(todo_id)):
        logger.info(
            f"Deleting todo with ID: {todo_id}",
            extra={
                "status": "processing",
                "operation": "delete_todo",
                "todo_id": todo_id,
                "component": "todo_handler"
            }
        )
        
        try:
            db_todo = db.query(TodoDB).filter(TodoDB.id == todo_id).first()
            
            if not db_todo:
                logger.warning(
                    f"Todo not found for deletion: {todo_id}",
                    extra={
                        "status": "not_found",
                        "operation": "delete_todo",
                        "todo_id": todo_id,
                        "component": "todo_handler"
                    }
                )
                log_todo_event("delete_todo", todo_id=str(todo_id), status="not_found")
                add_business_labels_with_exemplars({"status": "not_found", "operation": "delete_todo"})
                raise HTTPException(status_code=404, detail="Todo not found")

            title = db_todo.title
            completed = db_todo.completed
            
            db.delete(db_todo)
            db.commit()
            
            # RECORD EXEMPLAR METRICS
            duration = time.time() - start_time
            record_exemplar_counter(todo_operations, 1, {"operation": "delete"})
            record_exemplar_histogram(todo_deleted_duration, duration, {
                "operation": "delete_todo",
                "status": "success"
            })
            
            add_business_labels_with_exemplars({"status": "success", "operation": "delete_todo"})
            
            log_todo_event("delete_todo", todo_id=str(todo_id), status="success",
                          details={"title": title, "operation": "deleted_todo"})
            
            logger.info(
                f"Todo {todo_id} deleted successfully",
                extra={
                    "status": "success",
                    "operation": "delete_todo",
                    "todo_id": todo_id,
                    "title": title,
                    "was_completed": completed,
                    "duration": duration,
                    "component": "todo_handler"
                }
            )
            
            return {
                "message": "Todo deleted successfully", 
                "id": todo_id, 
                "title": title,
                "trace_id": get_current_trace_id()
            }
            
        except HTTPException:
            raise
        except Exception as e:
            db.rollback()
            duration = time.time() - start_time
            record_exemplar_histogram(todo_deleted_duration, duration, {
                "operation": "delete_todo",
                "status": "error"
            })
            
            logger.error(
                f"Error deleting todo {todo_id}",
                extra={
                    "status": "error",
                    "operation": "delete_todo",
                    "todo_id": todo_id,
                    "error": str(e),
                    "duration": duration,
                    "component": "todo_handler"
                },
                exc_info=True
            )
            
            add_business_labels_with_exemplars({"status": "error", "operation": "delete_todo"})
            log_todo_event("delete_todo", todo_id=str(todo_id), status="error",
                          details={"error": str(e), "operation": "delete_todo"})
            raise HTTPException(status_code=500, detail="Internal server error")


@app.post("/todos/{todo_id}/complete")
async def complete_todo(todo_id: int, db: Session = Depends(get_db)):
    """Complete a specific todo - dedicated endpoint for better tracing"""
    with trace_todo_operation("complete_todo", todo_id=str(todo_id)):
        logger.info(
            f"Completing todo with ID: {todo_id}",
            extra={
                "status": "processing",
                "operation": "complete_todo",
                "todo_id": todo_id,
                "component": "todo_handler"
            }
        )
        
        try:
            db_todo = db.query(TodoDB).filter(TodoDB.id == todo_id).first()
            
            if not db_todo:
                logger.warning(
                    f"Todo not found for completion: {todo_id}",
                    extra={
                        "status": "not_found",
                        "operation": "complete_todo",
                        "todo_id": todo_id,
                        "component": "todo_handler"
                    }
                )
                log_todo_event("complete_todo", todo_id=str(todo_id), status="not_found")
                add_business_labels({"status": "not_found", "operation": "complete_todo"})
                raise HTTPException(status_code=404, detail="Todo not found")

            if db_todo.completed:
                logger.info(
                    f"Todo {todo_id} already completed",
                    extra={
                        "status": "already_completed",
                        "operation": "complete_todo",
                        "todo_id": todo_id,
                        "title": db_todo.title,
                        "component": "todo_handler"
                    }
                )
                log_todo_event("complete_todo", todo_id=str(todo_id), status="already_completed")
                add_business_labels({"status": "already_completed", "operation": "complete_todo"})
                return {"message": "Todo already completed", "id": todo_id}

            db_todo.completed = True
            db.commit()
            db.refresh(db_todo)
            
            todo_operations.add(1, {"operation": "complete"})
            add_business_labels({"status": "success", "operation": "complete_todo"})
            
            log_todo_event("complete_todo", todo_id=str(todo_id), status="success",
                          details={"title": db_todo.title, "operation": "marked_as_done"})
            
            logger.info(
                f"Todo {todo_id} completed successfully",
                extra={
                    "status": "success",
                    "operation": "complete_todo",
                    "todo_id": todo_id,
                    "title": db_todo.title,
                    "component": "todo_handler"
                }
            )
            
            return {
                "message": "Todo completed successfully",
                "id": todo_id,
                "title": db_todo.title,
                "trace_id": get_current_trace_id()
            }
            
        except HTTPException:
            raise
        except Exception as e:
            db.rollback()
            logger.error(
                f"Error completing todo {todo_id}",
                extra={
                    "status": "error",
                    "operation": "complete_todo",
                    "todo_id": todo_id,
                    "error": str(e),
                    "component": "todo_handler"
                },
                exc_info=True
            )
            add_business_labels({"status": "error", "operation": "complete_todo"})
            log_todo_event("complete_todo", todo_id=str(todo_id), status="error",
                          details={"error": str(e), "operation": "complete_todo"})
            raise HTTPException(status_code=500, detail="Internal server error")


@app.get("/todos/{todo_id}", response_model=TodoResponse)
async def get_todo(todo_id: int, db: Session = Depends(get_db)):
    with trace_todo_operation("get_todo", todo_id=str(todo_id)):
        logger.info(
            f"Fetching todo with ID: {todo_id}",
            extra={
                "status": "processing",
                "operation": "get_todo",
                "todo_id": todo_id,
                "component": "todo_handler"
            }
        )
        
        # Validation
        if todo_id <= 0:
            logger.error(
                f"Invalid todo ID provided: {todo_id}",
                extra={
                    "status": "error",
                    "operation": "get_todo",
                    "todo_id": todo_id,
                    "error": "Invalid todo ID",
                    "component": "todo_handler"
                }
            )
            log_todo_event("get_todo", todo_id=str(todo_id), status="error",
                          details={"error": "Invalid todo ID"})
            raise HTTPException(status_code=400, detail="Todo ID must be positive")

        try:
            todo = db.query(TodoDB).filter(TodoDB.id == todo_id).first()
            
            if not todo:
                logger.warning(
                    f"Todo not found with ID: {todo_id}",
                    extra={
                        "status": "not_found",
                        "operation": "get_todo",
                        "todo_id": todo_id,
                        "component": "todo_handler"
                    }
                )
                log_todo_event("get_todo", todo_id=str(todo_id), status="not_found")
                add_business_labels({"status": "not_found", "operation": "get_todo"})
                raise HTTPException(status_code=404, detail="Todo not found")

            add_business_labels({"status": "success", "operation": "get_todo"})
            todo_operations.add(1, {"operation": "read_single"})
            
            log_todo_event("get_todo", todo_id=str(todo_id), status="success",
                          details={"title": todo.title, "completed": todo.completed})
            
            logger.info(
                f"Todo fetched successfully: {todo_id}",
                extra={
                    "status": "success",
                    "operation": "get_todo",
                    "todo_id": todo_id,
                    "title": todo.title,
                    "completed": todo.completed,
                    "component": "todo_handler"
                }
            )
            
            return todo
            
        except HTTPException:
            raise
        except Exception as e:
            logger.error(
                f"Error fetching todo {todo_id}",
                extra={
                    "status": "error",
                    "operation": "get_todo",
                    "todo_id": todo_id,
                    "error": str(e),
                    "component": "todo_handler"
                },
                exc_info=True
            )
            
            add_business_labels({"status": "error", "operation": "get_todo"})
            log_todo_event("get_todo", todo_id=str(todo_id), status="error", details={"error": str(e)})
            raise HTTPException(status_code=500, detail="Internal server error")


@app.get("/todos/{todo_id}/trace")
async def get_todo_trace_info(todo_id: int):
    """Get trace information for a todo operation"""
    with trace_todo_operation("get_trace_info", todo_id=str(todo_id)):
        logger.info(
            f"Getting trace info for todo: {todo_id}",
            extra={
                "status": "processing",
                "operation": "get_trace_info",
                "todo_id": todo_id,
                "component": "trace_handler"
            }
        )
        
        try:
            trace_id = get_current_trace_id()
            add_business_labels({"status": "success", "operation": "get_trace_info"})
            
            log_todo_event("get_trace_info", todo_id=str(todo_id), status="success",
                          details={"trace_id": trace_id})
            
            logger.info(
                f"Trace info retrieved for todo: {todo_id}",
                extra={
                    "status": "success",
                    "operation": "get_trace_info",
                    "todo_id": todo_id,
                    "trace_id": trace_id,
                    "component": "trace_handler"
                }
            )
            
            return {
                "todo_id": todo_id,
                "trace_id": trace_id,
                "tempo_url": f"http://localhost:3001/explore?orgId=1&left=%7B%22datasource%22:%22tempo-uid%22,%22queries%22:%5B%7B%22refId%22:%22A%22,%22queryType%22:%22traceId%22,%22query%22:%22{trace_id}%22%7D%5D,%22range%22:%7B%22from%22:%22now-1h%22,%22to%22:%22now%22%7D%7D" if trace_id else None
            }
            
        except Exception as e:
            logger.error(
                f"Error getting trace info for todo: {todo_id}",
                extra={
                    "status": "error",
                    "operation": "get_trace_info",
                    "todo_id": todo_id,
                    "error": str(e),
                    "component": "trace_handler"
                },
                exc_info=True
            )
            add_business_labels({"status": "error", "operation": "get_trace_info"})
            raise HTTPException(status_code=500, detail="Internal server error")


# --- Global Exception Handler ---
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    trace_id = get_current_trace_id()
    
    logger.error(
        f"Unhandled exception in {request.method} {request.url.path}",
        extra={
            "status": "error",
            "operation": "global_exception",
            "method": request.method,
            "path": request.url.path,
            "exception_type": type(exc).__name__,
            "trace_id": trace_id,
            "component": "exception_handler"
        },
        exc_info=True
    )
    
    add_business_labels({"status": "system_error", "operation": "global_exception"})
    
    return JSONResponse(
        status_code=500,
        content={
            "detail": "Internal server error",
            "type": type(exc).__name__,
            "path": str(request.url.path),
            "trace_id": trace_id,
        },
    )


@app.get("/metrics")
async def metrics_info():
    """Get metrics information"""
    with trace_todo_operation("get_metrics_info"):
        logger.info(
            "Getting metrics information",
            extra={
                "status": "processing",
                "operation": "get_metrics_info",
                "component": "metrics_handler"
            }
        )
        
        try:
            trace_id = get_current_trace_id()
            add_business_labels({"status": "success", "operation": "get_metrics_info"})
            log_todo_event("get_metrics_info", status="success")
            
            logger.info(
                "Metrics information retrieved successfully",
                extra={
                    "status": "success",
                    "operation": "get_metrics_info",
                    "trace_id": trace_id,
                    "component": "metrics_handler"
                }
            )
            
            return {
                "message": "Metrics are exported via OpenTelemetry (OTLP)",
                "trace_id": trace_id,
                "endpoints": {
                    "metrics": "http://localhost:32001/metrics",  # Grafana
                    "traces": "http://localhost:3001/explore",    # Tempo via Grafana
                    "logs": "http://localhost:3001/explore"       # Loki via Grafana
                }
            }
            
        except Exception as e:
            logger.error(
                "Error getting metrics information",
                extra={
                    "status": "error",
                    "operation": "get_metrics_info",
                    "error": str(e),
                    "component": "metrics_handler"
                },
                exc_info=True
            )
            add_business_labels({"status": "error", "operation": "get_metrics_info"})
            raise HTTPException(status_code=500, detail="Internal server error")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=8080, reload=False, log_level="info", access_log=True)