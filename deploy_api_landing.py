#!/usr/bin/env python3
"""
deploy_api_landing.py - Run from ~/projects/veris with venv active.
Copy api.html to ~/projects/veris/ first, then run this script.
"""
import os, sys, subprocess

ROOT = os.path.dirname(os.path.abspath(__file__))

def read(p):
    with open(os.path.join(ROOT, p), encoding='utf-8') as f: return f.read()

def write(p, c):
    full = os.path.join(ROOT, p)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, 'w', encoding='utf-8') as f: f.write(c)
    print(f"  OK {p}")

def patch(c, old, new, label):
    n = c.count(old)
    if n != 1: print(f"  FAIL anchor ({n}x): {label}"); sys.exit(1)
    print(f"  OK patch: {label}"); return c.replace(old, new)

def add_api_nav(c, fname):
    link = '      <a href="/api">API</a>\n'
    if link in c: print(f"    skip nav {fname}"); return c
    for a in ['      <a href="#" class="nav-cta">', '      <a href="/#" class="nav-cta">']:
        if a in c: return c.replace(a, link + a, 1)
    print(f"    WARN nav anchor not found: {fname}"); return c

# ---- Task 1+2: api.html -> static/api/index.html ----
print("\n[1] Deploy static/api/index.html")
src = os.path.join(ROOT, 'api.html')
if not os.path.exists(src):
    print("  FAIL: copy api.html to ~/projects/veris/ first"); sys.exit(1)
with open(src, encoding='utf-8') as f: html = f.read()
if 'Sample Wire' not in html: print("  FAIL: wrong api.html version"); sys.exit(1)

print("[2] Replace simulated form submit")
OLD = (
    "    // Simulate submit\n"
    "    form.classList.add('is-submitted');\n"
    "    document.getElementById('form-success').classList.add('is-shown');\n"
    "    form.scrollIntoView({ behavior: 'smooth', block: 'center' });"
)
NEW = (
    "    // Real submit\n"
    "    var payload = {\n"
    "      name: document.getElementById('f-name').value.trim(),\n"
    "      email: document.getElementById('f-email').value.trim(),\n"
    "      organization: document.getElementById('f-org').value.trim(),\n"
    "      use_case: document.getElementById('f-use').value.trim(),\n"
    "      estimated_volume: (document.getElementById('f-volume') || {}).value || null\n"
    "    };\n"
    "    var submitBtn = form.querySelector('.form-submit');\n"
    "    var originalText = submitBtn.textContent;\n"
    "    submitBtn.textContent = 'Submitting\u2026';\n"
    "    submitBtn.disabled = true;\n"
    "    fetch('/api/beta-request', {\n"
    "      method: 'POST',\n"
    "      headers: { 'Content-Type': 'application/json' },\n"
    "      body: JSON.stringify(payload)\n"
    "    })\n"
    "    .then(function(response) {\n"
    "      return response.json().then(function(data) {\n"
    "        if (response.ok && data.success) {\n"
    "          form.classList.add('is-submitted');\n"
    "          document.getElementById('form-success').classList.add('is-shown');\n"
    "          form.scrollIntoView({ behavior: 'smooth', block: 'center' });\n"
    "        } else {\n"
    "          submitBtn.textContent = originalText; submitBtn.disabled = false;\n"
    "          alert('Sorry, something went wrong. Please try again or email api@verumsignal.com directly.');\n"
    "        }\n"
    "      });\n"
    "    })\n"
    "    .catch(function(err) {\n"
    "      submitBtn.textContent = originalText; submitBtn.disabled = false;\n"
    "      alert('Sorry, something went wrong. Please try again or email api@verumsignal.com directly.');\n"
    "    });"
)
html = patch(html, OLD, NEW, "form submit handler")
write('static/api/index.html', html)

# ---- Task 2A: migration SQL ----
print("\n[3] Migration SQL")
write('scripts/migration_api_beta_requests.sql', """\
-- api_beta_requests — run before deploying /api landing page
CREATE TABLE IF NOT EXISTS api_beta_requests (
    id BIGSERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    email TEXT NOT NULL,
    organization TEXT NOT NULL,
    use_case TEXT NOT NULL,
    estimated_volume TEXT,
    submitted_at TIMESTAMP NOT NULL DEFAULT NOW(),
    ip TEXT,
    user_agent TEXT,
    contacted_at TIMESTAMP,
    status TEXT NOT NULL DEFAULT 'new'
        CHECK (status IN ('new','contacted','approved','declined','spam'))
);
CREATE INDEX IF NOT EXISTS idx_api_beta_requests_submitted ON api_beta_requests(submitted_at DESC);
CREATE INDEX IF NOT EXISTS idx_api_beta_requests_status ON api_beta_requests(status);
""")

# ---- Task 2B+C: Flask routes ----
print("\n[4] Flask routes + notification stub")
api_py = read('api.py')
ANCHOR = "@app.route('/terms', methods=['GET'])"
if api_py.count(ANCHOR) != 1: print(f"  FAIL terms anchor"); sys.exit(1)

ROUTES = (
    "\n\ndef send_beta_request_notification(request_id, name, email, org, use_case, volume):\n"
    '    """Log new beta request. Replace with real SMTP when api@verumsignal.com is configured."""\n'
    "    import datetime\n"
    "    line = (\n"
    "        f\"[{datetime.datetime.utcnow().isoformat()}] \"\n"
    "        f\"NEW BETA REQUEST #{request_id}: {name} <{email}> from {org}\\n\"\n"
    "        f\"  Volume: {volume or 'not specified'}\\n\"\n"
    "        f\"  Use case: {use_case[:200]}\\n\"\n"
    "        '---\\n'\n"
    "    )\n"
    "    try:\n"
    "        with open('/tmp/beta_requests.log', 'a') as f: f.write(line)\n"
    "    except Exception: pass\n"
    "\n\n"
    "@app.route('/api', methods=['GET'])\n"
    "def api_landing():\n"
    "    return send_from_directory(\n"
    "        os.path.join(app.root_path, 'static', 'api'),\n"
    "        'index.html'\n"
    "    )\n"
    "\n\n"
    "@app.route('/api/beta-request', methods=['POST'])\n"
    "def api_beta_request_submit():\n"
    '    """Submit a beta access request from the API landing page form."""\n'
    "    import re as _re\n"
    "    data = request.get_json(silent=True) or request.form\n"
    "    name             = (data.get('name') or '').strip()\n"
    "    email            = (data.get('email') or '').strip()\n"
    "    organization     = (data.get('organization') or '').strip()\n"
    "    use_case         = (data.get('use_case') or '').strip()\n"
    "    estimated_volume = (data.get('estimated_volume') or '').strip() or None\n"
    "    if not name or not email or not organization or not use_case:\n"
    "        return jsonify({'success': False, 'error': 'missing_required_fields'}), 400\n"
    "    if not _re.match(r'^[^\\s@]+@[^\\s@]+\\.[^\\s@]+$', email):\n"
    "        return jsonify({'success': False, 'error': 'invalid_email'}), 400\n"
    "    if len(name) > 200 or len(email) > 200 or len(organization) > 300 or len(use_case) > 5000:\n"
    "        return jsonify({'success': False, 'error': 'field_too_long'}), 400\n"
    "    ip         = request.headers.get('X-Forwarded-For', request.remote_addr or '').split(',')[0].strip()\n"
    "    user_agent = request.headers.get('User-Agent', '')[:500]\n"
    "    conn = get_db(); cur = conn.cursor()\n"
    "    try:\n"
    "        cur.execute(\"\"\"\n"
    "            INSERT INTO api_beta_requests\n"
    "              (name, email, organization, use_case, estimated_volume, ip, user_agent)\n"
    "            VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id\n"
    "        \"\"\", (name, email, organization, use_case, estimated_volume, ip, user_agent))\n"
    "        request_id = cur.fetchone()[0]; conn.commit()\n"
    "    except Exception:\n"
    "        conn.rollback(); return jsonify({'success': False, 'error': 'server_error'}), 500\n"
    "    finally:\n"
    "        cur.close(); conn.close()\n"
    "    try: send_beta_request_notification(request_id, name, email, organization, use_case, estimated_volume)\n"
    "    except Exception: pass\n"
    "    return jsonify({'success': True, 'request_id': request_id}), 200\n"
    "\n\n"
) + ANCHOR

api_py = api_py.replace(ANCHOR, ROUTES)
write('api.py', api_py)

# ---- Task 3: Nav links ----
print("\n[5] Nav links")

def do_static(path, footer_old, footer_new):
    try:
        c = read(path)
        c = add_api_nav(c, path)
        if footer_old in c:
            c = c.replace(footer_old, footer_new)
        write(path, c)
    except FileNotFoundError:
        print(f"  WARN not found: {path}")

do_static('static/index.html',
    '      <a href="/pricing">Pricing</a>\n    </nav>',
    '      <a href="/pricing">Pricing</a>\n      <a href="/api">API</a>\n    </nav>')

do_static('static/leaderboard.html',
    '      <a href="/pricing.html">Pricing</a>\n    </nav>',
    '      <a href="/pricing.html">Pricing</a>\n      <a href="/api">API</a>\n    </nav>')

do_static('static/how-it-works.html',
    '      <a href="https://www.verumsignal.com/pricing.html">Pricing</a>\n    </nav>',
    '      <a href="https://www.verumsignal.com/pricing.html">Pricing</a>\n      <a href="/api">API</a>\n    </nav>')

do_static('static/pricing.html',
    '      <a href="/pricing.html">Pricing</a>\n    </nav>',
    '      <a href="/pricing.html">Pricing</a>\n      <a href="/api">API</a>\n    </nav>')

do_static('static/debates-explainer.html',
    '      <a href="/debates/list">Debates</a>\n    </nav>',
    '      <a href="/debates/list">Debates</a>\n      <a href="/api">API</a>\n    </nav>')

for tmpl in [
    'templates/debates.html',
    'templates/outlet.html',
    'templates/outlet_stub.html',
    'templates/outlet_not_yet_published.html',
    'templates/debate.html',
]:
    try:
        c = read(tmpl)
        c = add_api_nav(c, tmpl)
        write(tmpl, c)
    except FileNotFoundError:
        print(f"  WARN not found: {tmpl}")

# ---- Task 4: Pricing API section ----
print("\n[6] Pricing page API section")
pricing = read('static/pricing.html')
API_SECTION = (
    '\n<!-- API section -->\n'
    '<section class="section" style="padding:64px 0 80px;border-top:0.5px solid var(--border);">\n'
    '  <div class="container" style="max-width:780px;">\n'
    '    <div style="background:var(--surface);border:0.5px solid var(--border);border-radius:16px;padding:40px 48px;display:flex;align-items:center;justify-content:space-between;gap:32px;">\n'
    '      <div>\n'
    '        <div style="font-family:ui-monospace,monospace;font-size:10px;letter-spacing:.14em;text-transform:uppercase;color:var(--violet-light);margin-bottom:12px;">For developers and researchers</div>\n'
    '        <h2 style="font-family:var(--font-display);font-size:28px;font-weight:400;letter-spacing:-.6px;margin-bottom:10px;color:var(--text);">Verum Signal API</h2>\n'
    '        <p style="font-size:14px;color:var(--text-2);line-height:1.6;max-width:460px;">Programmatic access to verdict-labeled claims, outlet credibility scores, and debate data. Currently accepting design partners.</p>\n'
    '      </div>\n'
    '      <a href="/api" style="display:inline-block;white-space:nowrap;padding:12px 28px;background:transparent;border:0.5px solid rgba(168,85,247,0.5);border-radius:8px;font-family:var(--font-sans);font-size:14px;font-weight:500;color:var(--violet-light);text-decoration:none;">Learn more \u2192</a>\n'
    '    </div>\n'
    '  </div>\n'
    '</section>\n'
    '\n<!-- Site footer -->'
)
FOOTER = '<!-- Site footer -->'
n = pricing.count(FOOTER)
if n == 1:
    pricing = pricing.replace(FOOTER, API_SECTION)
    write('static/pricing.html', pricing)
    print("  OK API section added")
else:
    print(f"  WARN footer comment found {n}x — add section manually before <!-- Site footer -->")

# ---- Syntax check ----
print("\nSyntax check...")
r = subprocess.run(['python3', '-m', 'py_compile', 'api.py'], capture_output=True, text=True)
if r.returncode == 0: print("  OK api.py")
else: print(f"  FAIL: {r.stderr}"); sys.exit(1)

print("""
DONE. Next:
  1. PGPASSWORD="$DB_PASSWORD" psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" -v ON_ERROR_STOP=1 -f scripts/migration_api_beta_requests.sql
  2. python3 -m flask --app api run --port 5001 &
     curl -sI http://localhost:5001/api
     curl -s -X POST http://localhost:5001/api/beta-request -H "Content-Type: application/json" -d '{"name":"Test","email":"t@t.com","organization":"X","use_case":"Y"}'
     kill %1
  3. git add -A && git commit -m "API landing page, beta request form, nav updates, pricing section" && git push
""")
