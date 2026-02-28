# Complete main.py with exemplars for ALL endpoints
# Add these imports at the top
from fastapi.responses import Response
from prometheus_client import CONTENT_TYPE_LATEST

# Import the new Prometheus functions (modify telemetry imports)
from .telemetry import (
    setup_telemetry, get_tracer, get_meter, add_business_labels,
    trace_todo_operation, log_todo_event, get_current_trace_id,
    trace_todo_operation_with_exemplars, add_business_labels_with_exemplars,
    log_application_event, StatusPreservingFormatter,
    # NEW exemplar functions
    record_histogram_with_exemplar, record_counter_with_exemplar,
    todo_duration_histogram, http_request_histogram, todo_operations_counter,
    get_prometheus_metrics
)

# Add NEW Prometheus metrics endpoint
@app.get("/metrics/prometheus")
async def prometheus_metrics():
    """Expose Prometheus metrics with exemplars - for direct scraping"""
    return Response(content=get_prometheus_metrics(), media_type=CONTENT_TYPE_LATEST)

# UPDATED middleware with exemplars
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
        
        # Existing logging...
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
        
        # UPDATED: Record exemplar with direct Prometheus
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
        
        # Record error exemplar
        record_histogram_with_exemplar(
            http_request_histogram, 
            elapsed, 
            labels={
                "method": request.method,
                "endpoint": request.url.path,
                "status_code": "500"
            },
            exemplar_labels={"error": str(e)[:50]}
        )
        raise

# 1. UPDATED create_todo with exemplars
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
            
            # UPDATED: Record exemplars with direct Prometheus
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
                exemplar_labels={"todo_title": todo.title[:20]}
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
            db.rollback()
            duration = time.time() - start_time
            
            # UPDATED: Record error exemplars
            record_histogram_with_exemplar(
                todo_duration_histogram, 
                duration,
                exemplar_labels={"operation": "create_error", "error": str(e)[:50]}
            )
            
            record_counter_with_exemplar(
                todo_operations_counter,
                1,
                labels={"operation": "create", "status": "error"},
                exemplar_labels={"error_type": type(e).__name__}
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

# 2. UPDATED get_todos with exemplars
@app.get("/todos", response_model=List[TodoResponse])
async def get_todos(skip: int = 0, limit: int = 100, db: Session = Depends(get_db)):
    start_time = time.time()
    
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
            
            # UPDATED: Record validation error exemplar
            record_counter_with_exemplar(
                todo_operations_counter,
                1,
                labels={"operation": "get_todos", "status": "validation_error"},
                exemplar_labels={"error": "invalid_skip", "skip": str(skip)}
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
            
            # UPDATED: Record validation error exemplar
            record_counter_with_exemplar(
                todo_operations_counter,
                1,
                labels={"operation": "get_todos", "status": "validation_error"},
                exemplar_labels={"error": "invalid_limit", "limit": str(limit)}
            )
            
            log_todo_event("get_todos", status="error", 
                          details={"error": "Invalid limit parameter", "operation": "all_todos"})
            raise HTTPException(status_code=400, detail="Limit must be between 1 and 1000")

        try:
            todos = db.query(TodoDB).offset(skip).limit(limit).all()
            duration = time.time() - start_time
            
            # UPDATED: Record success exemplars
            record_counter_with_exemplar(
                todo_operations_counter,
                1,
                labels={"operation": "get_todos", "status": "success"},
                exemplar_labels={"count": str(len(todos)), "skip": str(skip), "limit": str(limit)}
            )
            
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
            duration = time.time() - start_time
            
            # UPDATED: Record error exemplar
            record_counter_with_exemplar(
                todo_operations_counter,
                1,
                labels={"operation": "get_todos", "status": "error"},
                exemplar_labels={"error": str(e)[:50], "skip": str(skip), "limit": str(limit)}
            )
            
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

# 3. UPDATED update_todo with exemplars
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
                duration = time.time() - start_time
                
                # UPDATED: Record not found exemplar
                record_counter_with_exemplar(
                    todo_operations_counter,
                    1,
                    labels={"operation": "update_todo", "status": "not_found"},
                    exemplar_labels={"todo_id": str(todo_id)}
                )
                
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
                duration = time.time() - start_time
                
                # UPDATED: Record no changes exemplar
                record_counter_with_exemplar(
                    todo_operations_counter,
                    1,
                    labels={"operation": "update_todo", "status": "no_changes"},
                    exemplar_labels={"todo_id": str(todo_id)}
                )
                
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
            
            duration = time.time() - start_time
            
            # UPDATED: Record success exemplars
            record_histogram_with_exemplar(
                todo_duration_histogram, 
                duration,
                exemplar_labels={"operation": operation_type, "todo_id": str(todo_id), "fields": ",".join(updates)}
            )
            
            record_counter_with_exemplar(
                todo_operations_counter,
                1,
                labels={"operation": operation_type, "status": "success"},
                exemplar_labels={"todo_id": str(todo_id), "updated_fields": ",".join(updates)}
            )
            
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
            
            # UPDATED: Record error exemplar
            record_histogram_with_exemplar(
                todo_duration_histogram, 
                duration,
                exemplar_labels={"operation": "update_error", "todo_id": str(todo_id), "error": str(e)[:50]}
            )
            
            record_counter_with_exemplar(
                todo_operations_counter,
                1,
                labels={"operation": "update_todo", "status": "error"},
                exemplar_labels={"todo_id": str(todo_id), "error_type": type(e).__name__}
            )
            
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

# 4. UPDATED delete_todo with exemplars
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
                duration = time.time() - start_time
                
                # UPDATED: Record not found exemplar
                record_counter_with_exemplar(
                    todo_operations_counter,
                    1,
                    labels={"operation": "delete_todo", "status": "not_found"},
                    exemplar_labels={"todo_id": str(todo_id)}
                )
                
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
            
            duration = time.time() - start_time
            
            # UPDATED: Record success exemplars
            record_histogram_with_exemplar(
                todo_duration_histogram, 
                duration,
                exemplar_labels={"operation": "delete", "todo_id": str(todo_id), "title": title[:20]}
            )
            
            record_counter_with_exemplar(
                todo_operations_counter,
                1,
                labels={"operation": "delete", "status": "success"},
                exemplar_labels={"todo_id": str(todo_id), "was_completed": str(completed)}
            )
            
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
            
            # UPDATED: Record error exemplar
            record_histogram_with_exemplar(
                todo_duration_histogram, 
                duration,
                exemplar_labels={"operation": "delete_error", "todo_id": str(todo_id), "error": str(e)[:50]}
            )
            
            record_counter_with_exemplar(
                todo_operations_counter,
                1,
                labels={"operation": "delete", "status": "error"},
                exemplar_labels={"todo_id": str(todo_id), "error_type": type(e).__name__}
            )
            
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

# 5. UPDATED complete_todo with exemplars  
@app.post("/todos/{todo_id}/complete")
async def complete_todo(todo_id: int, db: Session = Depends(get_db)):
    start_time = time.time()
    
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
                duration = time.time() - start_time
                
                # UPDATED: Record not found exemplar
                record_counter_with_exemplar(
                    todo_operations_counter,
                    1,
                    labels={"operation": "complete", "status": "not_found"},
                    exemplar_labels={"todo_id": str(todo_id)}
                )
                
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
                duration = time.time() - start_time
                
                # UPDATED: Record already completed exemplar
                record_counter_with_exemplar(
                    todo_operations_counter,
                    1,
                    labels={"operation": "complete", "status": "already_completed"},
                    exemplar_labels={"todo_id": str(todo_id), "title": db_todo.title[:20]}
                )
                
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
            
            duration = time.time() - start_time
            
            # UPDATED: Record success exemplar
            record_counter_with_exemplar(
                todo_operations_counter,
                1,
                labels={"operation": "complete", "status": "success"},
                exemplar_labels={"todo_id": str(todo_id), "title": db_todo.title[:20]}
            )
            
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
            duration = time.time() - start_time
            
            # UPDATED: Record error exemplar
            record_counter_with_exemplar(
                todo_operations_counter,
                1,
                labels={"operation": "complete", "status": "error"},
                exemplar_labels={"todo_id": str(todo_id), "error": str(e)[:50]}
            )
            
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

# 6. UPDATED uncomplete_todo with exemplars
@app.post("/todos/{todo_id}/uncomplete")
async def uncomplete_todo(todo_id: int, db: Session = Depends(get_db)):
    start_time = time.time()
    
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
                duration = time.time() - start_time
                
                # UPDATED: Record not found exemplar
                record_counter_with_exemplar(
                    todo_operations_counter,
                    1,
                    labels={"operation": "uncomplete", "status": "not_found"},
                    exemplar_labels={"todo_id": str(todo_id)}
                )
                
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
                duration = time.time() - start_time
                
                # UPDATED: Record already uncompleted exemplar
                record_counter_with_exemplar(
                    todo_operations_counter,
                    1,
                    labels={"operation": "uncomplete", "status": "already_uncompleted"},
                    exemplar_labels={"todo_id": str(todo_id), "title": db_todo.title[:20]}
                )
                
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
            
            duration = time.time() - start_time
            
            # UPDATED: Record success exemplar
            record_counter_with_exemplar(
                todo_operations_counter,
                1,
                labels={"operation": "uncomplete", "status": "success"},
                exemplar_labels={"todo_id": str(todo_id), "title": db_todo.title[:20]}
            )
            
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
            duration = time.time() - start_time
            
            # UPDATED: Record error exemplar
            record_counter_with_exemplar(
                todo_operations_counter,
                1,
                labels={"operation": "uncomplete", "status": "error"},
                exemplar_labels={"todo_id": str(todo_id), "error": str(e)[:50]}
            )
            
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

# 7. UPDATED get_todo with exemplars
@app.get("/todos/{todo_id}", response_model=TodoResponse)
async def get_todo(todo_id: int, db: Session = Depends(get_db)):
    start_time = time.time()
    
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
            # UPDATED: Record validation error exemplar
            record_counter_with_exemplar(
                todo_operations_counter,
                1,
                labels={"operation": "get_todo", "status": "validation_error"},
                exemplar_labels={"todo_id": str(todo_id), "error": "invalid_id"}
            )
            
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
            duration = time.time() - start_time
            
            if not todo:
                # UPDATED: Record not found exemplar
                record_counter_with_exemplar(
                    todo_operations_counter,
                    1,
                    labels={"operation": "get_todo", "status": "not_found"},
                    exemplar_labels={"todo_id": str(todo_id)}
                )
                
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

            # UPDATED: Record success exemplar
            record_counter_with_exemplar(
                todo_operations_counter,
                1,
                labels={"operation": "get_todo", "status": "success"},
                exemplar_labels={"todo_id": str(todo_id), "title": todo.title[:20], "completed": str(todo.completed)}
            )
            
            add_business_labels({"status": "success", "operation": "get_todo"})
            
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
            duration = time.time() - start_time
            
            # UPDATED: Record error exemplar
            record_counter_with_exemplar(
                todo_operations_counter,
                1,
                labels={"operation": "get_todo", "status": "error"},
                exemplar_labels={"todo_id": str(todo_id), "error": str(e)[:50]}
            )
            
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