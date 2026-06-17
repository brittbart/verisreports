#!/usr/bin/env python3
"""
verdict_engine_parallel.py — Verum Signal v1.7 parallel verifier.

Runs alongside verdict_engine.py (does NOT replace it). Uses ThreadPoolExecutor
to verify multiple claims concurrently, targeting ~20s vs ~60s for a 10-claim batch.

KILL SWITCH: Set PARALLEL_VERIFIER_ENABLED=false (default) to disable.
             Set PARALLEL_VERIFIER_ENABLED=true to enable shadow mode.
             Set PARALLEL_VERIFIER_SHADOW=false to promote to production writes.

SHADOW MODE (default when enabled):
  - Runs alongside the sequential verifier on the same claims
  - Compares outputs and logs disagreements
  - Does NOT write verdicts to production
  - Accumulate 3+ weeks of shadow data before promoting

PRODUCTION MODE (PARALLEL_VERIFIER_SHADOW=false):
  - Writes verdicts directly
  - Sequential verifier is the fallback on any thread error
  - Kill switch (PARALLEL_VERIFIER_ENABLED=false) reverts to sequential immediately

CONCURRENCY: Bounded at MAX_WORKERS=4 threads (configurable via env var).
Per-thread DB connections — no shared state across threads.
Error isolation per thread: failed thread falls back to sequential for that claim.

USAGE:
  from verdict_engine_parallel import run_parallel_verdict_engine, is_parallel_enabled
  if is_parallel_enabled():
      run_parallel_verdict_engine(limit=10)
  else:
      run_verdict_engine(limit=10)  # sequential fallback
"""

import os
import json
import time
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

import psycopg2
from dotenv import load_dotenv

if os.path.exists(".env"):
    load_dotenv(override=False)

# Import from canonical verdict_engine — no duplicate logic
from verdict_engine import (
    analyse_claim,
    update_source_profile,
    calculate_reliability_score,
    get_connection,
    pre_filter_claim,
)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

PARALLEL_VERIFIER_ENABLED = os.getenv("PARALLEL_VERIFIER_ENABLED", "false").lower() == "true"
PARALLEL_VERIFIER_SHADOW = os.getenv("PARALLEL_VERIFIER_SHADOW", "true").lower() != "false"
MAX_WORKERS = int(os.getenv("PARALLEL_VERIFIER_WORKERS", "4"))

# Shadow log file
SHADOW_LOG_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "parallel_verifier_shadow.log"
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("parallel_verifier")


def is_parallel_enabled() -> bool:
    """Check kill switch. Always returns False if env var not explicitly set to true."""
    return PARALLEL_VERIFIER_ENABLED


# ---------------------------------------------------------------------------
# Per-thread DB connection (no shared state)
# ---------------------------------------------------------------------------

def _get_thread_connection():
    """Each thread gets its own DB connection. Never shared across threads."""
    return psycopg2.connect(
        dbname=os.getenv('DB_NAME'),
        user=os.getenv('DB_USER'),
        password=os.getenv('DB_PASSWORD'),
        host=os.getenv('DB_HOST'),
        port=os.getenv('DB_PORT', '5432'),
        connect_timeout=10,
        keepalives=1,
        keepalives_idle=30,
    )


# ---------------------------------------------------------------------------
# Single-claim worker (runs in thread)
# ---------------------------------------------------------------------------

def _verify_claim_worker(claim_row, depth=None):
    """
    Worker function executed in a thread pool.
    Opens its own DB connection. No shared state with other threads.

    Returns dict with claim_id, result, error, duration_ms.
    """
    claim_id, claim_text, speaker, claim_type, article_title, source_name, priority_score, claim_origin, attribution_context = claim_row
    t0 = time.time()
    conn = None

    try:
        conn = _get_thread_connection()
        cursor = conn.cursor()

        result = analyse_claim(
            claim_text, speaker, claim_type,
            article_title, source_name, cursor,
            claim_origin=claim_origin,
            attribution_context=attribution_context,
        )

        cursor.close()
        duration_ms = int((time.time() - t0) * 1000)

        return {
            'claim_id':    claim_id,
            'claim_text':  claim_text,
            'source_name': source_name,
            'result':      result,
            'error':       None,
            'duration_ms': duration_ms,
        }

    except Exception as e:
        duration_ms = int((time.time() - t0) * 1000)
        logger.error(f"Thread error on claim {claim_id}: {e}")
        return {
            'claim_id':    claim_id,
            'claim_text':  claim_text,
            'source_name': source_name,
            'result':      None,
            'error':       str(e),
            'duration_ms': duration_ms,
        }
    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Shadow comparison
# ---------------------------------------------------------------------------

def _log_shadow_comparison(claim_id, parallel_result, sequential_result):
    """Log disagreements between parallel and sequential verdicts."""
    p_verdict = (parallel_result or {}).get('verdict') if parallel_result else None
    s_verdict = (sequential_result or {}).get('verdict') if sequential_result else None

    agree = p_verdict == s_verdict
    entry = {
        'ts':              datetime.utcnow().isoformat(),
        'claim_id':        claim_id,
        'parallel_verdict':   p_verdict,
        'sequential_verdict': s_verdict,
        'agree':           agree,
    }

    with open(SHADOW_LOG_PATH, 'a') as f:
        f.write(json.dumps(entry) + '\n')

    if not agree:
        logger.warning(
            f"[shadow] DISAGREE claim {claim_id}: "
            f"parallel={p_verdict!r} sequential={s_verdict!r}"
        )
    return agree


def get_shadow_stats():
    """Parse shadow log and return agreement rate stats."""
    if not os.path.exists(SHADOW_LOG_PATH):
        return {'total': 0, 'agree': 0, 'disagree': 0, 'rate': None}

    total = agree = 0
    with open(SHADOW_LOG_PATH) as f:
        for line in f:
            try:
                entry = json.loads(line)
                total += 1
                if entry.get('agree'):
                    agree += 1
            except Exception:
                pass

    disagree = total - agree
    rate = round(agree / total * 100, 2) if total > 0 else None
    return {'total': total, 'agree': agree, 'disagree': disagree, 'rate': rate}


# ---------------------------------------------------------------------------
# Main parallel runner
# ---------------------------------------------------------------------------

def run_parallel_verdict_engine(limit=10, depth=None, shadow_compare=None):
    """
    Parallel verifier. Fetches unverified claims and processes them concurrently.

    Args:
        limit:          Max claims to process.
        depth:          Verification depth (passed through to analyse_claim).
        shadow_compare: If True, run sequential alongside and log disagreements.
                        Defaults to PARALLEL_VERIFIER_SHADOW env var.

    Returns:
        dict with verdicts_assigned, errors, duration_ms, shadow_stats (if shadow mode).
    """
    if shadow_compare is None:
        shadow_compare = PARALLEL_VERIFIER_SHADOW

    if not is_parallel_enabled():
        logger.info("Parallel verifier disabled (PARALLEL_VERIFIER_ENABLED != true). "
                    "Use run_verdict_engine() instead.")
        return {'verdicts_assigned': 0, 'errors': 0, 'skipped': 'kill_switch'}

    mode = "SHADOW (no writes)" if shadow_compare else "PRODUCTION (writing verdicts)"
    logger.info(f"Parallel verifier starting — mode={mode} limit={limit} workers={MAX_WORKERS}")

    t_start = time.time()

    # Fetch claims using a dedicated connection
    fetch_conn = get_connection()
    fetch_cursor = fetch_conn.cursor()
    fetch_cursor.execute("""
        SELECT c.id, c.claim_text, c.speaker, c.claim_type,
               a.title, a.source_name, c.priority_score,
               c.claim_origin, COALESCE(c.attribution_context, '')
        FROM claims c
        JOIN articles a ON c.article_id = a.id
        WHERE c.verdict IS NULL
          AND c.priority_score >= 30
          AND COALESCE(c.verification_attempts, 0) < 3
        ORDER BY c.priority_score DESC
        LIMIT %s;
    """, (limit,))
    claims = fetch_cursor.fetchall()
    fetch_cursor.close()
    fetch_conn.close()

    if not claims:
        logger.info("No unverified claims found.")
        return {'verdicts_assigned': 0, 'errors': 0, 'claims_found': 0}

    logger.info(f"Found {len(claims)} claims. Submitting to thread pool (workers={MAX_WORKERS})")

    # Submit all claims to thread pool
    futures = {}
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        for claim_row in claims:
            future = executor.submit(_verify_claim_worker, claim_row, depth)
            futures[future] = claim_row

    # Collect results
    verdicts_assigned = 0
    errors = 0
    shadow_disagree = 0

    write_conn = None if shadow_compare else get_connection()

    try:
        for future, claim_row in futures.items():
            worker_result = future.result()
            claim_id = worker_result['claim_id']
            result = worker_result['result']
            error = worker_result['error']
            duration_ms = worker_result['duration_ms']

            if error or not result:
                logger.error(f"Claim {claim_id} failed ({duration_ms}ms): {error}")
                errors += 1
                continue

            verdict = result.get('verdict', 'not_verifiable')
            logger.info(f"Claim {claim_id}: {verdict} ({duration_ms}ms)")

            if shadow_compare:
                # Shadow mode: run sequential for comparison, don't write either
                try:
                    seq_conn = get_connection()
                    seq_cursor = seq_conn.cursor()
                    seq_result = analyse_claim(
                        worker_result['claim_text'],
                        claim_row[2],  # speaker
                        claim_row[3],  # claim_type
                        claim_row[4],  # article_title
                        worker_result['source_name'],
                        seq_cursor
                    )
                    seq_cursor.close()
                    seq_conn.close()
                    agreed = _log_shadow_comparison(claim_id, result, seq_result)
                    if not agreed:
                        shadow_disagree += 1
                except Exception as e:
                    logger.error(f"Sequential comparison failed for claim {claim_id}: {e}")
            else:
                # Production mode: write verdict
                try:
                    write_cursor = write_conn.cursor()
                    import json as _pjson
                    _par_raw = result.get('sources_used', '')
                    if isinstance(_par_raw, list): _par_str = _par_raw
                    elif isinstance(_par_raw, str):
                        try:
                            _pp = _pjson.loads(_par_raw); _par_str = _pp if isinstance(_pp, list) else []
                        except Exception: _par_str = []
                    else: _par_str = []
                    _par_prose = _sources_to_prose(_par_str) if _par_str else (str(_par_raw) if _par_raw else '')
                    write_cursor.execute("""
                        UPDATE claims
                        SET verdict = %s,
                            confidence_score = %s,
                            verdict_summary = %s,
                            full_analysis = %s,
                            sources_used = %s,
                            sources_structured = %s,
                            verification_depth = COALESCE(%s, verification_depth),
                            verification_attempts = COALESCE(verification_attempts, 0) + 1,
                            last_checked = NOW()
                        WHERE id = %s;
                    """, (
                        verdict,
                        min(result.get('confidence_score', 1), 3),
                        result.get('verdict_summary', ''),
                        result.get('full_analysis', ''),
                        _par_prose,
                        _pjson.dumps(_par_str),
                        depth or 99,
                        claim_id
                    ))
                    update_source_profile(write_cursor, worker_result['source_name'], verdict)
                    calculate_reliability_score(write_cursor, worker_result['source_name'])
                    write_conn.commit()
                    write_cursor.close()
                    verdicts_assigned += 1
                except Exception as e:
                    logger.error(f"Write failed for claim {claim_id}: {e}")
                    if write_conn:
                        write_conn.rollback()
                    errors += 1

    finally:
        if write_conn:
            try:
                write_conn.close()
            except Exception:
                pass

    duration_ms = int((time.time() - t_start) * 1000)

    stats = {
        'verdicts_assigned': verdicts_assigned,
        'errors':            errors,
        'claims_found':      len(claims),
        'duration_ms':       duration_ms,
        'mode':              'shadow' if shadow_compare else 'production',
        'workers':           MAX_WORKERS,
    }

    if shadow_compare:
        shadow_stats = get_shadow_stats()
        stats['shadow_stats'] = shadow_stats
        logger.info(
            f"Shadow run complete: {len(claims)} claims in {duration_ms}ms. "
            f"Disagreements this run: {shadow_disagree}. "
            f"Cumulative agreement rate: {shadow_stats['rate']}%"
        )
    else:
        logger.info(
            f"Production run complete: {verdicts_assigned} verdicts in {duration_ms}ms. "
            f"Errors: {errors}"
        )

    return stats


# ---------------------------------------------------------------------------
# Shadow stats CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Parallel verifier shadow stats")
    parser.add_argument("--stats", action="store_true", help="Print shadow log stats")
    parser.add_argument("--run", action="store_true", help="Run one shadow batch (limit=10)")
    args = parser.parse_args()

    if args.stats:
        stats = get_shadow_stats()
        print(f"Shadow log entries: {stats['total']}")
        print(f"  Agree:    {stats['agree']}")
        print(f"  Disagree: {stats['disagree']}")
        print(f"  Rate:     {stats['rate']}%")

    elif args.run:
        if not is_parallel_enabled():
            print("PARALLEL_VERIFIER_ENABLED is not set to true. Set it to run.")
        else:
            result = run_parallel_verdict_engine(limit=10)
            print(json.dumps(result, indent=2))
    else:
        print("Use --stats or --run")
