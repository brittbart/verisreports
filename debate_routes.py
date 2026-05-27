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
        # Fetch all participants for all events in one query
        eid_list = [r[0] for r in rows]
        participants_by_event = {eid: [] for eid in eid_list}
        if eid_list:
            cur.execute("""
                SELECT es.event_id, s.id, s.name
                FROM event_speakers es
                JOIN speakers s ON s.id = es.speaker_id
                WHERE es.event_id = ANY(%s)
                  AND es.is_active = TRUE
                  AND s.speaker_type IN ('politician', 'official')
                ORDER BY es.event_id, es.speaker_order
            """, (eid_list,))
            order_counters = {}
            for ev_id, spk_id, spk_name in cur.fetchall():
                idx = order_counters.get(ev_id, 0)
                order_counters[ev_id] = idx + 1
                participants_by_event[ev_id].append({
                    'name':        spk_name,
                    'initials':    _initials(spk_name),
                    'color_class': _listing_color_class(idx),
                })
        cur.close()
        events = []
        today = date.today()
        for row in rows:
            (eid, slug, event_type, event_name, event_date, start_time, timezone, event_subtitle, venue,
             transcript_source, methodology_version, is_public, claim_count) = row
            status = _derive_status(event_date, today, start_time, timezone)
            events.append({
                'id':                  eid,
                'slug':                slug,
                'event_type':          event_type,
                'event_name':          event_name,
                'event_date':          event_date.isoformat() if event_date else None,
                'event_date_str':      event_date.strftime('%B %-d, %Y') if event_date else '',
                'event_date_mo':       event_date.strftime('%b').upper() if event_date else '',
                'event_date_day':      event_date.strftime('%-d') if event_date else '',
                'event_date_year':     event_date.strftime('%Y') if event_date else '',
                'start_time_str':      (start_time.strftime('%-I:%M %p') + ' ' + (timezone or 'ET')) if start_time else 'TBD',
                'event_subtitle':      event_subtitle or '',
                'event_start_iso':     (event_date.strftime('%Y-%m-%dT') + start_time.strftime('%H:%M:00') + {'CT': '-05:00', 'CST': '-06:00', 'CDT': '-05:00', 'ET': '-04:00', 'EST': '-05:00', 'EDT': '-04:00', 'MT': '-06:00', 'MST': '-07:00', 'MDT': '-06:00', 'PT': '-07:00', 'PST': '-08:00', 'PDT': '-07:00'}.get(timezone or 'CT', '-05:00')) if (event_date and start_time) else '',
                'venue':               venue or '',
                'transcript_source':   transcript_source or '',
                'methodology_version': methodology_version or METHODOLOGY_VERSION,
                'claim_count':         claim_count or 0,
                'status':              status,
                'start_time':          start_time.strftime('%H:%M') if start_time else None,
                'timezone':            timezone or 'CT',
                'participants':        participants_by_event.get(eid, []),
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
        status = _derive_status(event_date, today, start_time, timezone)

        event = {
            'id':                  eid,
            'slug':                slug,
            'event_type':          event_type,
            'event_name':          event_name,
            'event_date':          event_date,
            'event_date_str':      event_date.strftime('%B %-d, %Y') if event_date else '',
            'start_time_str':      (start_time.strftime('%-I:%M %p') + ' ' + (timezone or 'ET')) if start_time else 'TBD',
            'event_subtitle':      event_subtitle or '',
            'event_start_iso':     (event_date.strftime('%Y-%m-%dT') + start_time.strftime('%H:%M:00') + {'CT': '-05:00', 'CST': '-06:00', 'CDT': '-05:00', 'ET': '-04:00', 'EST': '-05:00', 'EDT': '-04:00', 'MT': '-06:00', 'MST': '-07:00', 'MDT': '-06:00', 'PT': '-07:00', 'PST': '-08:00', 'PDT': '-07:00'}.get(timezone or 'CT', '-05:00')) if (event_date and start_time) else '',
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
              AND es.is_active = TRUE
              AND s.speaker_type IN ('politician', 'official')
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
        speaker_order_map = {}  # {speaker_id: 0-based index}
        for idx, p in enumerate(rows):
            speaker_order_map[p[0]] = idx
            participants.append({
                'id':             p[0],
                'name':           p[1],
                'normalized_name': p[2],
                'slug':           p[3],
                'role':           p[4] or '',
                'party':          p[5] or '',
                'speaker_type':   p[6],
                'initials':       _initials(p[1]),
                'color_class':    _color_class(p[0], speaker_order_map),
            })
        event['participants'] = participants

        # Fetch verified claims for this event
        cur.execute("""
            SELECT
                c.id, c.claim_text, c.verdict, c.verdict_summary,
                c.confidence_score, c.first_seen,
                c.verdict_status,
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
             first_seen, raw_status, speaker_name, speaker_slug, speaker_id, article_url) = c
            claims.append({
                'id':              cid,
                'claim_text':      claim_text,
                'verdict':         verdict,
                'verdict_label':   VERDICT_LABELS.get(verdict, verdict),
                'verdict_summary': verdict_summary or '',
                'confidence':      confidence,
                'first_seen':      first_seen.strftime('%Y-%m-%d') if first_seen else '',
                'verdict_status':  raw_status if raw_status else 'final',
                'speaker_name':    speaker_name or '',
                'speaker_slug':    speaker_slug or '',
                'speaker_id':      speaker_id,
                'report_url':      ('/report?url=' + article_url) if article_url else '#',
                'initials':        _initials(speaker_name or ''),
                'color_class':     _color_class(speaker_id, speaker_order_map),
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
            SELECT event_date, start_time, timezone
            FROM events WHERE is_public = TRUE
        """)
        rows = cur.fetchall()
        complete_count = live_count = upcoming_count = 0
        for event_date, start_time, timezone in rows:
            s = _derive_status(event_date, today, start_time, timezone)
            if s == 'live':
                live_count += 1
            elif s == 'upcoming':
                upcoming_count += 1
            else:
                complete_count += 1

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
        # Try live first — must be within the time window
        cur.execute("""
            SELECT id, slug, event_name, event_date, venue,
                   transcript_source, methodology_version, start_time, timezone
            FROM events
            WHERE is_public = TRUE AND event_date = %s
            ORDER BY created_at DESC
        """, (today,))
        live_rows = cur.fetchall()
        row = None
        for lr in live_rows:
            if _derive_status(lr[3], today, lr[7], lr[8]) == 'live':
                row = lr[:7]
                break
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
        featured_rows = cur.fetchall()
        participants = [
            {'name': r[0], 'initials': _initials(r[0]), 'color_class': _color_class(r[1], {r[1]: i})}
            for i, r in enumerate(featured_rows)
        ]
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

def _derive_status(event_date, today, start_time=None, timezone=None, _now=None):
    if event_date is None:
        return 'complete'
    from datetime import datetime, timedelta, timezone as tz
    # UTC offsets for event timezone
    tz_offsets = {
        'ET': -4, 'EST': -5, 'EDT': -4,
        'CT': -5, 'CST': -6, 'CDT': -5,
        'MT': -6, 'MST': -7, 'MDT': -6,
        'PT': -7, 'PST': -8, 'PDT': -7,
    }
    offset_hours = tz_offsets.get(timezone or 'CT', -5)
    event_tz = tz(timedelta(hours=offset_hours))
    now_utc = _now if _now is not None else datetime.now(tz.utc)
    # Derive today in the event's local timezone (not server UTC)
    today_local = now_utc.astimezone(event_tz).date()
    if event_date < today_local:
        return 'complete'
    if event_date == today_local:
        # Only mark as live if within the debate window (30min before to 3hrs after start)
        if start_time is not None:
            event_start = datetime.combine(event_date, start_time).replace(tzinfo=event_tz)
            window_start = event_start - timedelta(minutes=30)
            window_end   = event_start + timedelta(hours=3)
            if window_start <= now_utc <= window_end:
                return 'live'
            elif now_utc > window_end:
                return 'complete'
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


# Candidate color classes — index-based (first speaker = cand-a, second = cand-b)
_CAND_CLASSES = ['cand-a', 'cand-b', 'cand-c', 'cand-d']

# Listing page uses t-* classes (dx-pmono system)
_LISTING_COLORS = ['t-violet', 't-pink', 't-cyan', 't-amber']

def _listing_color_class(index):
    return _LISTING_COLORS[index % len(_LISTING_COLORS)]

def _color_class(speaker_id, speaker_order_map=None):
    """Return cand-a/cand-b/etc based on speaker's position in the event.
    speaker_order_map: dict of {speaker_id: 0-based index} for this event.
    Falls back to speaker_id modulo if no map provided.
    """
    if speaker_order_map and speaker_id in speaker_order_map:
        return _CAND_CLASSES[speaker_order_map[speaker_id] % len(_CAND_CLASSES)]
    if speaker_id is None:
        return _CAND_CLASSES[0]
    return _CAND_CLASSES[speaker_id % len(_CAND_CLASSES)]


# ---------------------------------------------------------------------------
# Route registration
# ---------------------------------------------------------------------------

def register_debate_routes(app, get_db_conn):
    @app.route("/debates")
    def debates_about():
        from flask import send_from_directory
        import os
        return send_from_directory(
            os.path.join(os.path.dirname(__file__), 'static'),
            'debates-explainer.html'
        )

    @app.route("/debates/list")
    def debates_index():
        events = _get_all_public_events(get_db_conn)
        stats = _get_index_stats(get_db_conn)

        # Group by status
        from datetime import datetime, timedelta, timezone as tz
        TZ_OFFSETS = {'ET':-4,'EST':-5,'EDT':-4,'CT':-5,'CST':-6,'CDT':-5,'MT':-6,'MST':-7,'MDT':-6,'PT':-7,'PST':-8,'PDT':-7}
        now_utc = datetime.now(tz.utc)
        def _is_soon(e):
            """True if event starts within 24 hours."""
            ed = e.get('event_date')
            st = e.get('start_time')
            etz = e.get('timezone') or 'CT'
            if not ed or not st:
                return False
            offset = TZ_OFFSETS.get(etz, -5)
            event_tz_obj = tz(timedelta(hours=offset))
            # ed and st may be strings after JSON serialization fix — parse if needed
            if isinstance(ed, str):
                ed = datetime.strptime(ed, '%Y-%m-%d').date()
            if isinstance(st, str):
                st = datetime.strptime(st, '%H:%M').time()
            event_start = datetime.combine(ed, st).replace(tzinfo=event_tz_obj)
            delta = (event_start - now_utc).total_seconds()
            return 0 < delta <= 86400
        live_events     = [e for e in events if e['status'] == 'live']
        upcoming_events = sorted([e for e in events if e['status'] == 'upcoming'], key=lambda e: e['event_date'] or date.max)
        complete_events = sorted([e for e in events if e['status'] == 'complete'], key=lambda e: e['event_date'] or date.min, reverse=True)
        for e in upcoming_events:
            e['is_soon'] = _is_soon(e)

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
            breakdown[pid]['provisional_count'] = 0
            breakdown[pid]['final_count'] = 0
        for c in claims:
            pid = c['speaker_id']
            if pid and pid in breakdown:
                v = c['verdict']
                if v in breakdown[pid]:
                    breakdown[pid][v] += 1
                breakdown[pid]['total'] += 1
                if c.get('verdict_status') == 'provisional':
                    breakdown[pid]['provisional_count'] += 1
                else:
                    breakdown[pid]['final_count'] += 1

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

    @app.route("/api/debates/<slug>/stats")
    def api_debate_stats(slug):
        """Real-time pipeline stats for a debate event. Operator monitoring during live coverage."""
        slug = slug.lower()
        if not SLUG_RE.match(slug):
            return jsonify({'error': 'Invalid slug'}), 400
        try:
            conn = get_db_conn()
            cur = conn.cursor()
            # Get event_id from slug
            cur.execute("SELECT id, is_public FROM events WHERE slug = %s", (slug,))
            row = cur.fetchone()
            if not row:
                return jsonify({'error': 'Event not found'}), 404
            event_id, is_public = row
            # Utterance stats
            cur.execute("""
                SELECT
                    COUNT(*) AS total,
                    COUNT(CASE WHEN processed_at IS NOT NULL THEN 1 END) AS processed,
                    MAX(created_at) AS latest_at
                FROM speaker_utterances
                WHERE event_id = %s
            """, (event_id,))
            urow = cur.fetchone()
            utterances_total = urow[0] or 0
            utterances_processed = urow[1] or 0
            last_utterance_at = urow[2].isoformat() if urow[2] else None
            # Claim stats
            cur.execute("""
                SELECT
                    COUNT(*) AS total,
                    COUNT(CASE WHEN verdict IS NOT NULL THEN 1 END) AS with_verdict,
                    COUNT(CASE WHEN verdict_status = 'provisional' THEN 1 END) AS provisional,
                    COUNT(CASE WHEN verdict_status = 'final' OR (verdict IS NOT NULL AND verdict_status IS NULL) THEN 1 END) AS final,
                    COUNT(CASE WHEN verdict IS NULL THEN 1 END) AS pending_verification
                FROM claims
                WHERE event_id = %s AND claim_origin = 'debate_claim'
            """, (event_id,))
            crow = cur.fetchone()
            claims_total = crow[0] or 0
            claims_with_verdict = crow[1] or 0
            claims_provisional = crow[2] or 0
            claims_final = crow[3] or 0
            verification_pending = crow[4] or 0
            # Stream active: any utterance in last 3 minutes
            cur.execute("""
                SELECT COUNT(*) FROM speaker_utterances
                WHERE event_id = %s AND created_at > NOW() - INTERVAL '3 minutes'
            """, (event_id,))
            stream_active = (cur.fetchone()[0] or 0) > 0
            # Per-speaker provisional/final breakdown
            cur.execute("""
                SELECT c.speaker_id, s.name,
                       COUNT(CASE WHEN c.verdict_status = 'provisional' THEN 1 END) AS provisional,
                       COUNT(CASE WHEN c.verdict_status = 'final'
                                   OR (c.verdict IS NOT NULL AND c.verdict_status IS NULL) THEN 1 END) AS final
                FROM claims c
                JOIN speakers s ON s.id = c.speaker_id
                WHERE c.event_id = %s
                  AND c.claim_origin = 'debate_claim'
                  AND c.verdict IS NOT NULL
                  AND s.speaker_type IN ('politician', 'official')
                GROUP BY c.speaker_id, s.name
            """, (event_id,))
            speaker_rows = cur.fetchall()
            speakers_breakdown = [
                {
                    'speaker_id': row[0],
                    'name': row[1],
                    'claims_provisional': row[2],
                    'claims_final': row[3],
                }
                for row in speaker_rows
            ]
            cur.close()
            conn.close()
            return jsonify({
                'event_id': event_id,
                'slug': slug,
                'is_public': is_public,
                'stream_active': stream_active,
                'utterances_captured': utterances_total,
                'utterances_processed': utterances_processed,
                'extraction_pending': utterances_total - utterances_processed,
                'claims_extracted': claims_total,
                'claims_with_verdict': claims_with_verdict,
                'claims_provisional': claims_provisional,
                'claims_final': claims_final,
                'verification_pending': verification_pending,
                'last_utterance_at': last_utterance_at,
                'speakers': speakers_breakdown,
            })
        except Exception as e:
            return jsonify({'error': str(e)}), 500
