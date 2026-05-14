"""
Debate routes (v1.7):
  /debates              — Debates index page (HTML)
  /debates/<slug>       — Debate event detail page (HTML)
  /api/debates          — JSON: all public events
  /api/debates/<slug>   — JSON: single event with claims
"""
import re
from datetime import date
from flask import render_template, abort, jsonify
from api_leaderboard import METHODOLOGY_VERSION, VERDICT_LABELS

SLUG_RE = re.compile(r"^[a-z0-9-]+$")

# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def _get_all_public_events(get_db_conn):
    """Return all public events sorted by date descending."""
    conn = get_db_conn()
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT
                e.id, e.slug, e.event_type, e.event_name,
                e.event_date, e.start_time, e.timezone, e.event_subtitle, e.venue, e.transcript_source,
                e.methodology_version, e.is_public,
                COUNT(c.id) FILTER (WHERE c.verdict IS NOT NULL) AS claim_count
            FROM events e
            LEFT JOIN claims c ON c.event_id = e.id
                AND c.claim_origin = 'debate_claim'
            WHERE e.is_public = TRUE
            GROUP BY e.id, e.slug, e.event_type, e.event_name,
                     e.event_date, e.venue, e.transcript_source,
                     e.methodology_version, e.is_public
            ORDER BY e.event_date DESC
        """)
        rows = cur.fetchall()
        cur.close()
        events = []
        today = date.today()
        for row in rows:
            (eid, slug, event_type, event_name, event_date, start_time, timezone, event_subtitle, venue,
             transcript_source, methodology_version, is_public, claim_count) = row
            status = _derive_status(event_date, today)
            events.append({
                'id':                  eid,
                'slug':                slug,
                'event_type':          event_type,
                'event_name':          event_name,
                'event_date':          event_date,
                'event_date_str':      event_date.strftime('%B %-d, %Y') if event_date else '',
                'event_date_mo':       event_date.strftime('%b').upper() if event_date else '',
                'event_date_day':      event_date.strftime('%-d') if event_date else '',
                'event_date_year':     event_date.strftime('%Y') if event_date else '',
                'start_time_str':      (start_time.strftime('%-I:%M %p') + ' ' + (timezone or 'ET')) if start_time else 'TBD',
                'event_subtitle':      event_subtitle or '',
                'event_start_iso':     (event_date.strftime('%Y-%m-%dT') + start_time.strftime('%H:%M:00')) if (event_date and start_time) else '',
                'venue':               venue or '',
                'transcript_source':   transcript_source or '',
                'methodology_version': methodology_version or METHODOLOGY_VERSION,
                'claim_count':         claim_count or 0,
                'status':              status,
            })
        return events
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _get_event_by_slug(get_db_conn, slug):
    """Return a single public event by slug, or None."""
    conn = get_db_conn()
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT id, slug, event_type, event_name, event_date, start_time, timezone, event_subtitle, venue,
                   transcript_url, transcript_source, is_public,
                   methodology_version, notes
            FROM events
            WHERE slug = %s AND is_public = TRUE
        """, (slug,))
        row = cur.fetchone()
        if not row:
            cur.close()
            return None, []
        (eid, slug, event_type, event_name, event_date, start_time, timezone, event_subtitle, venue,
         transcript_url, transcript_source, is_public,
         methodology_version, notes) = row

        today = date.today()
        status = _derive_status(event_date, today)

        event = {
            'id':                  eid,
            'slug':                slug,
            'event_type':          event_type,
            'event_name':          event_name,
            'event_date':          event_date,
            'event_date_str':      event_date.strftime('%B %-d, %Y') if event_date else '',
            'start_time_str':      (start_time.strftime('%-I:%M %p') + ' ' + (timezone or 'ET')) if start_time else 'TBD',
            'event_subtitle':      event_subtitle or '',
            'event_start_iso':     (event_date.strftime('%Y-%m-%dT') + start_time.strftime('%H:%M:00')) if (event_date and start_time) else '',
            'venue':               venue or '',
            'transcript_url':      transcript_url or '',
            'transcript_source':   transcript_source or '',
            'methodology_version': methodology_version or METHODOLOGY_VERSION,
            'notes':               notes or '',
            'status':              status,
            'is_live':             status == 'live',
            'is_upcoming':         status == 'upcoming',
            'is_complete':         status == 'complete',
        }

        # Fetch participants — prefer event_speakers (pre-seeded), fall back to utterances
        cur.execute("""
            SELECT s.id, s.name, s.normalized_name, s.slug,
                   s.role, s.party, s.speaker_type
            FROM event_speakers es
            JOIN speakers s ON s.id = es.speaker_id
            WHERE es.event_id = %s
            ORDER BY es.speaker_order
        """, (eid,))
        rows = cur.fetchall()

        # Fall back to utterance-based detection if no pre-seeded speakers
        if not rows:
            cur.execute("""
                SELECT DISTINCT s.id, s.name, s.normalized_name, s.slug,
                                s.role, s.party, s.speaker_type
                FROM speaker_utterances su
                JOIN speakers s ON s.id = su.speaker_id
                WHERE su.event_id = %s
                  AND s.speaker_type IN ('politician', 'official')
                ORDER BY s.name
            """, (eid,))
            rows = cur.fetchall()

        participants = []
        for p in rows:
            participants.append({
                'id':             p[0],
                'name':           p[1],
                'normalized_name': p[2],
                'slug':           p[3],
                'role':           p[4] or '',
                'party':          p[5] or '',
                'speaker_type':   p[6],
                'initials':       _initials(p[1]),
                'color_class':    _color_class(p[0]),
            })
        event['participants'] = participants

        # Fetch verified claims for this event
        cur.execute("""
            SELECT
                c.id, c.claim_text, c.verdict, c.verdict_summary,
                c.confidence_score, c.first_seen,
                s.name AS speaker_name, s.slug AS speaker_slug,
                s.id AS speaker_id,
                a.url AS article_url
            FROM claims c
            LEFT JOIN speakers s ON s.id = c.speaker_id
            LEFT JOIN articles a ON a.id = c.article_id
            WHERE c.event_id = %s
              AND c.verdict IS NOT NULL
              AND c.claim_origin = 'debate_claim'
            ORDER BY c.id ASC
        """, (eid,))
        claims = []
        for c in cur.fetchall():
            (cid, claim_text, verdict, verdict_summary, confidence,
             first_seen, speaker_name, speaker_slug, speaker_id, article_url) = c
            claims.append({
                'id':              cid,
                'claim_text':      claim_text,
                'verdict':         verdict,
                'verdict_label':   VERDICT_LABELS.get(verdict, verdict),
                'verdict_summary': verdict_summary or '',
                'confidence':      confidence,
                'first_seen':      first_seen.strftime('%Y-%m-%d') if first_seen else '',
                'speaker_name':    speaker_name or '',
                'speaker_slug':    speaker_slug or '',
                'speaker_id':      speaker_id,
                'report_url':      ('/report?url=' + article_url) if article_url else '#',
                'initials':        _initials(speaker_name or ''),
                'color_class':     _color_class(speaker_id),
            })
        cur.close()
        return event, claims
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _get_index_stats(get_db_conn):
    """Return aggregate stats for the debates index page."""
    conn = get_db_conn()
    try:
        cur = conn.cursor()
        today = date.today()
        cur.execute("""
            SELECT
                COUNT(*) FILTER (WHERE event_date < %s) AS complete_count,
                COUNT(*) FILTER (WHERE event_date = %s) AS live_count,
                COUNT(*) FILTER (WHERE event_date > %s) AS upcoming_count
            FROM events WHERE is_public = TRUE
        """, (today, today, today))
        row = cur.fetchone()
        complete_count = row[0] or 0
        live_count = row[1] or 0
        upcoming_count = row[2] or 0

        cur.execute("""
            SELECT COUNT(*) FROM claims
            WHERE event_id IN (SELECT id FROM events WHERE is_public = TRUE)
              AND claim_origin = 'debate_claim'
              AND verdict IS NOT NULL
        """)
        total_claims = cur.fetchone()[0] or 0
        cur.close()
        return {
            'live_count':     live_count,
            'upcoming_count': upcoming_count,
            'complete_count': complete_count,
            'total_claims':   total_claims,
        }
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _get_featured_event(get_db_conn):
    """Return the featured event for the homepage debates section.
    Priority: live > upcoming (soonest) > most recent complete.
    """
    conn = get_db_conn()
    try:
        cur = conn.cursor()
        today = date.today()
        # Try live first
        cur.execute("""
            SELECT id, slug, event_name, event_date, venue,
                   transcript_source, methodology_version
            FROM events
            WHERE is_public = TRUE AND event_date = %s
            ORDER BY created_at DESC LIMIT 1
        """, (today,))
        row = cur.fetchone()
        status = 'live'
        if not row:
            # Try upcoming
            cur.execute("""
                SELECT id, slug, event_name, event_date, venue,
                       transcript_source, methodology_version
                FROM events
                WHERE is_public = TRUE AND event_date > %s
                ORDER BY event_date ASC LIMIT 1
            """, (today,))
            row = cur.fetchone()
            status = 'upcoming'
        if not row:
            # Fall back to most recent complete
            cur.execute("""
                SELECT id, slug, event_name, event_date, venue,
                       transcript_source, methodology_version
                FROM events
                WHERE is_public = TRUE AND event_date < %s
                ORDER BY event_date DESC LIMIT 1
            """, (today,))
            row = cur.fetchone()
            status = 'complete'
        if not row:
            cur.close()
            return None
        eid, slug, event_name, event_date, venue, transcript_source, methodology_version = row
        # Claim count
        cur.execute("""
            SELECT COUNT(*) FROM claims
            WHERE event_id = %s AND claim_origin = 'debate_claim' AND verdict IS NOT NULL
        """, (eid,))
        claim_count = cur.fetchone()[0] or 0
        # Participants
        cur.execute("""
            SELECT DISTINCT s.name, s.id
            FROM speaker_utterances su
            JOIN speakers s ON s.id = su.speaker_id
            WHERE su.event_id = %s AND s.speaker_type IN ('politician', 'official')
            ORDER BY s.name
        """, (eid,))
        participants = [{'name': r[0], 'initials': _initials(r[0]), 'color_class': _color_class(r[1])} for r in cur.fetchall()]
        # Recent claims for the live preview strip
        cur.execute("""
            SELECT c.claim_text, c.verdict, s.id AS speaker_id
            FROM claims c
            LEFT JOIN speakers s ON s.id = c.speaker_id
            WHERE c.event_id = %s AND c.verdict IS NOT NULL
              AND c.claim_origin = 'debate_claim'
            ORDER BY c.id DESC LIMIT 3
        """, (eid,))
        recent_claims = []
        for rc in cur.fetchall():
            recent_claims.append({
                'claim_text': rc[0][:80] + '…' if rc[0] and len(rc[0]) > 80 else (rc[0] or ''),
                'verdict':    rc[1],
                'color_class': _color_class(rc[2]),
            })
        cur.close()
        return {
            'slug':              slug,
            'event_name':        event_name,
            'event_date_str':    event_date.strftime('%B %-d, %Y') if event_date else '',
            'venue':             venue or '',
            'transcript_source': transcript_source or '',
            'claim_count':       claim_count,
            'status':            status,
            'participants':      participants,
            'recent_claims':     recent_claims,
            'href':              f'/debates/{slug}',
        }
    finally:
        try:
            conn.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def _derive_status(event_date, today, start_time=None):
    if event_date is None:
        return 'complete'
    if event_date < today:
        return 'complete'
    if event_date == today:
        # Only mark as live if within the debate window (30min before to 3hrs after start)
        if start_time is not None:
            from datetime import datetime, timedelta
            now = datetime.now().time()
            window_start = (datetime.combine(today, start_time) - timedelta(minutes=30)).time()
            window_end   = (datetime.combine(today, start_time) + timedelta(hours=3)).time()
            if window_start <= now <= window_end:
                return 'live'
            else:
                return 'upcoming'
        return 'live'  # no start_time — treat as live all day
    return 'upcoming'


def _initials(name):
    """Extract initials from a name: 'Donald Trump' -> 'DT'"""
    if not name:
        return '?'
    parts = name.strip().split()
    if len(parts) >= 2:
        return (parts[0][0] + parts[-1][0]).upper()
    return name[0].upper()


# Cycle through candidate colors for participants
_COLORS = ['t-violet', 't-pink', 't-cyan', 't-slate']

def _color_class(speaker_id):
    if speaker_id is None:
        return _COLORS[0]
    return _COLORS[speaker_id % len(_COLORS)]


# ---------------------------------------------------------------------------
# Route registration
# ---------------------------------------------------------------------------

def register_debate_routes(app, get_db_conn):

    @app.route("/debates")
    def debates_index():
        events = _get_all_public_events(get_db_conn)
        stats = _get_index_stats(get_db_conn)

        # Group by status
        live_events     = [e for e in events if e['status'] == 'live']
        upcoming_events = [e for e in events if e['status'] == 'upcoming']
        complete_events = [e for e in events if e['status'] == 'complete']

        return render_template(
            "debates.html",
            live_events=live_events,
            upcoming_events=upcoming_events,
            complete_events=complete_events,
            stats=stats,
            methodology_version=METHODOLOGY_VERSION,
        )

    @app.route("/debates/<slug>")
    def debate_detail(slug):
        slug = slug.lower()
        if not SLUG_RE.match(slug):
            abort(400)
        event, claims = _get_event_by_slug(get_db_conn, slug)
        if event is None:
            abort(404)

        # Per-participant verdict breakdown
        breakdown = {}
        for p in event['participants']:
            pid = p['id']
            breakdown[pid] = {v: 0 for v in VERDICT_LABELS}
            breakdown[pid]['total'] = 0
        for c in claims:
            pid = c['speaker_id']
            if pid and pid in breakdown:
                v = c['verdict']
                if v in breakdown[pid]:
                    breakdown[pid][v] += 1
                breakdown[pid]['total'] += 1

        return render_template(
            "debate.html",
            event=event,
            claims=claims,
            breakdown=breakdown,
            methodology_version=METHODOLOGY_VERSION,
        )

    @app.route("/api/debates")
    def api_debates():
        try:
            events = _get_all_public_events(get_db_conn)
            stats = _get_index_stats(get_db_conn)
            return jsonify({
                'events': events,
                'stats':  stats,
                'count':  len(events),
            })
        except Exception as e:
            return jsonify({'error': str(e)}), 500

    @app.route("/api/debates/<slug>")
    def api_debate(slug):
        slug = slug.lower()
        if not SLUG_RE.match(slug):
            return jsonify({'error': 'Invalid slug'}), 400
        try:
            event, claims = _get_event_by_slug(get_db_conn, slug)
            if event is None:
                return jsonify({'error': 'Event not found'}), 404
            event['claims'] = claims
            return jsonify(event)
        except Exception as e:
            return jsonify({'error': str(e)}), 500

    @app.route("/api/debates/featured")
    def api_debates_featured():
        """Returns the featured event for the homepage debates section."""
        try:
            featured = _get_featured_event(get_db_conn)
            if not featured:
                return jsonify({'featured': None})
            return jsonify({'featured': featured})
        except Exception as e:
            return jsonify({'error': str(e)}), 500
