#!/usr/bin/env python3
"""
post_debate_check.py — Verum Signal post-debate runbook.
Runs all post-debate checks and actions in sequence.
USAGE:
  python3 post_debate_check.py --event-id 11 --dry-run
  python3 post_debate_check.py --event-id 11
"""
import argparse
import os
import sys
from datetime import datetime, timezone
from dotenv import load_dotenv

if os.path.exists('.env'):
    load_dotenv(override=False)

SEP = '─' * 50

def get_connection():
    import psycopg2
    return psycopg2.connect(
        host=os.environ['DB_HOST'], port=os.environ['DB_PORT'],
        dbname=os.environ['DB_NAME'], user=os.environ['DB_USER'],
        password=os.environ['DB_PASSWORD']
    )

def check(label, ok, detail=''):
    icon = '✓' if ok else '✗'
    detail_str = f' — {detail}' if detail else ''
    print(f'  {icon} {label}{detail_str}')
    return ok

def section(title):
    print(f'\n{SEP}')
    print(f'  {title}')
    print(SEP)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--event-id', type=int, required=True)
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--window', type=int, default=3)
    args = parser.parse_args()

    eid = args.event_id
    dry_run = args.dry_run

    print(f'\nVerum Signal — Post-debate runbook')
    print(f'Run at: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}')
    print(f'Event ID: {eid}  |  Mode: {"DRY RUN" if dry_run else "APPLY"}')

    conn = get_connection()
    cur = conn.cursor()

    # ── 1. Event summary ──────────────────────────────────────────────────
    section('1. Event summary')
    cur.execute("""
        SELECT event_name, event_date, start_time, timezone
        FROM events WHERE id = %s
    """, (eid,))
    row = cur.fetchone()
    if not row:
        print(f'  ✗ Event {eid} not found')
        sys.exit(1)
    event_name, event_date, start_time, tz = row
    print(f'  {event_name}')
    print(f'  {event_date} · {start_time} {tz}')

    # ── 2. Utterance capture ──────────────────────────────────────────────
    section('2. Utterance capture')
    cur.execute("""
        SELECT 
            COUNT(*) as total,
            COUNT(*) FILTER (WHERE attribution_uncertain = TRUE) as uncertain,
            COUNT(DISTINCT speaker_id) FILTER (WHERE speaker_id IS NOT NULL) as speakers
        FROM speaker_utterances WHERE event_id = %s
    """, (eid,))
    total, uncertain, speakers = cur.fetchone()
    check('Utterances captured', total > 0, f'{total} total')
    check('Speakers detected', speakers > 0, f'{speakers} distinct speakers')
    check('Uncertain attributions', True, f'{uncertain} flagged (will re-attribute below)')

    # ── 3. Claim extraction ───────────────────────────────────────────────
    section('3. Claim extraction')
    cur.execute("""
        SELECT 
            COUNT(*) as total,
            COUNT(*) FILTER (WHERE verdict IS NOT NULL) as verified,
            COUNT(*) FILTER (WHERE verdict IS NULL) as pending,
            COUNT(*) FILTER (WHERE verdict_status = 'provisional') as provisional,
            COUNT(*) FILTER (WHERE verdict_status = 'final') as final
        FROM claims WHERE event_id = %s AND claim_origin = 'debate_claim'
    """, (eid,))
    total, verified, pending, provisional, final = cur.fetchone()
    check('Claims extracted', total > 0, f'{total} total')
    check('Claims verified', verified > 0, f'{verified} verified, {pending} pending')
    check('Provisional claims', True, f'{provisional} provisional, {final} final')

    # ── 4. Verdict distribution ───────────────────────────────────────────
    section('4. Verdict distribution')
    cur.execute("""
        SELECT verdict, COUNT(*) 
        FROM claims 
        WHERE event_id = %s AND claim_origin = 'debate_claim'
        AND verdict IS NOT NULL
        GROUP BY verdict ORDER BY count DESC
    """, (eid,))
    rows = cur.fetchall()
    if rows:
        for verdict, count in rows:
            print(f'  · {verdict:<20} {count}')
    else:
        print('  · No verdicts yet')

    # ── 5. Per-speaker breakdown ──────────────────────────────────────────
    section('5. Per-speaker claim breakdown')
    cur.execute("""
        SELECT s.name, COUNT(*) as claims,
               COUNT(*) FILTER (WHERE c.verdict IS NOT NULL) as verified
        FROM claims c
        JOIN speakers s ON s.id = c.speaker_id
        WHERE c.event_id = %s AND c.claim_origin = 'debate_claim'
        GROUP BY s.name ORDER BY claims DESC
    """, (eid,))
    for name, claims, verified in cur.fetchall():
        print(f'  · {name:<25} {claims} claims ({verified} verified)')

    # ── 6. Re-attribution pass ────────────────────────────────────────────
    section('6. Re-attribution pass')
    if uncertain == 0:
        print('  ✓ No uncertain utterances — skipping re-attribution')
    else:
        print(f'  Running reattribute_uncertain.py --event-id {eid} --window {args.window}{"  --dry-run" if dry_run else ""}...')
        import subprocess
        cmd = [sys.executable, 'reattribute_uncertain.py',
               '--event-id', str(eid), '--window', str(args.window)]
        if dry_run:
            cmd.append('--dry-run')
        result = subprocess.run(cmd, capture_output=True, text=True)
        print(result.stdout)
        if result.returncode != 0:
            print(f'  ✗ Re-attribution failed: {result.stderr}')
        # Check remaining uncertain
        cur.execute("""
            SELECT COUNT(*) FROM speaker_utterances
            WHERE event_id = %s AND attribution_uncertain = TRUE
        """, (eid,))
        remaining = cur.fetchone()[0]
        check('Uncertain utterances resolved',
              remaining < uncertain,
              f'{remaining} still uncertain (manual review via /disputes)')

    # ── 7. Confidence gate stats ──────────────────────────────────────────
    section('7. Confidence gate stats (ATTRIBUTION_CONFIDENCE_THRESHOLD = 0.60)')
    cur.execute("""
        SELECT
            COUNT(*) FILTER (WHERE attribution_confidence IS NOT NULL) as with_confidence,
            COUNT(*) FILTER (WHERE attribution_confidence < 0.60) as below_threshold,
            AVG(attribution_confidence) as avg_confidence,
            MIN(attribution_confidence) as min_confidence
        FROM speaker_utterances WHERE event_id = %s
    """, (eid,))
    with_conf, below, avg_conf, min_conf = cur.fetchone()
    check('Confidence scores captured', with_conf and with_conf > 0,
          f'{with_conf} utterances scored' if with_conf else 'NONE — gate did not fire')
    if with_conf and with_conf > 0:
        print(f'  · Below threshold (< 0.60): {below}')
        print(f'  · Average confidence:       {float(avg_conf):.3f}')
        print(f'  · Minimum confidence:       {float(min_conf):.3f}')

    # ── 8. Provisional promotion status ──────────────────────────────────
    section('8. Provisional promotion status')
    cur.execute("""
        SELECT 
            COUNT(*) FILTER (WHERE verdict_status = 'provisional') as still_provisional,
            COUNT(*) FILTER (WHERE verdict_status = 'final') as promoted_final,
            MIN(first_seen) as earliest_claim
        FROM claims
        WHERE event_id = %s AND claim_origin = 'debate_claim'
        AND verdict IS NOT NULL
    """, (eid,))
    still_prov, promoted, earliest = cur.fetchone()
    check('Provisional claims promoted',
          still_prov == 0,
          f'{promoted} final, {still_prov} still provisional')
    if still_prov > 0 and earliest:
        from datetime import timedelta
        promote_at = earliest + timedelta(minutes=60)
        now_utc = datetime.now(timezone.utc)
        if promote_at.tzinfo is None:
            promote_at = promote_at.replace(tzinfo=timezone.utc)
        if promote_at > now_utc:
            mins = int((promote_at - now_utc).total_seconds() / 60)
            print(f'  · Auto-promotion in ~{mins} minutes')
        else:
            print(f'  · Auto-promotion overdue — check railway_api_refresh cron')

    # ── 9. API materialized tables ────────────────────────────────────────
    section('9. API materialized tables')
    cur.execute("""
        SELECT COUNT(*) FROM api_debate_claims
        WHERE event_id = %s
    """, (eid,))
    api_count = cur.fetchone()[0]
    check('api_debate_claims populated', api_count > 0, f'{api_count} rows')

    # ── 10. Summary ───────────────────────────────────────────────────────
    section('10. Next steps')
    if pending > 0:
        print(f'  → {pending} claims still pending verification')
        print(f'    Run: python3 railway_api_refresh.py')
    if still_prov > 0:
        print(f'  → {still_prov} claims still provisional')
        print(f'    Wait for auto-promotion or run: python3 railway_api_refresh.py')
    if uncertain > 0:
        cur.execute("""
            SELECT COUNT(*) FROM speaker_utterances
            WHERE event_id = %s AND attribution_uncertain = TRUE
        """, (eid,))
        remaining_uncertain = cur.fetchone()[0]
        if remaining_uncertain > 0:
            print(f'  → {remaining_uncertain} utterances need manual review at /ops/disputes')
    print(f'  → Check debate page: verumsignal.com/debates/...')
    print()

    cur.close()
    conn.close()

if __name__ == '__main__':
    main()
