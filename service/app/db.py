"""Database engine and session management.

SQLite for local dev, Postgres (Cloud SQL) in deployment. Chosen by DATABASE_URL.
"""
import os
from collections.abc import Iterator

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from app.models import Base

DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./valueledger.db")

_connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, connect_args=_connect_args, future=True)

if DATABASE_URL.startswith("sqlite"):

    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_conn, _record):  # noqa: ANN001
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA journal_mode=WAL")
        cur.close()


SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)


def init_db() -> None:
    """Create tables. POC uses create_all rather than Alembic migrations.

    Tolerant of a concurrent creator: two processes starting together can both
    pass create_all's existence check and then race on the DDL.
    """
    from sqlalchemy.exc import OperationalError, ProgrammingError
    try:
        Base.metadata.create_all(bind=engine)
    except (OperationalError, ProgrammingError) as e:
        if "already exists" not in str(e).lower():
            raise
        print("[valueledger] schema already present (concurrent start)")


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
