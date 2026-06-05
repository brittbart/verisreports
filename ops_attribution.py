"""
ops_attribution.py — One-click attribution correction page.

Shows claims flagged by A5 (semantic check) or reattribute_llm.py,
with a "Correct" button that swaps speaker, writes revision_history,
sets correction_note, and kicks the refresh.

Registered as a Blueprint in api.py.
"""

from flask import Blueprint, jsonify, request, render_template_string
import json
import os
from datetime import datetime, timezone

bp = Blueprint('ops_attribution', __name__)


def _ops_auth():
    """Match api.py pattern: username='admin', password=OPS_PASSWORD."""
    from flask import Response
    auth = request.authorization
    expected_pw = os.environ.get('OPS_PASSWORD')
    if not expected_pw:
        return Response('OPS_PASSWORD not configured on server', 503)
    if not auth or auth.username != 'admin' or auth.password != expected_pw:
        return Response('Unauthorized', 401,
                        {'WWW-Authenticate': 'Basic realm="Verum Signal Ops"'})
    return None


@bp.route('/ops/attribution', methods=['GET'])
def ops_attribution_page():
    auth_err = _ops_auth()
    if auth_err:
        return auth_err
    return render_template_string(_PAGE_HTML)


@bp.route('/api/ops/attribution', methods=['GET'])
def api_ops_attribution():
    """Return claims needing attribution review."""
    auth_err = _ops_auth()
    if auth_err:
        return auth_err

    import psycopg2
    conn = psycopg2.connect(
        dbname=os.environ.get('DB_NAME'), user=os.environ.get('DB_USER'),
        password=os.environ.get('DB_PASSWORD'), host=os.environ.get('DB_HOST'),
        port=os.environ.get('DB_PORT', '5432'), connect_timeout=10)
    conn.autocommit = True
    cur = conn.cursor()

    event_id = request.args.get('event_id', type=int)

    # Get all claims with attribution flags OR correction notes
    sql = """
        SELECT c.id, c.claim_text, c.speaker_id, s.name AS speaker_name,
               c.event_id, e.event_name, c.verdict, c.correction_note,
               c.revision_history, c.verdict_status
        FROM claims c
        JOIN speakers s ON s.id = c.speaker_id
        JOIN events e ON e.id = c.event_id
        WHERE c.claim_origin = 'debate_claim'
          AND (
            c.revision_history::text LIKE '%attribution_flagged%'
            OR c.correction_note IS NOT NULL
          )
    """
    params = []
    if event_id:
        sql += " AND c.event_id = %s"
        params.append(event_id)
    sql += " ORDER BY c.id DESC LIMIT 200"

    cur.execute(sql, params)
    rows = cur.fetchall()

    # Get available speakers for correction dropdown
    cur.execute("""
        SELECT DISTINCT s.id, s.name
        FROM event_speakers es
        JOIN speakers s ON s.id = es.speaker_id
        WHERE s.speaker_type IN ('politician', 'official')
        ORDER BY s.name
    """)
    speakers = [{'id': r[0], 'name': r[1]} for r in cur.fetchall()]

    claims = []
    for r in rows:
        cid, text, sid, sname, eid, ename, verdict, cnote, revhist, vstatus = r
        flag_reason = ''
        if revhist:
            entries = revhist if isinstance(revhist, list) else []
            for entry in entries:
                if entry.get('action') == 'attribution_flagged':
                    flag_reason = entry.get('reason', '')
                    break
        claims.append({
            'id': cid,
            'claim_text': text[:120],
            'speaker_id': sid,
            'speaker_name': sname,
            'event_id': eid,
            'event_name': ename,
            'verdict': verdict,
            'correction_note': cnote,
            'flag_reason': flag_reason,
            'corrected': cnote is not None,
        })

    cur.close()
    return jsonify({'claims': claims, 'speakers': speakers})


@bp.route('/api/ops/attribution/correct', methods=['POST'])
def api_ops_attribution_correct():
    """Correct a claim's speaker attribution."""
    auth_err = _ops_auth()
    if auth_err:
        return auth_err

    data = request.get_json()
    claim_id = data.get('claim_id')
    new_speaker_id = data.get('new_speaker_id')
    if not claim_id or not new_speaker_id:
        return jsonify({'error': 'claim_id and new_speaker_id required'}), 400

    import psycopg2
    conn = psycopg2.connect(
        dbname=os.environ.get('DB_NAME'), user=os.environ.get('DB_USER'),
        password=os.environ.get('DB_PASSWORD'), host=os.environ.get('DB_HOST'),
        port=os.environ.get('DB_PORT', '5432'), connect_timeout=10)
    conn.autocommit = True
    cur = conn.cursor()

    # Get current state
    cur.execute("""
        SELECT c.speaker_id, s.name, c.event_id
        FROM claims c
        JOIN speakers s ON s.id = c.speaker_id
        WHERE c.id = %s
    """, (claim_id,))
    row = cur.fetchone()
    if not row:
        return jsonify({'error': 'claim not found'}), 404
    old_sid, old_name, event_id = row

    # Get new speaker name
    cur.execute("SELECT name FROM speakers WHERE id = %s", (new_speaker_id,))
    new_row = cur.fetchone()
    if not new_row:
        return jsonify({'error': 'new speaker not found'}), 404
    new_name = new_row[0]

    if old_sid == new_speaker_id:
        return jsonify({'error': 'same speaker — no change needed'}), 400

    # Update claim
    revision_entry = json.dumps([{
        'action': 'ops_manual_correction',
        'old_speaker_id': old_sid,
        'new_speaker_id': new_speaker_id,
        'timestamp': datetime.now(timezone.utc).isoformat(),
    }])
    correction_note = (
        'Correction: Originally attributed to ' + old_name +
        '. Attribution corrected to ' + new_name +
        ' following post-debate review.'
    )

    cur.execute("""
        UPDATE claims SET
            speaker_id = %s,
            speaker = %s,
            revision_history = COALESCE(revision_history, '[]'::jsonb) || %s::jsonb,
            correction_note = %s
        WHERE id = %s
    """, (new_speaker_id, new_name, revision_entry, correction_note, claim_id))

    # Also update the source utterance if available
    cur.execute("""
        UPDATE speaker_utterances SET speaker_id = %s
        WHERE id = (SELECT utterance_id FROM claims WHERE id = %s)
          AND utterance_id IS NOT NULL
    """, (new_speaker_id, claim_id))

    conn.commit()
    cur.close()

    return jsonify({
        'ok': True,
        'claim_id': claim_id,
        'old_speaker': old_name,
        'new_speaker': new_name,
        'message': 'Corrected. Run railway_api_refresh.py to propagate.',
    })


_PAGE_HTML = """<!DOCTYPE html>
<html><head>
<meta charset="utf-8">
<title>Attribution Review — Verum Signal Ops</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #0a0a0f; color: #e5e5e5; padding: 24px; }
  .nav-links { margin-bottom: 20px; display: flex; gap: 16px; flex-wrap: wrap; }
  .nav-links a { color: #a855f7; text-decoration: none; font-size: 13px; }
  h1 { font-size: 20px; font-weight: 600; margin-bottom: 16px; }
  .claim-card { background: rgba(255,255,255,0.03); border: 0.5px solid rgba(255,255,255,0.1); border-radius: 10px; padding: 16px; margin-bottom: 12px; }
  .claim-card.corrected { border-color: rgba(74, 222, 128, 0.3); }
  .claim-card.flagged { border-color: rgba(251, 191, 36, 0.3); }
  .claim-meta { font-size: 12px; color: #888; margin-bottom: 8px; display: flex; gap: 12px; flex-wrap: wrap; }
  .claim-text { font-size: 14px; line-height: 1.5; margin-bottom: 8px; }
  .flag-reason { font-size: 12px; color: #fbbf24; font-style: italic; margin-bottom: 8px; padding: 6px 10px; background: rgba(251,191,36,0.06); border-left: 2px solid rgba(251,191,36,0.4); border-radius: 0 4px 4px 0; }
  .correction-note { font-size: 12px; color: #4ade80; font-style: italic; margin-bottom: 8px; }
  .action-row { display: flex; align-items: center; gap: 10px; margin-top: 8px; }
  select { background: #1a1a2e; color: #e5e5e5; border: 0.5px solid rgba(255,255,255,0.15); border-radius: 6px; padding: 6px 10px; font-size: 13px; }
  button { background: #a855f7; color: white; border: none; border-radius: 6px; padding: 6px 14px; font-size: 13px; cursor: pointer; }
  button:hover { background: #9333ea; }
  button:disabled { opacity: 0.4; cursor: not-allowed; }
  .btn-done { background: #22c55e; }
  .status { font-size: 12px; margin-left: 8px; }
  .empty { text-align: center; color: #666; padding: 40px; }
  .v-pill { display: inline-block; font-size: 10px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.06em; padding: 2px 8px; border-radius: 100px; }
</style>
</head><body>
<div class="nav-links">
  <a href="/ops">&larr; Pipeline</a>
  <a href="/ops/history">History</a>
  <a href="/ops/insights">Insights</a>
  <a href="/ops/changelog">Changelog</a>
  <a href="/ops/outlets">Outlets</a>
  <a href="/ops/queue">Queue</a>
  <a href="/ops/disputes">Disputes</a>
  <a href="/ops/api-usage">API</a>
  <a href="/ops/attribution" style="color:#fbbf24;font-weight:600">Attribution</a>
</div>
<h1>Attribution Review</h1>
<div id="claims-list"><div class="empty">Loading...</div></div>
<script>
async function load() {
  const resp = await fetch('/api/ops/attribution', {headers: {'Authorization': 'Basic ' + btoa(document.cookie.match(/ops_auth=([^;]+)/)?.[1] || prompt('ops user:pass'))}});
  if (!resp.ok) { document.getElementById('claims-list').innerHTML = '<div class="empty">Auth failed or error</div>'; return; }
  const data = await resp.json();
  const el = document.getElementById('claims-list');
  if (!data.claims.length) { el.innerHTML = '<div class="empty">No flagged or corrected claims</div>'; return; }

  const speakerOpts = data.speakers.map(s => '<option value="'+s.id+'">'+s.name+' ('+s.id+')</option>').join('');

  el.innerHTML = data.claims.map(c => {
    const cls = c.corrected ? 'corrected' : (c.flag_reason ? 'flagged' : '');
    return '<div class="claim-card ' + cls + '" data-id="'+c.id+'">' +
      '<div class="claim-meta"><span>Claim #'+c.id+'</span><span>'+c.event_name+'</span><span>Speaker: <strong>'+c.speaker_name+'</strong> ('+c.speaker_id+')</span><span class="v-pill">'+(c.verdict||'')+'</span></div>' +
      '<div class="claim-text">&ldquo;'+c.claim_text+'&rdquo;</div>' +
      (c.flag_reason ? '<div class="flag-reason">'+c.flag_reason+'</div>' : '') +
      (c.correction_note ? '<div class="correction-note">'+c.correction_note+'</div>' : '') +
      (c.corrected ? '<div class="status btn-done" style="color:#4ade80;">&#10003; Corrected</div>' :
        '<div class="action-row"><label style="font-size:12px;">Correct to:</label><select class="new-speaker">'+speakerOpts+'</select><button onclick="correct('+c.id+',this)">Correct</button><span class="status"></span></div>'
      ) +
      '</div>';
  }).join('');
}

async function correct(claimId, btn) {
  const card = btn.closest('.claim-card');
  const newSid = parseInt(card.querySelector('.new-speaker').value);
  const status = card.querySelector('.status');
  btn.disabled = true;
  status.textContent = 'Correcting...';

  const creds = document.cookie.match(/ops_auth=([^;]+)/)?.[1] || prompt('ops user:pass');
  const resp = await fetch('/api/ops/attribution/correct', {
    method: 'POST',
    headers: {'Content-Type': 'application/json', 'Authorization': 'Basic ' + btoa(creds)},
    body: JSON.stringify({claim_id: claimId, new_speaker_id: newSid})
  });
  const data = await resp.json();
  if (data.ok) {
    status.textContent = data.old_speaker + ' → ' + data.new_speaker;
    status.style.color = '#4ade80';
    card.classList.add('corrected');
    card.classList.remove('flagged');
  } else {
    status.textContent = data.error || 'Failed';
    status.style.color = '#ef4444';
    btn.disabled = false;
  }
}

load();
</script>
</body></html>
"""
