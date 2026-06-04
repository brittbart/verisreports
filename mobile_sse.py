"""
mobile_sse.py — Verum Signal Mobile SSE Stream
===============================================
Server-Sent Events endpoint for live debate claim feeds.
Streams new claims to the mobile app as they are verified during live debates.

SSE event types emitted:
    event: connected          — initial connection confirmation + existing claims
    event: claim              — new verified claim (provisional or final)
    event: claim_provisional  — new unverified claim (verdict=NULL, verdict_status=provisional)
    event: claim_update       — existing provisional claim now has a verdict
    event: heartbeat          — keepalive ping every 15s
    event: debate_ended       — debate marked complete, client should stop reconnecting
    event: error              — something went wrong
"""

import time
import json
from datetime import datetime, timezone
from flask import Response, request


def _sse_event(event_type: str, data: dict) -> str:
    payload = json.dumps(data, default=str)
    return f"event: {event_type}\ndata: {payload}\n\n"

def _sse_heartbeat() -> str:
    return f"event: heartbeat\ndata: {json.dumps({'ts': datetime.now(timezone.utc).isoformat()})}\n\n"


def _format_claim(row, cols) -> dict:
    c = dict(zip(cols, row))
    return {
        "id":                  c['id'],
        "claim_text":          c['claim_text'],
        "verdict":             c['verdict'].lower().replace(' ', '_') if c['verdict'] else None,
        "confidence_score":    int(c['confidence_score']) if c['confidence_score'] else None,
        "verdict_summary":     c['verdict_summary'],
        "speaker_id":          c['speaker_id'],
        "speaker_name":        c['speaker_name'],
        "speaker_slug":        (c['speaker_name'] or '').lower().replace(' ', '-') if c['speaker_name'] else None,
        "timestamp_seconds":   c['timestamp_seconds'],
        "is_provisional":      c['verdict_status'] == 'provisional',
        "verdict_status":      c['verdict_status'],
        "first_seen":          c['first_seen'].isoformat() if c['first_seen'] else None,
        "methodology_version": c['methodology_version'],
    }


CLAIM_QUERY = """
    SELECT
        c.id, c.claim_text, c.verdict, c.confidence_score,
        c.verdict_summary, c.speaker_id, c.verdict_status,
        c.first_seen, c.methodology_version,
        c.timestamp_seconds,
        s.name AS speaker_name
    FROM claims c
    LEFT JOIN speakers s ON s.id = c.speaker_id
    WHERE c.event_id = %s
      AND c.verdict IS NOT NULL
      AND c.id > %s
    ORDER BY c.first_seen ASC, c.id ASC
    LIMIT 50
"""

PROVISIONAL_CLAIM_QUERY = """
    SELECT
        c.id, c.claim_text, c.verdict, c.confidence_score,
        c.verdict_summary, c.speaker_id, c.verdict_status,
        c.first_seen, c.methodology_version,
        c.timestamp_seconds,
        s.name AS speaker_name
    FROM claims c
    LEFT JOIN speakers s ON s.id = c.speaker_id
    WHERE c.event_id = %s
      AND c.verdict IS NULL
      AND c.verdict_status = 'provisional'
      AND c.id > %s
    ORDER BY c.first_seen ASC, c.id ASC
    LIMIT 50
"""

CLAIM_UPDATE_QUERY = """
    SELECT
        c.id, c.claim_text, c.verdict, c.confidence_score,
        c.verdict_summary, c.speaker_id, c.verdict_status,
        c.first_seen, c.methodology_version,
        c.timestamp_seconds,
        s.name AS speaker_name
    FROM claims c
    LEFT JOIN speakers s ON s.id = c.speaker_id
    WHERE c.event_id = %s
      AND c.verdict IS NOT NULL
      AND c.id = ANY(%s)
    ORDER BY c.id ASC
"""

def _get_event(slug: str, get_db):
    db = get_db()
    cur = db.cursor()
    try:
        cur.execute("SELECT id, event_name, event_date FROM events WHERE slug = %s", (slug,))
        row = cur.fetchone()
        if not row:
            return None
        event_id, event_name, event_date = row
        today = datetime.now(timezone.utc).date()
        is_ended = event_date and event_date < today
        return event_id, event_name, is_ended
    finally:
        cur.close()
        db.close()

def _get_claims_since(event_id: int, since_id: int, get_db) -> list:
    db = get_db()
    cur = db.cursor()
    try:
        cur.execute(CLAIM_QUERY, (event_id, since_id))
        rows = cur.fetchall()
        cols = [d[0] for d in cur.description]
        return [_format_claim(row, cols) for row in rows]
    except Exception as e:
        print(f"[mobile_sse] DB error fetching claims: {e}")
        return []
    finally:
        cur.close()
        db.close()

def _get_provisional_claims_since(event_id: int, since_id: int, get_db) -> list:
    db = get_db()
    cur = db.cursor()
    try:
        cur.execute(PROVISIONAL_CLAIM_QUERY, (event_id, since_id))
        rows = cur.fetchall()
        cols = [d[0] for d in cur.description]
        return [_format_claim(row, cols) for row in rows]
    except Exception as e:
        print(f"[mobile_sse] DB error fetching provisional claims: {e}")
        return []
    finally:
        cur.close()
        db.close()

def _get_claim_updates(event_id: int, pending_ids: list, get_db) -> list:
    if not pending_ids:
        return []
    db = get_db()
    cur = db.cursor()
    try:
        cur.execute(CLAIM_UPDATE_QUERY, (event_id, pending_ids))
        rows = cur.fetchall()
        cols = [d[0] for d in cur.description]
        return [_format_claim(row, cols) for row in rows]
    except Exception as e:
        print(f"[mobile_sse] DB error fetching claim updates: {e}")
        return []
    finally:
        cur.close()
        db.close()


def debate_stream_generator(slug: str, get_db,
                             since_id: int = 0,
                             poll_interval: int = 5,
                             heartbeat_interval: int = 15,
                             max_duration: int = 14400):
    event_info = _get_event(slug, get_db)
    if not event_info:
        yield _sse_event("error", {"code": "NOT_FOUND", "message": f"Debate '{slug}' not found"})
        return

    event_id, event_name, is_ended = event_info

    last_claim_id = since_id
    last_provisional_id = since_id
    pending_provisional_ids = set()

    existing_claims = _get_claims_since(event_id, last_claim_id, get_db)
    existing_provisional = _get_provisional_claims_since(event_id, last_provisional_id, get_db)
    existing_verified_ids = {c['id'] for c in existing_claims}
    existing_provisional = [c for c in existing_provisional if c['id'] not in existing_verified_ids]

    yield _sse_event("connected", {
        "event_id":              event_id,
        "event_name":            event_name,
        "slug":                  slug,
        "is_ended":              is_ended,
        "existing_claims":       existing_claims,
        "existing_provisional":  existing_provisional,
        "last_claim_id":         existing_claims[-1]['id'] if existing_claims else last_claim_id,
        "ts":                    datetime.now(timezone.utc).isoformat(),
    })

    if is_ended:
        yield _sse_event("debate_ended", {"slug": slug, "message": "Debate has ended. Reconnection not needed."})
        return

    if existing_claims:
        last_claim_id = max(c['id'] for c in existing_claims)
    if existing_provisional:
        last_provisional_id = max(c['id'] for c in existing_provisional)
        pending_provisional_ids.update(c['id'] for c in existing_provisional)

    start_time = time.time()
    last_heartbeat = time.time()
    last_poll = time.time()

    while True:
        now = time.time()

        if now - start_time > max_duration:
            yield _sse_event("error", {"code": "TIMEOUT", "message": "Stream max duration reached. Reconnect to continue."})
            break

        if now - last_poll >= poll_interval:
            try:
                # 1. Check pending provisional claims for verdict updates
                if pending_provisional_ids:
                    updated = _get_claim_updates(event_id, list(pending_provisional_ids), get_db)
                    for claim in updated:
                        yield _sse_event("claim_update", claim)
                        pending_provisional_ids.discard(claim['id'])
                        last_claim_id = max(last_claim_id, claim['id'])
                        last_heartbeat = time.time()

                # 2. New verified claims
                new_verified = _get_claims_since(event_id, last_claim_id, get_db)
                for claim in new_verified:
                    if claim['id'] in pending_provisional_ids:
                        yield _sse_event("claim_update", claim)
                        pending_provisional_ids.discard(claim['id'])
                    else:
                        yield _sse_event("claim", claim)
                    last_claim_id = max(last_claim_id, claim['id'])
                    last_heartbeat = time.time()

                # 3. New provisional claims (no verdict yet)
                new_verified_ids = {c['id'] for c in new_verified}
                new_provisional = _get_provisional_claims_since(event_id, last_provisional_id, get_db)
                for claim in new_provisional:
                    if claim['id'] not in new_verified_ids:
                        yield _sse_event("claim_provisional", claim)
                        pending_provisional_ids.add(claim['id'])
                        last_provisional_id = max(last_provisional_id, claim['id'])
                        last_heartbeat = time.time()

            except GeneratorExit:
                break
            except Exception as e:
                print(f"[mobile_sse] poll error: {e}")
                yield _sse_event("error", {"code": "POLL_ERROR", "message": "Temporary error fetching claims. Continuing..."})
            last_poll = time.time()

        if now - last_heartbeat >= heartbeat_interval:
            try:
                yield _sse_heartbeat()
                last_heartbeat = time.time()
            except GeneratorExit:
                break

        time.sleep(1)


def register_sse_routes(mobile_bp, get_db):
    @mobile_bp.route('/debates/<slug>/stream')
    def debate_stream(slug):
        try:
            since_id = int(request.args.get('since_id', 0))
        except (ValueError, TypeError):
            since_id = 0

        def generate():
            yield from debate_stream_generator(slug, get_db, since_id=since_id)

        return Response(
            generate(),
            mimetype='text/event-stream',
            headers={
                'Cache-Control':     'no-cache',
                'X-Accel-Buffering': 'no',
                'Connection':        'keep-alive',
            }
        )
