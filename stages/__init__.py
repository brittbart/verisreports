"""
stages/ — one-shot scripts for each pipeline stage.

Replaces the long-running scheduler.py daemon. Each stage is invoked
independently by a systemd timer; the per-stage scripts import the
same domain modules scheduler.py imported, so behavior is identical.

See stages/_common.py for shared helpers (logging, job_runs writer).
"""
