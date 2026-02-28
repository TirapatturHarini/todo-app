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

from .telemetry import setup_telemetry, get_tracer, get_meter
from .database import create_tables, get_db, TodoDB
from .models import Todo, TodoCreate, TodoUpdate

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("/tmp/app.log") if os.path.exists("/tmp") else logging.NullHandler(),
    ],
)
logger = logging.getLogger(__name__)

# Lifespan handler
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting Todo Application")
    try:
        create_tables()
        logger.info("Database tables created/verified")
    except Exception as e:
        logger.error("Failed to create database tables: %s", e)
    yield
    logger.info("Shutting down Todo Application")

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
    allow_origins=["http://localhost:3000"],  # adjust in prod
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Telemetry setup
setup_telemetry(app)
tracer = get_tracer()
meter = get_meter()

# Metrics
todo_counter = meter.create_counter("todos_total", description="Total todos created", unit="1")
todo_operations = meter.create_counter("todo_operations_total", description="Total todo operations", unit="1")
request_duration = meter.create_histogram("http_request_duration_seconds", description="HTTP request duration in seconds", unit="s")

# Middleware to measure request time
@app.middleware("http")
async def add_process_time_header(request: Request, call_next):
    start = time.time()
    response = await call_next(request)
    elapsed = time.time() - start
    response.headers["X-Process-Time"] = f"{elapsed:.6f}"
    try:
        request_duration.record(elapsed, {
            "method": request.method,
            "endpoint": request.url.path,
            "status_code": str(response.status_code),
        })
    except Exception:
        pass
    return response

# --- Health & Readiness ---
@app.get("/health")
async def health_check(db: Session = Depends(get_db)):
    """Health check endpoint"""
    with tracer.start_as_current_span("health_check"):
        logger.info("Health check requested")
        status = {
            "status": "healthy",
            "service": "todo-api",
            "version": "1.0.0",
            "timestamp": time.time(),
        }

        try:
            db.execute(text("SELECT 1"))
            status["database"] = "connected"
        except Exception as e:
            logger.warning("Database health check failed: %s", e)
            status["database"] = "disconnected"
            status["status"] = "degraded"

        return status

@app.get("/ready")
async def readiness_check(db: Session = Depends(get_db)):
    """Readiness check for Kubernetes"""
    try:
        db.execute(text("SELECT 1"))
        return {"status": "ready"}
    except Exception as e:
        logger.error("Readiness check failed: %s", e)
        raise HTTPException(status_code=503, detail="Service not ready")

# --- CRUD Endpoints ---

@app.get("/todos", response_model=List[Todo])
async def get_todos(skip: int = 0, limit: int = 100, db: Session = Depends(get_db)):
    with tracer.start_as_current_span("get_todos"):
        logger.info(f"Fetching todos with skip={skip}, limit={limit}")
        if skip < 0:
            raise HTTPException(status_code=400, detail="Skip must be non-negative")
        if limit <= 0 or limit > 1000:
            raise HTTPException(status_code=400, detail="Limit must be between 1 and 1000")

        todos = db.query(TodoDB).offset(skip).limit(limit).all()
        todo_operations.add(1, {"operation": "read", "count": len(todos)})
        return todos

@app.post("/todos", response_model=Todo, status_code=201)
async def create_todo(todo: TodoCreate, db: Session = Depends(get_db)):
    with tracer.start_as_current_span("create_todo"):
        logger.info(f"Creating new todo: {todo.title}")
        try:
            db_todo = TodoDB(title=todo.title, description=todo.description)
            db.add(db_todo)
            db.commit()
            db.refresh(db_todo)
            todo_counter.add(1)
            todo_operations.add(1, {"operation": "create"})
            return db_todo
        except Exception as e:
            db.rollback()
            logger.error("Error creating todo: %s", e)
            raise HTTPException(status_code=500, detail="Internal server error")

@app.get("/todos/{todo_id}", response_model=Todo)
async def get_todo(todo_id: int, db: Session = Depends(get_db)):
    with tracer.start_as_current_span("get_todo"):
        logger.info(f"Fetching todo with ID: {todo_id}")
        if todo_id <= 0:
            raise HTTPException(status_code=400, detail="Todo ID must be positive")

        todo = db.query(TodoDB).filter(TodoDB.id == todo_id).first()
        if not todo:
            raise HTTPException(status_code=404, detail="Todo not found")

        todo_operations.add(1, {"operation": "read_single"})
        return todo

@app.put("/todos/{todo_id}", response_model=Todo)
async def update_todo(todo_id: int, todo_update: TodoUpdate, db: Session = Depends(get_db)):
    with tracer.start_as_current_span("update_todo"):
        logger.info(f"Updating todo with ID: {todo_id}")
        db_todo = db.query(TodoDB).filter(TodoDB.id == todo_id).first()
        if not db_todo:
            raise HTTPException(status_code=404, detail="Todo not found")

        updated = False
        if todo_update.title is not None:
            db_todo.title = todo_update.title
            updated = True
        if todo_update.description is not None:
            db_todo.description = todo_update.description
            updated = True
        if todo_update.completed is not None:
            db_todo.completed = todo_update.completed
            updated = True

        if not updated:
            raise HTTPException(status_code=400, detail="No fields to update")

        try:
            db.commit()
            db.refresh(db_todo)
            todo_operations.add(1, {"operation": "update"})
            return db_todo
        except Exception as e:
            db.rollback()
            logger.error("Error updating todo %s: %s", todo_id, e)
            raise HTTPException(status_code=500, detail="Internal server error")

@app.delete("/todos/{todo_id}")
async def delete_todo(todo_id: int, db: Session = Depends(get_db)):
    with tracer.start_as_current_span("delete_todo"):
        logger.info(f"Deleting todo with ID: {todo_id}")
        db_todo = db.query(TodoDB).filter(TodoDB.id == todo_id).first()
        if not db_todo:
            raise HTTPException(status_code=404, detail="Todo not found")

        try:
            title = db_todo.title
            db.delete(db_todo)
            db.commit()
            todo_operations.add(1, {"operation": "delete"})
            return {"message": "Todo deleted successfully", "id": todo_id, "title": title}
        except Exception as e:
            db.rollback()
            logger.error("Error deleting todo %s: %s", todo_id, e)
            raise HTTPException(status_code=500, detail="Internal server error")

# --- Global Exception Handler ---
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error("Unhandled exception: %s", exc, exc_info=True)
    return JSONResponse(
        status_code=500,
        content={
            "detail": "Internal server error",
            "type": type(exc).__name__,
            "path": str(request.url.path),
        },
    )

@app.get("/metrics")
async def metrics_info():
    return {"message": "Metrics are exported via OpenTelemetry (OTLP)"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=8080, reload=False, log_level="info", access_log=True)
