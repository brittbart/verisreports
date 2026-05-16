"""
admin_routes.py — Verum Signal Debate Admin Dashboard
Routes: /admin, /api/admin/events, /api/admin/speakers, /api/admin/stream
"""

import os
import json
import re
from datetime import date, datetime
from flask import request, jsonify, Response

# ---------------------------------------------------------------------------
# Auth (same pattern as ops)
# ---------------------------------------------------------------------------

def _admin_auth():
    """Return 401 response if not authenticated, else None."""
    import base64
    auth = request.headers.get('Authorization', '')
    if not auth.startswith('Basic '):
        return Response(
            'Verum Signal Admin',
            401,
            {'WWW-Authenticate': 'Basic realm="Verum Signal Admin"'}
        )
    try:
        decoded = base64.b64decode(auth[6:]).decode('utf-8')
        _, password = decoded.split(':', 1)
        ops_pw = os.environ.get('OPS_PASSWORD', '')
        if password != ops_pw:
            raise ValueError
    except Exception:
        return Response(
            'Unauthorized',
            401,
            {'WWW-Authenticate': 'Basic realm="Verum Signal Admin"'}
        )
    return None

# ---------------------------------------------------------------------------
# Station registry — known broadcast live stream URLs
# ---------------------------------------------------------------------------

STATION_REGISTRY = {
    'kcci':          {'name': 'KCCI Des Moines',     'url': 'https://www.kcci.com/live'},
    'iowa-pbs':      {'name': 'Iowa PBS',             'url': 'https://www.iowapbs.org/livestream'},
    'cspan-1':       {'name': 'C-SPAN 1',             'url': 'https://www.c-span.org/live/1'},
    'cspan-2':       {'name': 'C-SPAN 2',             'url': 'https://www.c-span.org/live/2'},
    'pbs-newshour':  {'name': 'PBS NewsHour',         'url': 'https://www.pbs.org/newshour/live'},
    'abc-news':      {'name': 'ABC News Live',        'url': 'https://abcnews.go.com/live'},
    'nbc-news':      {'name': 'NBC News Now',         'url': 'https://www.nbcnews.com/live'},
    'cbs-news':      {'name': 'CBS News',             'url': 'https://www.cbsnews.com/live'},
    'youtube':       {'name': 'YouTube (search)',     'url': None},
    'custom':        {'name': 'Custom URL',           'url': None},
}

# ---------------------------------------------------------------------------
# Admin HTML
# ---------------------------------------------------------------------------

_ADMIN_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Verum Signal — Debate Admin</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=DM+Mono:wght@400;500&family=DM+Sans:wght@300;400;500&display=swap" rel="stylesheet">
<style>
:root {
  --bg: #0a0a0f;
  --surface: #111118;
  --surface2: #1a1a24;
  --border: rgba(255,255,255,0.08);
  --text: #e8e8f0;
  --text-2: #9090a8;
  --text-3: #555568;
  --violet: #a855f7;
  --pink: #ec4899;
  --green: #4ade80;
  --yellow: #fbbf24;
  --red: #f87171;
  --mono: 'DM Mono', monospace;
  --sans: 'DM Sans', sans-serif;
}
* { box-sizing: border-box; margin: 0; padding: 0; }
body { background: var(--bg); color: var(--text); font-family: var(--sans); font-size: 14px; min-height: 100vh; }

/* Layout */
.layout { display: grid; grid-template-columns: 220px 1fr; min-height: 100vh; }
.sidebar { background: var(--surface); border-right: 1px solid var(--border); padding: 24px 0; position: sticky; top: 0; height: 100vh; overflow-y: auto; }
.main { padding: 32px; max-width: 960px; }

/* Sidebar */
.sidebar-logo { padding: 0 20px 24px; border-bottom: 1px solid var(--border); margin-bottom: 16px; }
.sidebar-logo span { font-family: var(--mono); font-size: 11px; letter-spacing: 0.15em; color: var(--violet); text-transform: uppercase; }
.sidebar-logo .dot { display: inline-block; width: 6px; height: 6px; border-radius: 50%; background: var(--pink); margin-right: 8px; }
.nav-item { display: flex; align-items: center; gap: 10px; padding: 9px 20px; color: var(--text-2); cursor: pointer; border-left: 2px solid transparent; transition: all .15s; font-size: 13px; }
.nav-item:hover { color: var(--text); background: rgba(255,255,255,0.03); }
.nav-item.active { color: var(--violet); border-left-color: var(--violet); background: rgba(168,85,247,0.06); }
.nav-section { font-family: var(--mono); font-size: 10px; letter-spacing: 0.12em; color: var(--text-3); text-transform: uppercase; padding: 16px 20px 6px; }

/* Page header */
.page-header { margin-bottom: 28px; }
.page-title { font-size: 22px; font-weight: 500; letter-spacing: -0.3px; margin-bottom: 4px; }
.page-sub { color: var(--text-2); font-size: 13px; }

/* Cards */
.card { background: var(--surface); border: 1px solid var(--border); border-radius: 12px; padding: 20px; margin-bottom: 16px; }
.card-title { font-family: var(--mono); font-size: 11px; letter-spacing: 0.12em; color: var(--text-3); text-transform: uppercase; margin-bottom: 16px; }

/* Form */
.form-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }
.form-grid.cols-3 { grid-template-columns: 1fr 1fr 1fr; }
.form-group { display: flex; flex-direction: column; gap: 6px; }
.form-group.full { grid-column: 1 / -1; }
label { font-size: 11px; font-family: var(--mono); color: var(--text-2); letter-spacing: 0.08em; text-transform: uppercase; }
input, select, textarea {
  background: var(--surface2); border: 1px solid var(--border);
  color: var(--text); border-radius: 8px; padding: 9px 12px;
  font-family: var(--sans); font-size: 13px; outline: none; width: 100%;
  transition: border-color .15s;
}
input:focus, select:focus, textarea:focus { border-color: var(--violet); }
textarea { min-height: 70px; resize: vertical; }
select option { background: var(--surface2); }

/* Buttons */
.btn { display: inline-flex; align-items: center; gap: 8px; padding: 9px 16px; border-radius: 8px; border: none; cursor: pointer; font-family: var(--sans); font-size: 13px; font-weight: 500; transition: all .15s; }
.btn-primary { background: var(--violet); color: #fff; }
.btn-primary:hover { background: #9333ea; }
.btn-secondary { background: var(--surface2); color: var(--text); border: 1px solid var(--border); }
.btn-secondary:hover { border-color: var(--violet); color: var(--violet); }
.btn-danger { background: rgba(248,113,113,0.1); color: var(--red); border: 1px solid rgba(248,113,113,0.2); }
.btn-danger:hover { background: rgba(248,113,113,0.2); }
.btn-green { background: rgba(74,222,128,0.1); color: var(--green); border: 1px solid rgba(74,222,128,0.2); }
.btn-green:hover { background: rgba(74,222,128,0.2); }
.btn-sm { padding: 5px 10px; font-size: 12px; }

/* Table */
.table-wrap { overflow-x: auto; }
table { width: 100%; border-collapse: collapse; }
th { font-family: var(--mono); font-size: 10px; letter-spacing: 0.1em; color: var(--text-3); text-transform: uppercase; padding: 8px 12px; text-align: left; border-bottom: 1px solid var(--border); }
td { padding: 10px 12px; border-bottom: 1px solid rgba(255,255,255,0.04); font-size: 13px; vertical-align: middle; }
tr:hover td { background: rgba(255,255,255,0.02); }
.badge { display: inline-flex; align-items: center; gap: 5px; padding: 3px 8px; border-radius: 20px; font-size: 11px; font-family: var(--mono); }
.badge-live { background: rgba(74,222,128,0.12); color: var(--green); }
.badge-upcoming { background: rgba(251,191,36,0.12); color: var(--yellow); }
.badge-complete { background: rgba(168,85,247,0.12); color: var(--violet); }
.dot-live { width: 6px; height: 6px; border-radius: 50%; background: var(--green); animation: pulse 1.5s infinite; }
@keyframes pulse { 0%,100% { opacity:1; } 50% { opacity:.4; } }

/* Tabs */
.tabs { display: flex; gap: 4px; margin-bottom: 24px; border-bottom: 1px solid var(--border); padding-bottom: 0; }
.tab { padding: 8px 16px; cursor: pointer; color: var(--text-2); font-size: 13px; border-bottom: 2px solid transparent; margin-bottom: -1px; transition: all .15s; }
.tab.active { color: var(--violet); border-bottom-color: var(--violet); }
.tab-pane { display: none; }
.tab-pane.active { display: block; }

/* Speaker chips */
.speaker-list { display: flex; flex-direction: column; gap: 8px; margin-top: 8px; }
.speaker-row { display: flex; gap: 8px; align-items: center; }
.speaker-row input { flex: 1; }
.speaker-row .speaker-id { width: 80px; flex: none; }

/* Alert */
.alert { padding: 10px 14px; border-radius: 8px; font-size: 13px; margin-bottom: 16px; }
.alert-success { background: rgba(74,222,128,0.1); border: 1px solid rgba(74,222,128,0.2); color: var(--green); }
.alert-error { background: rgba(248,113,113,0.1); border: 1px solid rgba(248,113,113,0.2); color: var(--red); }

/* Stream status */
.stream-status { display: flex; align-items: center; gap: 10px; padding: 12px 16px; background: var(--surface2); border-radius: 8px; border: 1px solid var(--border); margin-bottom: 12px; }
.stream-info { flex: 1; }
.stream-name { font-size: 13px; font-weight: 500; }
.stream-meta { font-size: 11px; color: var(--text-2); font-family: var(--mono); margin-top: 2px; }

/* Hidden */
.hidden { display: none !important; }
</style>
</head>
<body>
<div class="layout">

<!-- Sidebar -->
<aside class="sidebar">
  <div class="sidebar-logo">
    <div><span class="dot"></span><span>Verum Signal</span></div>
    <div style="font-size:11px;color:var(--text-3);margin-top:4px;">Debate Admin</div>
  </div>
  <div class="nav-section">Events</div>
  <div class="nav-item active" onclick="showTab('events')">📅 All Events</div>
  <div class="nav-item" onclick="showTab('add-event')">➕ Add Event</div>
  <div class="nav-section">People</div>
  <div class="nav-item" onclick="showTab('speakers')">🎙 Speakers</div>
  <div class="nav-section">Operations</div>
  <div class="nav-item" onclick="showTab('stream')">📡 Stream Control</div>
  <div class="nav-item" onclick="window.location='/ops'">⚙️ Ops Dashboard</div>
</aside>

<!-- Main -->
<main class="main">
  <div id="alert-box" class="hidden"></div>

  <!-- EVENTS TAB -->
  <div id="tab-events" class="tab-pane active">
    <div class="page-header">
      <div class="page-title">Events</div>
      <div class="page-sub">All debates, speeches, and hearings tracked by Verum Signal</div>
    </div>
    <div class="card">
      <div class="table-wrap">
        <table id="events-table">
          <thead>
            <tr>
              <th>Date</th>
              <th>Name</th>
              <th>Type</th>
              <th>Status</th>
              <th>Speakers</th>
              <th>Claims</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody id="events-tbody">
            <tr><td colspan="7" style="color:var(--text-3);text-align:center;padding:32px;">Loading...</td></tr>
          </tbody>
        </table>
      </div>
    </div>
  </div>

  <!-- ADD EVENT TAB -->
  <div id="tab-add-event" class="tab-pane">
    <div class="page-header">
      <div class="page-title">Add Event</div>
      <div class="page-sub">Create a new debate, speech, or hearing</div>
    </div>
    <div class="card">
      <div class="card-title">Event Details</div>
      <div class="form-grid">
        <div class="form-group full">
          <label>Event Name</label>
          <input type="text" id="new-name" placeholder="Iowa Senate: Democratic primary debate — Round 2">
        </div>
        <div class="form-group">
          <label>Slug</label>
          <input type="text" id="new-slug" placeholder="iowa-senate-dem-2026-r2">
        </div>
        <div class="form-group">
          <label>Type</label>
          <select id="new-type">
            <option value="debate">Debate</option>
            <option value="speech">Speech</option>
            <option value="hearing">Hearing</option>
            <option value="press_conference">Press Conference</option>
            <option value="town_hall">Town Hall</option>
          </select>
        </div>
        <div class="form-group">
          <label>Date</label>
          <input type="date" id="new-date">
        </div>
        <div class="form-group">
          <label>Start Time</label>
          <input type="time" id="new-time" placeholder="19:00">
        </div>
        <div class="form-group">
          <label>Timezone</label>
          <select id="new-tz">
            <option value="CT">CT (Central)</option>
            <option value="ET">ET (Eastern)</option>
            <option value="MT">MT (Mountain)</option>
            <option value="PT">PT (Pacific)</option>
          </select>
        </div>
        <div class="form-group">
          <label>Venue</label>
          <input type="text" id="new-venue" placeholder="Iowa PBS Studios, Johnston">
        </div>
        <div class="form-group">
          <label>Transcript Source</label>
          <input type="text" id="new-transcript-source" placeholder="Iowa Public Radio">
        </div>
      </div>
    </div>

    <div class="card">
      <div class="card-title">Stream Configuration</div>
      <div class="form-grid">
        <div class="form-group">
          <label>Broadcast Station</label>
          <select id="new-station" onchange="onStationChange()">
            <option value="">— Select station —</option>
            <option value="kcci">KCCI Des Moines</option>
            <option value="iowa-pbs">Iowa PBS</option>
            <option value="cspan-1">C-SPAN 1</option>
            <option value="cspan-2">C-SPAN 2</option>
            <option value="pbs-newshour">PBS NewsHour</option>
            <option value="abc-news">ABC News Live</option>
            <option value="nbc-news">NBC News Now</option>
            <option value="cbs-news">CBS News</option>
            <option value="youtube">YouTube (search)</option>
            <option value="custom">Custom URL</option>
          </select>
        </div>
        <div class="form-group">
          <label>Direct Stream URL <span style="color:var(--text-3)">(optional override)</span></label>
          <input type="text" id="new-stream-url" placeholder="https://...">
        </div>
        <div class="form-group full">
          <label>YouTube Search Query <span style="color:var(--text-3)">(fallback)</span></label>
          <input type="text" id="new-search-query" placeholder="KCCI Iowa Senate debate live 2026">
        </div>
      </div>
    </div>

    <div class="card">
      <div class="card-title">Speakers</div>
      <div id="speaker-rows" class="speaker-list">
        <div class="speaker-row">
          <input type="text" placeholder="Speaker name (e.g. Josh Turek)" class="speaker-name">
          <input type="number" placeholder="DB ID" class="speaker-id">
          <button class="btn btn-secondary btn-sm" onclick="lookupSpeaker(this)">Lookup</button>
          <button class="btn btn-danger btn-sm" onclick="removeSpeakerRow(this)">✕</button>
        </div>
      </div>
      <button class="btn btn-secondary btn-sm" style="margin-top:10px;" onclick="addSpeakerRow()">+ Add speaker</button>
      <div style="margin-top:12px;font-size:12px;color:var(--text-3);">
        Speaker order matters — first speaker listed goes first in the debate.
      </div>
    </div>

    <div style="display:flex;gap:10px;">
      <button class="btn btn-primary" onclick="submitEvent()">Create Event</button>
      <button class="btn btn-secondary" onclick="showTab('events')">Cancel</button>
    </div>
  </div>

  <!-- SPEAKERS TAB -->
  <div id="tab-speakers" class="tab-pane">
    <div class="page-header">
      <div class="page-title">Speakers</div>
      <div class="page-sub">Politicians, officials, and public figures in the system</div>
    </div>
    <div class="card" style="margin-bottom:16px;">
      <div class="card-title">Add New Speaker</div>
      <div class="form-grid cols-3">
        <div class="form-group">
          <label>Full Name</label>
          <input type="text" id="sp-name" placeholder="Josh Turek">
        </div>
        <div class="form-group">
          <label>Slug</label>
          <input type="text" id="sp-slug" placeholder="josh-turek">
        </div>
        <div class="form-group">
          <label>Role / Title</label>
          <input type="text" id="sp-role" placeholder="Iowa State Representative">
        </div>
        <div class="form-group">
          <label>Party</label>
          <select id="sp-party">
            <option value="Democrat">Democrat</option>
            <option value="Republican">Republican</option>
            <option value="Independent">Independent</option>
            <option value="">Other / None</option>
          </select>
        </div>
        <div class="form-group">
          <label>Type</label>
          <select id="sp-type">
            <option value="politician">Politician</option>
            <option value="official">Official</option>
            <option value="pundit">Pundit</option>
            <option value="other">Other</option>
          </select>
        </div>
        <div class="form-group" style="align-self:end;">
          <button class="btn btn-primary" onclick="addSpeaker()">Add Speaker</button>
        </div>
      </div>
    </div>
    <div class="card">
      <input type="text" id="speaker-search" placeholder="Search speakers..." style="margin-bottom:14px;" oninput="filterSpeakers()">
      <div class="table-wrap">
        <table>
          <thead><tr><th>ID</th><th>Name</th><th>Slug</th><th>Role</th><th>Party</th><th>Type</th></tr></thead>
          <tbody id="speakers-tbody">
            <tr><td colspan="6" style="color:var(--text-3);text-align:center;padding:32px;">Loading...</td></tr>
          </tbody>
        </table>
      </div>
    </div>
  </div>

  <!-- STREAM CONTROL TAB -->
  <div id="tab-stream" class="tab-pane">
    <div class="page-header">
      <div class="page-title">Stream Control</div>
      <div class="page-sub">Manually trigger or stop streams for any event</div>
    </div>
    <div class="card">
      <div class="card-title">Active / Upcoming Events</div>
      <div id="stream-events">
        <div style="color:var(--text-3);padding:16px 0;">Loading...</div>
      </div>
    </div>
    <div class="card">
      <div class="card-title">Manual Stream Launch</div>
      <div class="form-grid">
        <div class="form-group">
          <label>Event</label>
          <select id="stream-event-select">
            <option value="">— Select event —</option>
          </select>
        </div>
        <div class="form-group">
          <label>Stream URL <span style="color:var(--text-3)">(optional override)</span></label>
          <input type="text" id="stream-url-override" placeholder="Leave blank to use event's URL">
        </div>
      </div>
      <div style="margin-top:14px;display:flex;gap:10px;">
        <button class="btn btn-green" onclick="launchStream()">▶ Launch Stream</button>
        <button class="btn btn-danger" onclick="stopStream()">■ Stop Stream</button>
      </div>
    </div>
  </div>

</main>
</div>

<!-- EDIT EVENT MODAL -->
<div id="edit-modal" style="display:none;position:fixed;inset:0;background:rgba(0,0,0,0.7);z-index:1000;align-items:center;justify-content:center;">
  <div style="background:var(--surface);border:1px solid var(--border);border-radius:14px;padding:28px;width:620px;max-width:95vw;max-height:90vh;overflow-y:auto;position:relative;">
    <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:20px;">
      <div style="font-size:16px;font-weight:500;">Edit Event</div>
      <button onclick="closeEditModal()" style="background:none;border:none;color:var(--text-2);cursor:pointer;font-size:20px;line-height:1;">×</button>
    </div>
    <input type="hidden" id="edit-id">
    <div class="form-grid">
      <div class="form-group full">
        <label>Event Name</label>
        <input type="text" id="edit-name">
      </div>
      <div class="form-group">
        <label>Slug</label>
        <input type="text" id="edit-slug">
      </div>
      <div class="form-group">
        <label>Type</label>
        <select id="edit-type">
          <option value="debate">Debate</option>
          <option value="speech">Speech</option>
          <option value="hearing">Hearing</option>
          <option value="press_conference">Press Conference</option>
          <option value="town_hall">Town Hall</option>
        </select>
      </div>
      <div class="form-group">
        <label>Date</label>
        <input type="date" id="edit-date">
      </div>
      <div class="form-group">
        <label>Start Time</label>
        <input type="time" id="edit-time">
      </div>
      <div class="form-group">
        <label>Timezone</label>
        <select id="edit-tz">
          <option value="CT">CT (Central)</option>
          <option value="ET">ET (Eastern)</option>
          <option value="MT">MT (Mountain)</option>
          <option value="PT">PT (Pacific)</option>
        </select>
      </div>
      <div class="form-group">
        <label>Venue</label>
        <input type="text" id="edit-venue">
      </div>
      <div class="form-group">
        <label>Transcript Source</label>
        <input type="text" id="edit-transcript-source">
      </div>
      <div class="form-group full">
        <label>Direct Stream URL</label>
        <input type="text" id="edit-stream-url">
      </div>
      <div class="form-group full">
        <label>YouTube Search Query</label>
        <input type="text" id="edit-search-query">
      </div>
    </div>
    <div style="margin-top:20px;display:flex;gap:10px;justify-content:flex-end;">
      <button class="btn btn-secondary" onclick="closeEditModal()">Cancel</button>
      <button class="btn btn-primary" onclick="submitEdit()">Save Changes</button>
    </div>
  </div>
</div>

<script>
const STATIONS = {
  'kcci':         'https://www.kcci.com/live',
  'iowa-pbs':     'https://www.iowapbs.org/livestream',
  'cspan-1':      'https://www.c-span.org/live/1',
  'cspan-2':      'https://www.c-span.org/live/2',
  'pbs-newshour': 'https://www.pbs.org/newshour/live',
  'abc-news':     'https://abcnews.go.com/live',
  'nbc-news':     'https://www.nbcnews.com/live',
  'cbs-news':     'https://www.cbsnews.com/live',
};

let allSpeakers = [];
let allEvents = [];

// Tab navigation
function showTab(name) {
  document.querySelectorAll('.tab-pane').forEach(p => p.classList.remove('active'));
  document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
  document.getElementById('tab-' + name).classList.add('active');
  event && event.target && event.target.classList.add('active');
  if (name === 'events') loadEvents();
  if (name === 'speakers') loadSpeakers();
  if (name === 'stream') loadStreamEvents();
}

// Alert
function showAlert(msg, type='success') {
  const box = document.getElementById('alert-box');
  box.className = 'alert alert-' + type;
  box.textContent = msg;
  box.classList.remove('hidden');
  setTimeout(() => box.classList.add('hidden'), 4000);
}

// Load events
async function loadEvents() {
  const res = await fetch('/api/admin/events');
  const data = await res.json();
  allEvents = data.events || [];
  populateStreamSelect(allEvents);
  const tbody = document.getElementById('events-tbody');
  if (!allEvents.length) {
    tbody.innerHTML = '<tr><td colspan="7" style="color:var(--text-3);text-align:center;padding:32px;">No events yet</td></tr>';
    return;
  }
  tbody.innerHTML = allEvents.map(e => `
    <tr>
      <td style="font-family:var(--mono);font-size:12px;">${e.event_date || '—'}<br><span style="color:var(--text-3)">${e.start_time_str || ''}</span></td>
      <td>
        <div style="font-weight:500;">${e.event_name}</div>
        <div style="font-size:11px;color:var(--text-3);font-family:var(--mono)">${e.slug}</div>
      </td>
      <td><span style="font-size:11px;color:var(--text-2);">${e.event_type}</span></td>
      <td>${statusBadge(e.status)}</td>
      <td style="font-size:12px;">${(e.speakers||[]).map(s=>`<div>${s.name}</div>`).join('')||'<span style="color:var(--text-3)">None</span>'}</td>
      <td style="font-family:var(--mono);font-size:12px;">${e.claim_count || 0}</td>
      <td>
        <div style="display:flex;gap:6px;flex-wrap:wrap;">
          <button class="btn btn-secondary btn-sm" onclick="editEvent(${e.id})">Edit</button>
          <button class="btn btn-green btn-sm" onclick="quickStream(${e.id})">▶ Stream</button>
          <button class="btn btn-danger btn-sm" onclick="deleteEvent(${e.id}, '${e.event_name.replace(/'/g,"\\'")}')">Delete</button>
        </div>
      </td>
    </tr>
  `).join('');
}

function statusBadge(status) {
  const map = {
    live: '<span class="badge badge-live"><span class="dot-live"></span>Live</span>',
    upcoming: '<span class="badge badge-upcoming">Upcoming</span>',
    complete: '<span class="badge badge-complete">Complete</span>',
  };
  return map[status] || `<span style="color:var(--text-3)">${status}</span>`;
}

// Load speakers
async function loadSpeakers() {
  const res = await fetch('/api/admin/speakers');
  const data = await res.json();
  allSpeakers = data.speakers || [];
  renderSpeakers(allSpeakers);
}

function renderSpeakers(speakers) {
  const tbody = document.getElementById('speakers-tbody');
  if (!speakers.length) {
    tbody.innerHTML = '<tr><td colspan="6" style="color:var(--text-3);text-align:center;padding:32px;">No speakers</td></tr>';
    return;
  }
  tbody.innerHTML = speakers.map(s => `
    <tr>
      <td style="font-family:var(--mono);font-size:12px;color:var(--text-3)">${s.id}</td>
      <td style="font-weight:500;">${s.name}</td>
      <td style="font-family:var(--mono);font-size:11px;color:var(--text-2)">${s.slug||'—'}</td>
      <td style="font-size:12px;color:var(--text-2)">${s.role||'—'}</td>
      <td style="font-size:12px;">${s.party||'—'}</td>
      <td style="font-size:11px;color:var(--text-3)">${s.speaker_type||'—'}</td>
    </tr>
  `).join('');
}

function filterSpeakers() {
  const q = document.getElementById('speaker-search').value.toLowerCase();
  renderSpeakers(allSpeakers.filter(s => 
    s.name.toLowerCase().includes(q) || (s.slug||'').includes(q) || (s.role||'').toLowerCase().includes(q)
  ));
}

// Add speaker
async function addSpeaker() {
  const name = document.getElementById('sp-name').value.trim();
  if (!name) { showAlert('Name required', 'error'); return; }
  const slug = document.getElementById('sp-slug').value.trim() || name.toLowerCase().replace(/\s+/g,'-').replace(/[^a-z0-9-]/g,'');
  const payload = {
    name, slug,
    role: document.getElementById('sp-role').value.trim(),
    party: document.getElementById('sp-party').value,
    speaker_type: document.getElementById('sp-type').value,
  };
  const res = await fetch('/api/admin/speakers', { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(payload) });
  const data = await res.json();
  if (data.error) { showAlert(data.error, 'error'); return; }
  showAlert(`Speaker "${name}" added with ID ${data.id}`);
  ['sp-name','sp-slug','sp-role'].forEach(id => document.getElementById(id).value='');
  loadSpeakers();
}

// Speaker rows in add-event form
function addSpeakerRow() {
  const row = document.createElement('div');
  row.className = 'speaker-row';
  row.innerHTML = `
    <input type="text" placeholder="Speaker name" class="speaker-name">
    <input type="number" placeholder="DB ID" class="speaker-id">
    <button class="btn btn-secondary btn-sm" onclick="lookupSpeaker(this)">Lookup</button>
    <button class="btn btn-danger btn-sm" onclick="removeSpeakerRow(this)">✕</button>
  `;
  document.getElementById('speaker-rows').appendChild(row);
}

function removeSpeakerRow(btn) {
  const rows = document.querySelectorAll('.speaker-row');
  if (rows.length > 1) btn.closest('.speaker-row').remove();
}

async function lookupSpeaker(btn) {
  const row = btn.closest('.speaker-row');
  const name = row.querySelector('.speaker-name').value.trim().toLowerCase();
  if (!name) return;
  // Fetch fresh if allSpeakers not yet loaded
  if (!allSpeakers.length) {
    const res = await fetch('/api/admin/speakers');
    const data = await res.json();
    allSpeakers = data.speakers || [];
  }
  const match = allSpeakers.find(s => s.name.toLowerCase().includes(name));
  if (match) {
    row.querySelector('.speaker-id').value = match.id;
    row.querySelector('.speaker-name').value = match.name;
    showAlert(`Found: ${match.name} (ID: ${match.id})`);
  } else {
    showAlert(`No speaker found matching "${name}"`, 'error');
  }
}

// Station change
function onStationChange() {
  const val = document.getElementById('new-station').value;
  const url = STATIONS[val] || '';
  if (url) document.getElementById('new-stream-url').value = url;
}

// Auto-slug from name
document.addEventListener('DOMContentLoaded', () => {
  document.getElementById('new-name').addEventListener('input', function() {
    const slug = this.value.toLowerCase()
      .replace(/[^a-z0-9\s-]/g,'').replace(/\s+/g,'-').replace(/-+/g,'-').trim();
    document.getElementById('new-slug').value = slug;
  });
  loadEvents();
  loadSpeakers();
});

// Submit new event
async function submitEvent() {
  const name = document.getElementById('new-name').value.trim();
  const slug = document.getElementById('new-slug').value.trim();
  if (!name || !slug) { showAlert('Name and slug required', 'error'); return; }

  // Collect speakers
  const speakers = [];
  document.querySelectorAll('.speaker-row').forEach((row, i) => {
    const sname = row.querySelector('.speaker-name').value.trim();
    const sid = row.querySelector('.speaker-id').value.trim();
    if (sname || sid) speakers.push({ name: sname, id: sid ? parseInt(sid) : null, order: i+1 });
  });

  const payload = {
    event_name: name,
    slug,
    event_type: document.getElementById('new-type').value,
    event_date: document.getElementById('new-date').value,
    start_time: document.getElementById('new-time').value,
    timezone: document.getElementById('new-tz').value,
    venue: document.getElementById('new-venue').value.trim(),
    transcript_source: document.getElementById('new-transcript-source').value.trim(),
    stream_url: document.getElementById('new-stream-url').value.trim(),
    search_query: document.getElementById('new-search-query').value.trim(),
    speakers,
    is_public: true,
  };

  const res = await fetch('/api/admin/events', { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(payload) });
  const data = await res.json();
  if (data.error) { showAlert(data.error, 'error'); return; }
  showAlert(`Event "${name}" created! ID: ${data.id}`);
  showTab('events');
}

// Delete event
async function deleteEvent(id, name) {
  if (!confirm(`Delete "${name}"? This will remove all utterances and claims.`)) return;
  const res = await fetch(`/api/admin/events/${id}`, { method:'DELETE' });
  const data = await res.json();
  if (data.error) { showAlert(data.error, 'error'); return; }
  showAlert(`Event deleted`);
  loadEvents();
}

// Stream control
function loadStreamEvents() {
  loadEvents().then(() => {
    const container = document.getElementById('stream-events');
    const active = allEvents.filter(e => e.status === 'live' || e.status === 'upcoming').slice(0,5);
    if (!active.length) { container.innerHTML = '<div style="color:var(--text-3);padding:16px 0;">No active or upcoming events</div>'; return; }
    container.innerHTML = active.map(e => `
      <div class="stream-status">
        <div class="stream-info">
          <div class="stream-name">${e.event_name}</div>
          <div class="stream-meta">${e.event_date} ${e.start_time_str || ''} · ${statusBadge(e.status)}</div>
        </div>
        <button class="btn btn-green btn-sm" onclick="quickStream(${e.id})">▶ Start</button>
      </div>
    `).join('');
  });
}

function populateStreamSelect(events) {
  const opts = '<option value="">— Select event —</option>' +
    events.map(e => `<option value="${e.id}">${e.event_name} (${e.event_date||'TBD'})</option>`).join('');
  document.getElementById('stream-event-select').innerHTML = opts;
  const replaySel = document.getElementById('replay-event-select');
  if (replaySel) replaySel.innerHTML = opts;
}

async function quickStream(eventId) {
  const res = await fetch('/api/admin/stream/launch', {
    method: 'POST',
    headers: {'Content-Type':'application/json'},
    body: JSON.stringify({ event_id: eventId })
  });
  const data = await res.json();
  if (data.error) { showAlert(data.error, 'error'); return; }
  showAlert(data.message || 'Stream launched');
}

async function launchStream() {
  const eventId = document.getElementById('stream-event-select').value;
  const urlOverride = document.getElementById('stream-url-override').value.trim();
  if (!eventId) { showAlert('Select an event', 'error'); return; }
  const res = await fetch('/api/admin/stream/launch', {
    method: 'POST',
    headers: {'Content-Type':'application/json'},
    body: JSON.stringify({ event_id: parseInt(eventId), stream_url: urlOverride || null })
  });
  const data = await res.json();
  if (data.error) { showAlert(data.error, 'error'); return; }
  showAlert(data.message || 'Stream launched');
}

async function stopStream() {
  const eventId = document.getElementById('stream-event-select').value;
  if (!eventId) { showAlert('Select an event', 'error'); return; }
  const res = await fetch('/api/admin/stream/stop', {
    method: 'POST',
    headers: {'Content-Type':'application/json'},
    body: JSON.stringify({ event_id: parseInt(eventId) })
  });
  const data = await res.json();
  showAlert(data.message || 'Stream stopped');
}

function editEvent(id) {
  const e = allEvents.find(ev => ev.id === id);
  if (!e) return;
  document.getElementById('edit-id').value = e.id;
  document.getElementById('edit-name').value = e.event_name || '';
  document.getElementById('edit-slug').value = e.slug || '';
  document.getElementById('edit-type').value = e.event_type || 'debate';
  document.getElementById('edit-date').value = e.event_date || '';
  // parse time from start_time_str e.g. "7:00 PM CT"
  const timeMatch = (e.start_time_str || '').match(/(\d+):(\d+)\s*(AM|PM)/i);
  if (timeMatch) {
    let h = parseInt(timeMatch[1]);
    const m = timeMatch[2];
    const ampm = timeMatch[3].toUpperCase();
    if (ampm === 'PM' && h !== 12) h += 12;
    if (ampm === 'AM' && h === 12) h = 0;
    document.getElementById('edit-time').value = String(h).padStart(2,'0') + ':' + m;
  } else {
    document.getElementById('edit-time').value = '';
  }
  const tzMatch = (e.start_time_str || '').match(/(CT|ET|MT|PT)$/);
  document.getElementById('edit-tz').value = tzMatch ? tzMatch[1] : 'CT';
  document.getElementById('edit-venue').value = e.venue || '';
  document.getElementById('edit-transcript-source').value = e.transcript_source || '';
  document.getElementById('edit-stream-url').value = e.stream_url || '';
  document.getElementById('edit-search-query').value = e.search_query || '';
  const modal = document.getElementById('edit-modal');
  modal.style.display = 'flex';
}

function closeEditModal() {
  document.getElementById('edit-modal').style.display = 'none';
}

async function submitEdit() {
  const id = document.getElementById('edit-id').value;
  const payload = {
    event_name:        document.getElementById('edit-name').value.trim(),
    slug:              document.getElementById('edit-slug').value.trim(),
    event_type:        document.getElementById('edit-type').value,
    event_date:        document.getElementById('edit-date').value,
    start_time:        document.getElementById('edit-time').value,
    timezone:          document.getElementById('edit-tz').value,
    venue:             document.getElementById('edit-venue').value.trim(),
    transcript_source: document.getElementById('edit-transcript-source').value.trim(),
    stream_url:        document.getElementById('edit-stream-url').value.trim(),
    search_query:      document.getElementById('edit-search-query').value.trim(),
  };
  const res = await fetch(`/api/admin/events/${id}`, {
    method: 'PATCH',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(payload)
  });
  const data = await res.json();
  if (data.error) { showAlert(data.error, 'error'); return; }
  showAlert('Event updated');
  closeEditModal();
  loadEvents();
}

// Close modal on backdrop click
document.getElementById('edit-modal').addEventListener('click', function(e) {
  if (e.target === this) closeEditModal();
});
</script>
</body>
</html>
"""

# ---------------------------------------------------------------------------
# Register routes
# ---------------------------------------------------------------------------

def register_admin_routes(app, get_db_conn):

    @app.route('/admin', methods=['GET'])
    def admin_dashboard():
        auth_err = _admin_auth()
        if auth_err: return auth_err
        return Response(_ADMIN_HTML, mimetype='text/html')

    @app.route('/api/admin/events', methods=['GET'])
    def admin_get_events():
        auth_err = _admin_auth()
        if auth_err: return auth_err
        conn = get_db_conn()
        try:
            cur = conn.cursor()
            cur.execute("""
                SELECT e.id, e.slug, e.event_type, e.event_name,
                       e.event_date, e.start_time, e.timezone,
                       e.venue, e.transcript_source, e.stream_url,
                       e.search_query, e.is_public,
                       COUNT(DISTINCT c.id) FILTER (WHERE c.verdict IS NOT NULL) AS claim_count
                FROM events e
                LEFT JOIN claims c ON c.event_id = e.id
                GROUP BY e.id
                ORDER BY e.event_date DESC NULLS LAST, e.created_at DESC
            """)
            rows = cur.fetchall()
            cols = [d[0] for d in cur.description]

            # Get speakers per event
            cur.execute("""
                SELECT es.event_id, s.id, s.name, s.slug, es.speaker_order
                FROM event_speakers es
                JOIN speakers s ON s.id = es.speaker_id
                ORDER BY es.event_id, es.speaker_order
            """)
            speaker_rows = cur.fetchall()
            speakers_by_event = {}
            for r in speaker_rows:
                speakers_by_event.setdefault(r[0], []).append({'id': r[1], 'name': r[2], 'slug': r[3]})

            today = date.today()
            events = []
            for row in rows:
                e = dict(zip(cols, row))
                eid = e['id']
                edate = e['event_date']
                stime = e['start_time']

                # Derive status
                if edate is None or edate < today:
                    status = 'complete'
                elif edate == today:
                    if stime:
                        from datetime import timedelta
                        now = datetime.now().time()
                        win_start = (datetime.combine(today, stime) - timedelta(minutes=30)).time()
                        win_end = (datetime.combine(today, stime) + timedelta(hours=3)).time()
                        status = 'live' if win_start <= now <= win_end else 'upcoming'
                    else:
                        status = 'live'
                else:
                    status = 'upcoming'

                events.append({
                    'id': eid,
                    'slug': e['slug'],
                    'event_type': e['event_type'] or 'debate',
                    'event_name': e['event_name'],
                    'event_date': edate.strftime('%Y-%m-%d') if edate else None,
                    'start_time_str': (stime.strftime('%-I:%M %p') + ' ' + (e['timezone'] or 'CT')) if stime else 'TBD',
                    'venue': e['venue'] or '',
                    'transcript_source': e['transcript_source'] or '',
                    'stream_url': e['stream_url'] or '',
                    'search_query': e['search_query'] or '',
                    'is_public': e['is_public'],
                    'claim_count': e['claim_count'] or 0,
                    'status': status,
                    'speakers': speakers_by_event.get(eid, []),
                })
            cur.close()
            return jsonify({'events': events})
        finally:
            conn.close()

    @app.route('/api/admin/events', methods=['POST'])
    def admin_create_event():
        auth_err = _admin_auth()
        if auth_err: return auth_err
        data = request.get_json()
        if not data:
            return jsonify({'error': 'No data'}), 400

        slug = data.get('slug', '').strip()
        name = data.get('event_name', '').strip()
        if not slug or not name:
            return jsonify({'error': 'slug and event_name required'}), 400

        conn = get_db_conn()
        try:
            cur = conn.cursor()

            # Check slug uniqueness
            cur.execute("SELECT id FROM events WHERE slug = %s", (slug,))
            if cur.fetchone():
                return jsonify({'error': f'Slug "{slug}" already exists'}), 400

            # Parse date/time
            event_date = data.get('event_date') or None
            start_time = data.get('start_time') or None

            cur.execute("""
                INSERT INTO events (slug, event_type, event_name, event_date, start_time,
                                    timezone, venue, transcript_source, stream_url,
                                    search_query, is_public, methodology_version)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'v1.7')
                RETURNING id
            """, (
                slug, data.get('event_type','debate'), name,
                event_date, start_time,
                data.get('timezone','CT'),
                data.get('venue',''),
                data.get('transcript_source',''),
                data.get('stream_url','') or None,
                data.get('search_query','') or None,
                data.get('is_public', True),
            ))
            event_id = cur.fetchone()[0]

            # Insert speakers
            speakers = data.get('speakers', [])
            for i, sp in enumerate(speakers):
                sp_id = sp.get('id')
                if not sp_id:
                    # Try to find by name
                    sp_name = sp.get('name', '').strip()
                    if sp_name:
                        cur.execute("SELECT id FROM speakers WHERE LOWER(name) = LOWER(%s)", (sp_name,))
                        row = cur.fetchone()
                        if row:
                            sp_id = row[0]
                if sp_id:
                    cur.execute("""
                        INSERT INTO event_speakers (event_id, speaker_id, speaker_order)
                        VALUES (%s, %s, %s)
                        ON CONFLICT DO NOTHING
                    """, (event_id, sp_id, i+1))

            conn.commit()
            cur.close()
            return jsonify({'id': event_id, 'slug': slug})
        except Exception as ex:
            conn.rollback()
            return jsonify({'error': str(ex)}), 500
        finally:
            conn.close()

    @app.route('/api/admin/events/<int:event_id>', methods=['PATCH'])
    def admin_update_event(event_id):
        auth_err = _admin_auth()
        if auth_err: return auth_err
        data = request.get_json()
        if not data:
            return jsonify({'error': 'No data'}), 400
        conn = get_db_conn()
        try:
            cur = conn.cursor()
            # Check event exists
            cur.execute("SELECT id FROM events WHERE id = %s", (event_id,))
            if not cur.fetchone():
                return jsonify({'error': 'Event not found'}), 404
            # Check slug uniqueness if changed
            new_slug = data.get('slug', '').strip()
            if new_slug:
                cur.execute("SELECT id FROM events WHERE slug = %s AND id != %s", (new_slug, event_id))
                if cur.fetchone():
                    return jsonify({'error': f'Slug "{new_slug}" already in use'}), 400
            cur.execute("""
                UPDATE events SET
                    event_name        = COALESCE(%s, event_name),
                    slug              = COALESCE(NULLIF(%s,''), slug),
                    event_type        = COALESCE(%s, event_type),
                    event_date        = %s,
                    start_time        = %s,
                    timezone          = COALESCE(%s, timezone),
                    venue             = %s,
                    transcript_source = %s,
                    stream_url        = NULLIF(%s, ''),
                    search_query      = NULLIF(%s, ''),
                    updated_at        = NOW()
                WHERE id = %s
            """, (
                data.get('event_name') or None,
                new_slug or None,
                data.get('event_type') or None,
                data.get('event_date') or None,
                data.get('start_time') or None,
                data.get('timezone') or None,
                data.get('venue', ''),
                data.get('transcript_source', ''),
                data.get('stream_url', ''),
                data.get('search_query', ''),
                event_id,
            ))
            conn.commit()
            cur.close()
            return jsonify({'ok': True})
        except Exception as ex:
            conn.rollback()
            return jsonify({'error': str(ex)}), 500
        finally:
            conn.close()

    @app.route('/api/admin/events/<int:event_id>', methods=['DELETE'])
    def admin_delete_event(event_id):
        auth_err = _admin_auth()
        if auth_err: return auth_err
        conn = get_db_conn()
        try:
            cur = conn.cursor()
            cur.execute("DELETE FROM claims WHERE event_id = %s", (event_id,))
            cur.execute("DELETE FROM speaker_utterances WHERE event_id = %s", (event_id,))
            cur.execute("DELETE FROM event_speakers WHERE event_id = %s", (event_id,))
            cur.execute("DELETE FROM events WHERE id = %s", (event_id,))
            conn.commit()
            cur.close()
            return jsonify({'ok': True})
        except Exception as ex:
            conn.rollback()
            return jsonify({'error': str(ex)}), 500
        finally:
            conn.close()

    @app.route('/api/admin/speakers', methods=['GET'])
    def admin_get_speakers():
        auth_err = _admin_auth()
        if auth_err: return auth_err
        conn = get_db_conn()
        try:
            cur = conn.cursor()
            cur.execute("""
                SELECT id, name, slug, role, party, speaker_type
                FROM speakers ORDER BY name
            """)
            rows = cur.fetchall()
            cur.close()
            return jsonify({'speakers': [
                {'id': r[0], 'name': r[1], 'slug': r[2],
                 'role': r[3], 'party': r[4], 'speaker_type': r[5]}
                for r in rows
            ]})
        finally:
            conn.close()

    @app.route('/api/admin/speakers', methods=['POST'])
    def admin_create_speaker():
        auth_err = _admin_auth()
        if auth_err: return auth_err
        data = request.get_json()
        name = data.get('name','').strip()
        if not name:
            return jsonify({'error': 'name required'}), 400
        slug = data.get('slug','').strip() or name.lower().replace(' ','-').replace("'",'')
        conn = get_db_conn()
        try:
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO speakers (name, slug, normalized_name, role, party, speaker_type)
                VALUES (%s,%s,%s,%s,%s,%s)
                ON CONFLICT (slug) DO UPDATE SET name=EXCLUDED.name
                RETURNING id
            """, (
                name, slug, name.lower(),
                data.get('role',''), data.get('party',''),
                data.get('speaker_type','politician')
            ))
            sp_id = cur.fetchone()[0]
            conn.commit()
            cur.close()
            return jsonify({'id': sp_id})
        except Exception as ex:
            conn.rollback()
            return jsonify({'error': str(ex)}), 500
        finally:
            conn.close()

    @app.route('/api/admin/stream/launch', methods=['POST'])
    def admin_launch_stream():
        auth_err = _admin_auth()
        if auth_err: return auth_err
        data = request.get_json()
        event_id = data.get('event_id')
        if not event_id:
            return jsonify({'error': 'event_id required'}), 400

        # Get event info
        conn = get_db_conn()
        try:
            cur = conn.cursor()
            cur.execute("""
                SELECT e.slug, e.stream_url, e.search_query,
                       string_agg(es.speaker_id::text, ',' ORDER BY es.speaker_order) as speaker_order
                FROM events e
                LEFT JOIN event_speakers es ON es.event_id = e.id
                WHERE e.id = %s
                GROUP BY e.id
            """, (event_id,))
            row = cur.fetchone()
            cur.close()
        finally:
            conn.close()

        if not row:
            return jsonify({'error': 'Event not found'}), 404

        slug, stream_url, search_query, speaker_order = row
        url_override = data.get('stream_url') or stream_url

        if not url_override and search_query:
            # Try to resolve via yt-dlp search
            url_override = f"ytsearch1:{search_query}"

        if not url_override:
            return jsonify({'error': 'No stream URL or search query configured for this event'}), 400

        # Launch stream as background process
        import subprocess
        cmd = [
            'python3', '-u', 'debate_stream.py',
            '--mode', 'live',
            '--url', url_override,
            '--event-slug', slug,
        ]
        if speaker_order:
            cmd += ['--speaker-order', speaker_order]

        try:
            proc = subprocess.Popen(
                cmd,
                cwd=os.path.expanduser('~/projects/veris'),
                stdout=open(f'/tmp/stream_{event_id}.log', 'w'),
                stderr=subprocess.STDOUT,
                start_new_session=True
            )
            return jsonify({'message': f'Stream launched (PID {proc.pid})', 'pid': proc.pid})
        except Exception as ex:
            return jsonify({'error': str(ex)}), 500

    @app.route('/api/admin/stream/replay', methods=['POST'])
    def admin_replay_stream():
        auth_err = _admin_auth()
        if auth_err: return auth_err
        data = request.get_json()
        event_id = data.get('event_id')
        url = data.get('url', '').strip()
        if not event_id:
            return jsonify({'error': 'event_id required'}), 400
        if not url:
            return jsonify({'error': 'YouTube URL required'}), 400
        # Get event slug and speaker order
        conn = get_db_conn()
        try:
            cur = conn.cursor()
            cur.execute("""
                SELECT e.slug,
                       string_agg(es.speaker_id::text, ',' ORDER BY es.speaker_order) as speaker_order,
                       string_agg(s.name || ':' || es.speaker_id::text, ',' ORDER BY es.speaker_order) as speaker_map
                FROM events e
                LEFT JOIN event_speakers es ON es.event_id = e.id
                LEFT JOIN speakers s ON s.id = es.speaker_id
                WHERE e.id = %s
                GROUP BY e.id
            """, (event_id,))
            row = cur.fetchone()
            cur.close()
        finally:
            conn.close()
        if not row:
            return jsonify({'error': 'Event not found'}), 404
        slug, speaker_order, speaker_map = row
        import subprocess
        log_path = f'/tmp/replay_{event_id}.log'
        cmd = [
            'python3', '-u', 'debate_stream.py',
            '--mode', 'async',
            '--url', url,
            '--event-slug', slug,
        ]
        if speaker_map:
            cmd += ['--speakers', speaker_map.upper()]
        if speaker_order:
            cmd += ['--speaker-order', speaker_order]
        try:
            proc = subprocess.Popen(
                cmd,
                cwd=os.path.expanduser('~/projects/veris'),
                stdout=open(log_path, 'w'),
                stderr=subprocess.STDOUT,
                start_new_session=True,
                env={**os.environ,
                     'PATH': f"{os.path.expanduser('~/projects/veris/venv/bin')}:{os.environ.get('PATH','')}"}
            )
            return jsonify({'message': f'Replay started (PID {proc.pid}). Check /tmp/replay_{event_id}.log for progress.'})
        except Exception as ex:
            return jsonify({'error': str(ex)}), 500

    @app.route('/api/admin/stream/stop', methods=['POST'])
    def admin_stop_stream():
        auth_err = _admin_auth()
        if auth_err: return auth_err
        data = request.get_json()
        event_id = data.get('event_id')
        # Kill any debate_stream processes for this event
        import subprocess
        result = subprocess.run(
            ['pkill', '-f', f'debate_stream.py.*--event-slug'],
            capture_output=True
        )
        return jsonify({'message': 'Stream stop signal sent'})
