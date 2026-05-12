"""
Shared helpers for stages/.

Every stage script imports from this module to get:
  - configured logger (writes to stderr, line-buffered, systemd-friendly)
  - run_stage() context manager that logs start/end and writes a
    job_runs row with timing + status

Usage:
    from stages._common import run_stage

    def main():
        with run_stage("fetch") as ctx:
            import fetch_articles
            articles = fetch_articles.fetch_articles()
            ctx.record(articles_processed=len(articles))
            # If anything in this block raises, the row is written with
            # status='failed' and the exception is logged. Exit code is
            # non-zero so systemd records the failure.

    if __name__ == "__main__":
        main()

job_runs schema
---------------
    CREATE TABLE IF NOT EXISTS job_runs (
        id              BIGSERIAL PRIMARY KEY,
        stage           TEXT NOT NULL,
        started_at      TIMESTAMPTZ NOT NULL,
        finished_at     TIMESTAMPTZ,
        duration_ms     INTEGER,
        status          TEXT NOT NULL,        -- 'running', 'ok', 'failed'
        items_processed INTEGER,
        error_class     TEXT,
        error_message   TEXT,
        hostname        TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_job_runs_stage_started
        ON job_runs (stage, started_at DESC);
"""
import logging
import os
import socket
import sys
import time
import traceback
from contextlib import contextmanager
from datetime import datetime, timezone

import psycopg2
from dotenv import load_dotenv


# Load .env if present. Stages run as systemd services with EnvironmentFile,
# but during manual testing this fallback matters.
if os.path.exists(".env"):
    load_dotenv(override=False)


# ---------------------------------------------------------------------
# Logger — line-buffered to stderr so systemd journal sees output
# immediately. flush=True on print() was the Patch 14 fix; using
# logging.StreamHandler avoids the same issue by default.
# ---------------------------------------------------------------------
def _build_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(
        logging.Formatter("%(asctime)s [%(name)s] %(levelname)s %(message)s")
    )
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    return logger


log = _build_logger("stages")


# ---------------------------------------------------------------------
# Postgres connection for job_runs writes.
# Uses the same Patch 13 timeouts/keepalives pattern.
# ---------------------------------------------------------------------
def _db_connect():
    conn = psycopg2.connect(
        dbname=os.getenv("DB_NAME"),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
        host=os.getenv("DB_HOST"),
        port=os.getenv("DB_PORT", "5432"),
        connect_timeout=10,
        keepalives=1,
        keepalives_idle=30,
        keepalives_interval=10,
        keepalives_count=3,
        application_name="veris-stages-common",
    )
    with conn.cursor() as cur:
        cur.execute("SET statement_timeout = 30000")  # 30s for job_runs writes
    conn.commit()
    return conn


# ---------------------------------------------------------------------
# StageContext — what stage functions receive
# ---------------------------------------------------------------------
class StageContext:
    """Passed to the body of a run_stage() block.

    Lets the stage report what it processed (so job_runs has a useful
    items_processed column) without the stage having to think about DB.
    """

    def __init__(self, stage: str):
        self.stage = stage
        self.items_processed = None

    def record(self, items_processed: int):
        """Stages call this to report how many items they handled.
        Optional — if a stage doesn't call it, job_runs.items_processed
        stays NULL."""
        self.items_processed = items_processed


def _write_job_run_start(stage: str) -> int | None:
    """Insert a 'running' job_runs row. Returns the row id, or None on failure.

    Failures here are LOGGED but don't abort the stage — we'd rather lose
    a job_runs entry than fail the whole stage because the audit DB is
    down."""
    try:
        conn = _db_connect()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO job_runs (stage, started_at, status, hostname)
                    VALUES (%s, %s, 'running', %s)
                    RETURNING id
                    """,
                    (stage, datetime.now(timezone.utc), socket.gethostname()),
                )
                row_id = cur.fetchone()[0]
            conn.commit()
            return row_id
        finally:
            conn.close()
    except Exception as e:
        log.warning(f"job_runs start-write failed (continuing): {e}")
        return None


def _write_job_run_end(
    row_id: int,
    status: str,
    duration_ms: int,
    items_processed: int | None,
    error_class: str | None,
    error_message: str | None,
) -> None:
    """Update the existing job_runs row with completion details."""
    if row_id is None:
        return
    try:
        conn = _db_connect()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE job_runs
                    SET finished_at = %s,
                        duration_ms = %s,
                        status = %s,
                        items_processed = %s,
                        error_class = %s,
                        error_message = %s
                    WHERE id = %s
                    """,
                    (
                        datetime.now(timezone.utc),
                        duration_ms,
                        status,
                        items_processed,
                        error_class,
                        # Truncate very long error messages
                        (error_message or "")[:2000] if error_message else None,
                        row_id,
                    ),
                )
            conn.commit()
        finally:
            conn.close()
    except Exception as e:
        log.warning(f"job_runs end-write failed: {e}")


# ---------------------------------------------------------------------
# Public API — context manager stages use
# ---------------------------------------------------------------------
@contextmanager
def run_stage(stage: str):
    """Wrap a stage's main work block.

    - Logs start
    - Writes 'running' row to job_runs
    - On clean exit: status='ok'
    - On exception: status='failed', re-raises so caller exits non-zero
      (systemd records the failure and Restart=on-failure can retry).

    Usage:
        with run_stage("fetch") as ctx:
            ...do work...
            ctx.record(items_processed=42)
    """
    start = time.monotonic()
    log.info(f"[{stage}] starting")
    row_id = _write_job_run_start(stage)
    ctx = StageContext(stage)

    try:
        yield ctx
    except Exception as e:
        duration_ms = int((time.monotonic() - start) * 1000)
        log.error(f"[{stage}] failed after {duration_ms}ms: {type(e).__name__}: {e}")
        log.error(traceback.format_exc())
        _write_job_run_end(
            row_id=row_id,
            status="failed",
            duration_ms=duration_ms,
            items_processed=ctx.items_processed,
            error_class=type(e).__name__,
            error_message=str(e),
        )
        raise  # propagate so the script exits non-zero
    else:
        duration_ms = int((time.monotonic() - start) * 1000)
        items_str = (
            f", items={ctx.items_processed}" if ctx.items_processed is not None else ""
        )
        log.info(f"[{stage}] completed in {duration_ms}ms{items_str}")
        _write_job_run_end(
            row_id=row_id,
            status="ok",
            duration_ms=duration_ms,
            items_processed=ctx.items_processed,
            error_class=None,
            error_message=None,
        )
