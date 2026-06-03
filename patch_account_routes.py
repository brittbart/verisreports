"""
patch_account_routes.py
Verum Signal — Add /account and /auth/error routes to api.py.

/account   — shows email, tier, quota, upgrade CTA. Requires auth.
             Unauthenticated users redirected to /pricing.html.
/auth/error — friendly error page for failed magic link attempts.

Inserts both routes just before the auth blueprint registration lines.

Run: python3 patch_account_routes.py
"""

import sys
import shutil
from datetime import datetime

TARGET = 'api.py'
BACKUP = f'api.py.bak.{datetime.now().strftime("%Y%m%d_%H%M%S")}'

OLD = 'from auth_routes import register_auth_routes\nregister_auth_routes(app, get_db)'

NEW = '''# ── /account ──────────────────────────────────────────────────────────────────
@app.route('/account', methods=['GET'])
def account_page():
    from auth_routes import get_current_user, get_subscription, QUOTA_LIMITS
    from flask import session
    import os

    user = get_current_user(get_db)
    if not user:
        return redirect('/pricing.html?reason=login_required')

    # Consumer subscription
    consumer_sub = get_subscription(get_db, user['id'], 'consumer')
    consumer_tier = consumer_sub['tier'] if consumer_sub else 'free'
    consumer_used = consumer_sub['quota_used_this_month'] if consumer_sub else 0
    consumer_limit = QUOTA_LIMITS['consumer'][consumer_tier]
    consumer_reset = consumer_sub['quota_reset_at'] if consumer_sub else None

    # API subscription
    api_sub = get_subscription(get_db, user['id'], 'api')
    api_tier = api_sub['tier'] if api_sub else None
    api_used = api_sub['quota_used_this_month'] if api_sub else 0
    api_limit = QUOTA_LIMITS['api'][api_tier] if api_tier else None
    api_reset = api_sub['quota_reset_at'] if api_sub else None

    def quota_bar(used, limit, tier):
        if limit == 0:
            pct = 100
        else:
            pct = min(100, round(used / limit * 100))
        cls = 'full' if pct >= 100 else ('warn' if pct >= 80 else '')
        return f\'\'\'
        <div class="quota-label">
          Reports used this month
          <span>{used} / {limit}</span>
        </div>
        <div class="quota-track">
          <div class="quota-fill {cls}" style="width:{pct}%"></div>
        </div>\'\'\'

    def reset_line(reset_at):
        if not reset_at:
            return \'\'
        try:
            from datetime import timezone
            if hasattr(reset_at, 'strftime'):
                return f\'<div class="quota-reset">Resets {reset_at.strftime("%B 1")}</div>\'
        except Exception:
            pass
        return \'\'

    def tier_badge(tier):
        return f\'<span class="tier-badge {tier}">{tier.upper()}</span>\'

    # ── Consumer section ───────────────────────────────────────────────────────
    consumer_html = f\'\'\'
    <div class="section">
      <div class="section-label">Consumer · Reports</div>
      <div class="tier-row">
        <div style="font-size:13px;color:rgba(255,255,255,0.55);font-weight:300;">
          Full claim-by-claim analysis
        </div>
        {tier_badge(consumer_tier)}
      </div>
      {quota_bar(consumer_used, consumer_limit, consumer_tier)}
      {reset_line(consumer_reset)}
      {upgrade_consumer_cta(consumer_tier)}
    </div>\'\'\'

    # ── API section (only if they have an API subscription) ───────────────────
    api_html = \'\'
    if api_sub:
        api_html = f\'\'\'
    <div class="section">
      <div class="section-label">API · Calls</div>
      <div class="tier-row">
        <div style="font-size:13px;color:rgba(255,255,255,0.55);font-weight:300;">
          Programmatic access
        </div>
        {tier_badge(api_tier)}
      </div>
      {quota_bar(api_used, api_limit, api_tier)}
      {reset_line(api_reset)}
      {upgrade_api_cta(api_tier)}
    </div>\'\'\'

    content = f\'\'\'
    <div class="panel">
      <div class="panel-header">
        <div class="eyebrow">Account</div>
        <div class="email-line">{user["email"]}</div>
      </div>
      {consumer_html}
      {api_html}
      <div class="signout-section">
        <span class="signout-meta">verumsignal.com</span>
        <button class="btn-signout" id="signout-btn">Sign out</button>
      </div>
    </div>\'\'\'

    with open(os.path.join(os.path.dirname(__file__), \'templates\', \'account.html\'), \'r\') as f:
        template = f.read()

    html = template.replace(\'{{content}}\', content)
    from flask import Response
    return Response(html, mimetype=\'text/html\')


def upgrade_consumer_cta(tier):
    if tier in (\'pro\', \'scale\'):
        return \'\'
    return \'\'\'
    <div class="upgrade-block">
      <p>Upgrade to Pro for 50 on-demand reports per month, full claim analysis,
         and all sources weighed and cited.</p>
      <a href="/pricing.html" class="btn-upgrade">Upgrade to Pro &rarr;</a>
    </div>\'\'\'


def upgrade_api_cta(tier):
    if tier in (\'pro\', \'scale\'):
        return \'\'
    return \'\'\'
    <div class="upgrade-block" style="margin-top:12px;">
      <p>Upgrade to API Pro for 1,000 calls/month, or Scale for 25,000 calls/month.</p>
      <a href="/api#pricing" class="btn-upgrade">Upgrade API access &rarr;</a>
    </div>\'\'\'


# ── /auth/error ────────────────────────────────────────────────────────────────
@app.route('/auth/error', methods=['GET'])
def auth_error_page():
    import os
    reason = request.args.get('reason', 'unknown')

    messages = {
        'missing_token':     ('Link missing', 'The sign-in link is incomplete. Please request a new one.'),
        'invalid_token':     ('Link not found', 'This sign-in link is invalid or has already been used. Each link works once.'),
        'token_already_used':('Link already used', 'This sign-in link has already been used. Please request a new one.'),
        'token_expired':     ('Link expired', 'This sign-in link expired after 15 minutes. Please request a new one.'),
        'server_error':      ('Something went wrong', 'An unexpected error occurred. Please try again.'),
    }

    title, message = messages.get(reason, ('Sign-in failed', 'Please request a new sign-in link.'))

    content = f\'\'\'
    <div class="panel">
      <div class="error-panel">
        <div class="error-icon">&#10007;</div>
        <h2>{title}</h2>
        <p>{message}</p>
        <a href="/" class="btn-ghost">&larr; Back to Verum Signal</a>
      </div>
    </div>\'\'\'

    with open(os.path.join(os.path.dirname(__file__), \'templates\', \'account.html\'), \'r\') as f:
        template = f.read()

    html = template.replace(\'{{content}}\', content)
    from flask import Response
    return Response(html, mimetype=\'text/html\')


from auth_routes import register_auth_routes
register_auth_routes(app, get_db)'''


def run():
    with open(TARGET, 'r') as f:
        content = f.read()

    count = content.count(OLD)
    if count == 0:
        print('[FAIL] Anchor not found.')
        sys.exit(1)
    if count > 1:
        print(f'[FAIL] Anchor found {count} times — ambiguous.')
        sys.exit(1)

    print('[OK]   Anchor found exactly once.')

    shutil.copy2(TARGET, BACKUP)
    print(f'[OK]   Backup written to {BACKUP}')

    new_content = content.replace(OLD, NEW)

    if new_content == content:
        print('[FAIL] Replacement produced no change.')
        sys.exit(1)

    with open(TARGET, 'w') as f:
        f.write(new_content)

    print('[OK]   Patch applied.')

    with open(TARGET, 'r') as f:
        verify = f.read()

    if 'def account_page' not in verify:
        print('[FAIL] account_page not found after patch.')
        sys.exit(1)

    if 'def auth_error_page' not in verify:
        print('[FAIL] auth_error_page not found after patch.')
        sys.exit(1)

    print('[OK]   Patch verified.')
    print('\nNext: python3 -c "from api import app; print(\'import OK\')"')


if __name__ == '__main__':
    run()
