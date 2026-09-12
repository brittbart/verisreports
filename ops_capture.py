"""
ops_capture.py — Capture panel for the veris-capture worker.

/ops/capture shows every upcoming debate event with its stream, window, capture flag and the worker's capture_status row,
and offers Start / Stop. The page never spawns processes: Start sets events.capture_enabled = TRUE (the worker picks it up
on its next tick); Stop sets it FALSE (the worker terminates that child's process group and records 'stopped').
Basic auth: username 'admin', password OPS_PASSWORD (503 when unset), same as every /ops route. Registered in api.py.
"""
from flask import Blueprint, jsonify, request, render_template_string
import os
from datetime import datetime, timezone, timedelta

bp = Blueprint('ops_capture', __name__)


def _ops_auth():
    from flask import Response
    auth = request.authorization
    expected_pw = os.environ.get('OPS_PASSWORD')
    if not expected_pw:
        return Response('OPS_PASSWORD not configured on server', 503)
    if not auth or auth.username != 'admin' or auth.password != expected_pw:
        return Response('Unauthorized', 401, {'WWW-Authenticate': 'Basic realm="Verum Signal Ops"'})
    return None


def _conn():
    import psycopg2
    return psycopg2.connect(dbname=os.environ.get('DB_NAME'), user=os.environ.get('DB_USER'), password=os.environ.get('DB_PASSWORD'),
                            host=os.environ.get('DB_HOST'), port=os.environ.get('DB_PORT'), connect_timeout=10)


def _rows():
    import event_time
    conn = _conn(); cur = conn.cursor()
    cur.execute("""
        SELECT e.id, e.slug, e.event_name, e.event_date, e.start_time, e.timezone, e.stream_url, e.capture_enabled,
               cs.state, cs.pid, cs.host, cs.started_at, cs.last_seen, cs.exit_code, cs.log_path, cs.dry_run, cs.r2_keys,
               (SELECT string_agg(es.speaker_id::text, ',' ORDER BY es.speaker_order) FROM event_speakers es WHERE es.event_id = e.id) AS roster
        FROM events e LEFT JOIN capture_status cs ON cs.event_id = e.id
        WHERE e.event_type = 'debate' AND e.event_date >= CURRENT_DATE - 1
        ORDER BY e.event_date, e.start_time, e.id""")
    out = []; now = datetime.now(timezone.utc)
    for r in cur.fetchall():
        eid, slug, name, d, t, tz, url, enabled, state, pid, host, started, seen, rc, log, dry, keys, roster = r
        w = event_time.window(d, t, tz) if t else None
        start = event_time.start_dt(d, t, tz) if t else None
        out.append({'id': eid, 'slug': slug, 'name': name, 'date': str(d), 'time': str(t)[:5] if t else None, 'tz': tz,
                    'start_utc': start.isoformat() if start else None, 'window_open': bool(w and w[0] <= now <= w[1]),
                    'stream_url': url, 'capture_enabled': bool(enabled), 'roster': roster,
                    'state': state, 'pid': pid, 'host': host, 'started_at': started.isoformat() if started else None,
                    'last_seen': seen.isoformat() if seen else None, 'seen_age_s': int((now - seen).total_seconds()) if seen else None,
                    'exit_code': rc, 'log_path': os.path.basename(log) if log else None, 'dry_run': dry, 'r2_keys': keys or []})
    conn.close(); return out


@bp.route('/ops/capture', methods=['GET'])
def ops_capture_page():
    err = _ops_auth()
    if err: return err
    return render_template_string(_PAGE_HTML)


@bp.route('/api/ops/capture', methods=['GET'])
def api_ops_capture():
    err = _ops_auth()
    if err: return err
    return jsonify({'events': _rows(), 'now': datetime.now(timezone.utc).isoformat()})


@bp.route('/api/ops/capture/<int:event_id>/<action>', methods=['POST'])
def api_ops_capture_action(event_id, action):
    err = _ops_auth()
    if err: return err
    if action not in ('start', 'stop'):
        return jsonify({'error': 'action must be start or stop'}), 400
    conn = _conn(); cur = conn.cursor()
    cur.execute("SELECT stream_url, capture_enabled FROM events WHERE id = %s AND event_type = 'debate'", (event_id,))
    row = cur.fetchone()
    if not row: conn.close(); return jsonify({'error': 'no such debate event'}), 404
    if action == 'start' and not row[0]:
        conn.close(); return jsonify({'error': 'no stream_url pinned - pin the stream before starting'}), 409
    cur.execute("UPDATE events SET capture_enabled = %s WHERE id = %s", (action == 'start', event_id))
    conn.commit(); conn.close()
    return jsonify({'ok': True, 'event_id': event_id, 'capture_enabled': action == 'start',
                    'note': 'the worker acts on its next tick (up to 60 s)'})


_PAGE_HTML = r"""<!doctype html><html><head><meta charset="utf-8"><title>Verum Signal — Capture</title>
<style>body{font-family:system-ui,sans-serif;margin:24px;color:#111}h1{font-size:20px}table{border-collapse:collapse;width:100%}
th,td{padding:6px 8px;border-bottom:1px solid #ddd;font-size:13px;text-align:left;vertical-align:top}th{background:#f4f4f4}
.on{color:#0a7}.off{color:#999}.running{background:#e8f7ee}.exited,.killed,.stopped{background:#f7f0e8}.waiting{background:#fff8e1}
button{padding:4px 10px;font-size:12px;cursor:pointer}small{color:#666}#msg{margin:8px 0;color:#a00}</style></head><body>
<h1>Capture — veris-capture worker</h1>
<p><small>Start sets capture_enabled; the worker spawns the watchdog on its next tick (≤60 s) once the event's window opens (start −30 min). Stop terminates that capture. Refreshes every 15 s.</small></p>
<div id="msg"></div><table id="t"><thead><tr><th>Event</th><th>When</th><th>Stream</th><th>Roster</th><th>Flag</th><th>Worker</th><th>Last seen</th><th>Exit</th><th>R2</th><th></th></tr></thead><tbody></tbody></table>
<script>
async function load(){const r=await fetch('/api/ops/capture');if(!r.ok){document.getElementById('msg').textContent='load failed '+r.status;return;}
const d=await r.json();const tb=document.querySelector('#t tbody');tb.innerHTML='';
for(const e of d.events){const tr=document.createElement('tr');tr.className=e.state||'';
const stream=e.stream_url?('<a href="'+e.stream_url+'" target="_blank">pinned</a>'):'<span class=off>none</span>';
const flag=e.capture_enabled?'<span class=on>enabled</span>':'<span class=off>off</span>';
const win=e.window_open?' <b>[window open]</b>':'';
const btn=e.capture_enabled?'<button onclick="act('+e.id+',\'stop\')">Stop</button>':'<button onclick="act('+e.id+',\'start\')">Start</button>';
tr.innerHTML='<td>'+e.id+' '+e.name+'<br><small>'+e.slug+'</small></td><td>'+e.date+' '+(e.time||'')+' '+(e.tz||'')+win+'</td><td>'+stream+'</td><td>'+(e.roster||'')+'</td><td>'+flag+'</td><td>'+(e.state||'—')+(e.pid?' pid '+e.pid:'')+(e.host?'<br><small>'+e.host+'</small>':'')+'</td><td>'+(e.seen_age_s!=null?e.seen_age_s+' s ago':'—')+'</td><td>'+(e.exit_code!=null?e.exit_code:'—')+'</td><td>'+(e.r2_keys.length)+'</td><td>'+btn+'</td>';
tb.appendChild(tr);}}
async function act(id,a){const r=await fetch('/api/ops/capture/'+id+'/'+a,{method:'POST'});const j=await r.json();document.getElementById('msg').textContent=r.ok?('event '+id+': '+a+' — '+j.note):('error: '+(j.error||r.status));load();}
load();setInterval(load,15000);
</script></body></html>"""
