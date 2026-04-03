# app/database.py

import os
import logging
from sqlalchemy import create_engine, Column, Integer, String, Boolean, DateTime, text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session
from datetime import datetime
from typing import Generator

# Setup logging
logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

# Database connection string - support both DATABASE_URL and component vars
# For Kubernetes: set DATABASE_URL directly or it will be constructed from components
DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    # Fallback: construct from individual variables
    db_user = os.getenv("POSTGRES_USER", "todouser")
    db_pass = os.getenv("POSTGRES_PASSWORD", "todopass")
    db_name = os.getenv("POSTGRES_DB", "tododb")
    db_host = os.getenv("POSTGRES_HOST", "postgres")
    db_port = os.getenv("POSTGRES_PORT", "5432")
    DATABASE_URL = f"postgresql://{db_user}:{db_pass}@{db_host}:{db_port}/{db_name}"
    logger.info(f"Constructed DATABASE_URL from components: postgresql://***:***@{db_host}:{db_port}/{db_name}")
else:
    logger.info(f"Using DATABASE_URL from environment variable")

# Create SQLAlchemy engine with robust connection handling
logger.info(f"Initializing database engine with URL: {DATABASE_URL.split('@')[0]}@{DATABASE_URL.split('@')[1] if '@' in DATABASE_URL else 'N/A'}")
engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    pool_recycle=300,
    pool_size=10,
    max_overflow=20,
    pool_timeout=30,
    echo=False,  # Changed from True to reduce log noise in production
    connect_args={
        "connect_timeout": 10,
    }
)

# Create session local
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Base class
Base = declarative_base()

# Define TodoDB model
class TodoDB(Base):
    __tablename__ = "todos"
    id = Column(Integer, primary_key=True, index=True)
    title = Column(String(200), nullable=False)
    description = Column(String(1000), nullable=True)
    completed = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

# Test database connection on startup
def test_connection():
    """Test database connectivity before app starts"""
    try:
        with engine.connect() as conn:
            result = conn.execute(text("SELECT 1"))
            logger.info("✓ Database connection successful")
            return True
    except Exception as e:
        logger.error(f"✗ Database connection failed: {e}")
        return False

# Create tables
def create_tables():
    try:
        # Wait for DB to be ready
        max_retries = 5
        for attempt in range(max_retries):
            if test_connection():
                break
            if attempt < max_retries - 1:
                logger.warning(f"Connection attempt {attempt + 1}/{max_retries} failed, retrying...")
                import time
                time.sleep(2)
        
        Base.metadata.create_all(bind=engine)
        logger.info("✓ Tables created/verified successfully")
    except Exception as e:
        logger.error(f"✗ Failed to create tables: {e}", exc_info=True)
        raise

# Dependency for DB session
def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        try:
            db.close()
        except Exception as e:
            logger.error(f"Error closing session: {e}")
