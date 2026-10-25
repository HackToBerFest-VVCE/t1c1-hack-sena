"""
database.py – SQLAlchemy engine and session factory for ClaimLens AI.

Database file: backend/claimlens.db (SQLite)
"""

from pathlib import Path
from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

# Keep the .db file next to this module regardless of working directory
DB_PATH = Path(__file__).parent / "claimlens.db"
DATABASE_URL = f"sqlite:///{DB_PATH}"

# check_same_thread=False is required for SQLite when used with FastAPI
engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False},
    echo=False,          # set True to log every SQL statement
)

SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)


class Base(DeclarativeBase):
    """Shared declarative base – all models inherit from this."""
    pass


def get_db():
    """FastAPI dependency: yields a DB session, closes it on exit."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
