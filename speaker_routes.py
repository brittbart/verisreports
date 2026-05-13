"""
Speaker detail routes (v1.7):
  /speaker/<slug>   — HTML page
  /speakers         — Speaker index page (HTML)
"""
import re
from flask import render_template, abort, request
from api_leaderboard import (
    compute_score,
    compute_score_band,
    compute_tier,
    METHODOLOGY_VERSION,
    INCLUSION_THRESHOLD,
    WEIGHTS,
    VERDICT_LABELS,
    SPEAKER_BY_ID_SQL,
    SPEAKER_SCORE_SQL,
    SPEAKER_RECENT_CLAIMS_SQL,
)

SLUG_RE = re.compile(r"^[a-z0-9-]+$")


def _get_speaker_by_slug(get_db_conn, slug):
    """Return speaker row dict by slug, or None if not found."""
    conn = get_db_conn()
    try:
        cur = conn.cursor()
        # Get speaker id from slug first
        cur.execute("SELECT id FROM speakers WHERE slug = %s", (slug,))
        row = cur.fetchone()
        if not row:
            return None
        speaker_id = row[0]
        cur.execute(SPEAKER_BY_ID_SQL, (speaker_id,))
        row = cur.fetchone()
        cur.close()
        return row
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _get_speaker_recent_claims(get_db_conn, speaker_id, limit=20):
    """Return recent claims for a speaker."""
    conn = get_db_conn()
    try:
        cur = conn.cursor()
        cur.execute(SPEAKER_RECENT_CLAIMS_SQL, (speaker_id,))
        rows = cur.fetchall()
        cur.close()
        out = []
        for r in rows:
            cid, claim_text, verdict, verdict_summary, first_seen, source_name, article_id, article_url = r
            out.append({
                'id':              cid,
                'claim_text':      claim_text,
                'verdict':         verdict,
                'verdict_label':   VERDICT_LABELS.get(verdict, verdict),
                'verdict_summary': verdict_summary,
                'first_seen':      first_seen.strftime('%Y-%m-%d') if first_seen else None,
                'source_name':     source_name,
                'article_id':      article_id,
                'report':          ('/report?url=' + article_url) if article_url else '#',
            })
        return out
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _build_speaker_view(row, claims):
    """Build the view dict from a SPEAKER_BY_ID_SQL row."""
    (
        speaker_id, name, normalized_name, slug, speaker_type, role, party,
        current_office, photo_url,
        supported, plausible, corroborated, overstated, disputed, not_supported,
        not_verifiable, opinion, verdict_count, scoreable_count, weighted_sum,
        first_verdict_at, last_verdict_at
    ) = row

    score = compute_score(weighted_sum, scoreable_count)
    tier = compute_tier(scoreable_count or 0)
    band = compute_score_band(score)
    is_scored = (scoreable_count or 0) >= INCLUSION_THRESHOLD

    return {
        'id':               speaker_id,
        'name':             name,
        'normalized_name':  normalized_name,
        'slug':             slug,
        'speaker_type':     speaker_type,
        'role':             role,
        'party':            party,
        'current_office':   current_office,
        'photo_url':        photo_url,
        'score':            score,
        'score_band':       band,
        'tier':             tier,
        'state':            'scored' if is_scored else 'sub_threshold',
        'scored_as_of':     last_verdict_at.strftime('%Y-%m-%d') if last_verdict_at else None,
        'verdict_count':    verdict_count or 0,
        'scoreable_count':  scoreable_count or 0,
        'excluded_count':   (verdict_count or 0) - (scoreable_count or 0),
        'threshold':        INCLUSION_THRESHOLD,
        'verdict_breakdown': {
            'supported':     supported or 0,
            'plausible':     plausible or 0,
            'corroborated':  corroborated or 0,
            'overstated':    overstated or 0,
            'disputed':      disputed or 0,
            'not_supported': not_supported or 0,
        },
        'excluded_breakdown': {
            'opinion':        opinion or 0,
            'not_verifiable': not_verifiable or 0,
        },
        'verdicts': claims,
    }


def _get_all_speakers(get_db_conn):
    """Return all speakers with at least 1 verdict, sorted by scoreable_count desc."""
    conn = get_db_conn()
    try:
        cur = conn.cursor()
        cur.execute(SPEAKER_SCORE_SQL)
        rows = cur.fetchall()
        cur.close()
        speakers = []
        for row in rows:
            (
                speaker_id, name, normalized_name, slug, speaker_type, role, party,
                current_office, photo_url,
                supported, plausible, corroborated, overstated, disputed, not_supported,
                not_verifiable, opinion, verdict_count, scoreable_count, weighted_sum,
                first_verdict_at, last_verdict_at
            ) = row
            score = compute_score(weighted_sum, scoreable_count)
            speakers.append({
                'id':             speaker_id,
                'name':           name,
                'slug':           slug,
                'speaker_type':   speaker_type,
                'party':          party,
                'current_office': current_office,
                'score':          score,
                'score_band':     compute_score_band(score),
                'tier':           compute_tier(scoreable_count or 0),
                'scoreable_count': scoreable_count or 0,
                'verdict_count':  verdict_count or 0,
                'scored_as_of':   last_verdict_at.strftime('%Y-%m-%d') if last_verdict_at else None,
            })
        # Sort: scored first (by score desc), then unscored by verdict count desc
        speakers.sort(
            key=lambda s: (s['score'] is not None, s['score'] or 0, s['scoreable_count']),
            reverse=True
        )
        return speakers
    finally:
        try:
            conn.close()
        except Exception:
            pass


def register_speaker_routes(app, get_db_conn):

    @app.route("/speaker/<slug>")
    def speaker_detail(slug):
        slug = slug.lower()
        if not SLUG_RE.match(slug):
            abort(400, description="Invalid speaker identifier")
        row = _get_speaker_by_slug(get_db_conn, slug)
        if row is None:
            abort(404)
        claims = _get_speaker_recent_claims(get_db_conn, row[0])
        speaker = _build_speaker_view(row, claims)
        return render_template(
            "speaker.html",
            speaker=speaker,
            methodology_version=METHODOLOGY_VERSION,
            inclusion_threshold=INCLUSION_THRESHOLD,
        )

    @app.route("/speakers")
    def speakers_index():
        speakers = _get_all_speakers(get_db_conn)
        return render_template(
            "speakers.html",
            speakers=speakers,
            methodology_version=METHODOLOGY_VERSION,
            total=len(speakers),
        )
