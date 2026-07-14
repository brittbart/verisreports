"""
billing_routes.py
Verum Signal — Stripe billing blueprint (Session 6 Phase 5 follow-on,
built ahead of the LLC per STRIPE_READINESS_CHECKLIST.docx)

Routes:
    POST /billing/checkout   — create a Stripe Checkout Session, redirect
    POST /billing/portal     — create a Stripe Customer Portal session, redirect
    POST /webhooks/stripe    — receive + verify Stripe webhook events

Helper (used internally, exposed for testing):
    handle_stripe_event(get_db, event) — the actual event-processing
    logic, separated from the route handler so it can be unit-tested
    with hand-built event payloads, without a live webhook request or
    a real Stripe account.

NOT LIVE. Requires, none of which exist yet:
    STRIPE_SECRET_KEY, STRIPE_WEBHOOK_SECRET  (Railway env vars)
    STRIPE_PRICE_CONSUMER_PRO, STRIPE_PRICE_API_PRO  (Price IDs, created
        in the Stripe Dashboard once the account exists)
Until STRIPE_SECRET_KEY is set, /billing/checkout and /billing/portal
return a clear 503 rather than crashing on a missing key — same
"log and return gracefully" shape as send_magic_link() before
RESEND_API_KEY was set.

Only the 'pro' tier is self-serve here, for both products — see
STRIPE_READINESS_CHECKLIST.docx Part III. 'scale'/enterprise deals
are manual (a subscriptions row written directly), not exposed through
Checkout — this file has no code path that can create a 'scale' row.

Status mapping (Stripe subscription.status -> local subscriptions.status)
matches STRIPE_READINESS_CHECKLIST.docx Part IV exactly, including the
three states with no exact local equivalent (unpaid, incomplete_expired,
paused) — the recommended mappings from that document, not yet
confirmed by Britt as final.
"""
import os
import logging

from flask import Blueprint, request, jsonify, redirect

logger = logging.getLogger(__name__)

billing_bp = Blueprint('billing', __name__)

# One Stripe Price ID per self-serve (product, tier) pair. Both are
# 'pro' — 'scale' is deliberately absent, see module docstring.
STRIPE_PRICE_IDS = {
    'consumer': lambda: os.environ.get('STRIPE_PRICE_CONSUMER_PRO'),
    'api': lambda: os.environ.get('STRIPE_PRICE_API_PRO'),
}

# Stripe subscription.status -> local subscriptions.status.
# Direct matches first, then the three open-decision mappings.
STRIPE_STATUS_MAP = {
    'active': 'active',
    'trialing': 'trialing',
    'past_due': 'past_due',
    'canceled': 'canceled',
    'incomplete': 'incomplete',
    'unpaid': 'past_due',              # OPEN DECISION — recommended mapping
    'incomplete_expired': 'canceled',  # OPEN DECISION — recommended mapping
    'paused': 'past_due',              # OPEN DECISION — recommended mapping
}


def _app_base_url():
    return os.environ.get('APP_BASE_URL', 'https://verumsignal.com')


def _stripe_configured():
    return bool(os.environ.get('STRIPE_SECRET_KEY'))


def handle_stripe_event(get_db, event):
    """
    Processes one verified Stripe event, updating the local DB. Kept
    separate from the webhook route so it can be tested directly with
    hand-built event dicts (see session6_stripe_webhook_test.py) —
    no live webhook request or real Stripe account required to test
    this function itself.

    Returns True if the event type was recognized and handled (even if
    it turned out to be a no-op), False if the event type is unhandled
    (not an error — just outside the 4-event minimum set).
    """
    event_type = event['type']
    obj = event['data']['object']

    if event_type == 'checkout.session.completed':
        metadata = obj.get('metadata') or {}
        user_id = metadata.get('user_id')
        product = metadata.get('product')
        if not user_id or product not in ('consumer', 'api'):
            logger.error("[billing] checkout.session.completed missing/invalid metadata: %s", metadata)
            return True
        user_id = int(user_id)
        stripe_subscription_id = obj.get('subscription')
        stripe_customer_id = obj.get('customer')

        conn = get_db()
        cur = conn.cursor()
        try:
            if stripe_customer_id:
                cur.execute(
                    "UPDATE users SET stripe_customer_id = %s WHERE id = %s",
                    (stripe_customer_id, user_id)
                )
            cur.execute("""
                INSERT INTO subscriptions
                    (user_id, product, tier, status, stripe_subscription_id,
                     quota_used_this_month, updated_at)
                VALUES (%s, %s, 'pro', 'active', %s, 0, NOW())
                ON CONFLICT (user_id, product) DO UPDATE
                    SET tier = 'pro', status = 'active',
                        stripe_subscription_id = %s, updated_at = NOW()
            """, (user_id, product, stripe_subscription_id, stripe_subscription_id))
            conn.commit()
        finally:
            cur.close()
            conn.close()
        return True

    elif event_type == 'customer.subscription.updated':
        stripe_subscription_id = obj['id']
        stripe_status = obj['status']
        local_status = STRIPE_STATUS_MAP.get(stripe_status)
        if local_status is None:
            logger.error("[billing] unrecognized Stripe subscription status: %s", stripe_status)
            return True
        conn = get_db()
        cur = conn.cursor()
        try:
            cur.execute("""
                UPDATE subscriptions SET status = %s, updated_at = NOW()
                WHERE stripe_subscription_id = %s
            """, (local_status, stripe_subscription_id))
            conn.commit()
        finally:
            cur.close()
            conn.close()
        return True

    elif event_type == 'customer.subscription.deleted':
        stripe_subscription_id = obj['id']
        conn = get_db()
        cur = conn.cursor()
        try:
            cur.execute("""
                UPDATE subscriptions SET status = 'canceled', updated_at = NOW()
                WHERE stripe_subscription_id = %s
            """, (stripe_subscription_id,))
            conn.commit()
        finally:
            cur.close()
            conn.close()
        return True

    elif event_type == 'invoice.payment_failed':
        # customer.subscription.updated (Stripe fires both) already
        # catches the resulting status flip to past_due — this branch
        # exists for optional customer-facing messaging later (e.g. an
        # /account banner), not a required DB write today.
        return True

    return False


def register_billing_routes(app, get_db):
    import stripe  # local import: don't require the package at all until this is called

    @app.route('/billing/checkout', methods=['POST'])
    def billing_checkout():
        from auth_routes import get_current_user

        if not _stripe_configured():
            logger.error("[billing] /billing/checkout called but STRIPE_SECRET_KEY is not set")
            return jsonify({'error': 'billing is not available yet'}), 503

        user = get_current_user(get_db)
        if not user:
            return redirect('/pricing.html?reason=login_required')

        product = request.form.get('product', '').strip()
        if product not in STRIPE_PRICE_IDS:
            return jsonify({'error': 'invalid product'}), 400

        price_id = STRIPE_PRICE_IDS[product]()
        if not price_id:
            logger.error("[billing] no Stripe Price ID configured for product=%s", product)
            return jsonify({'error': 'pricing not configured for this product'}), 500

        stripe.api_key = os.environ.get('STRIPE_SECRET_KEY')

        conn = get_db()
        cur = conn.cursor()
        try:
            cur.execute("SELECT stripe_customer_id FROM users WHERE id = %s", (user['id'],))
            row = cur.fetchone()
            stripe_customer_id = row[0] if row else None

            if not stripe_customer_id:
                customer = stripe.Customer.create(
                    email=user['email'],
                    metadata={'user_id': str(user['id'])},
                )
                stripe_customer_id = customer.id
                cur.execute(
                    "UPDATE users SET stripe_customer_id = %s WHERE id = %s",
                    (stripe_customer_id, user['id'])
                )
                conn.commit()
        finally:
            cur.close()
            conn.close()

        checkout_session = stripe.checkout.Session.create(
            customer=stripe_customer_id,
            payment_method_types=['card'],
            line_items=[{'price': price_id, 'quantity': 1}],
            mode='subscription',
            success_url=f"{_app_base_url()}/account?checkout=success",
            cancel_url=f"{_app_base_url()}/pricing.html?checkout=canceled",
            metadata={'user_id': str(user['id']), 'product': product},
        )
        return redirect(checkout_session.url, code=303)

    @app.route('/billing/portal', methods=['POST'])
    def billing_portal():
        from auth_routes import get_current_user

        if not _stripe_configured():
            logger.error("[billing] /billing/portal called but STRIPE_SECRET_KEY is not set")
            return jsonify({'error': 'billing is not available yet'}), 503

        user = get_current_user(get_db)
        if not user:
            return redirect('/pricing.html?reason=login_required')

        conn = get_db()
        cur = conn.cursor()
        try:
            cur.execute("SELECT stripe_customer_id FROM users WHERE id = %s", (user['id'],))
            row = cur.fetchone()
        finally:
            cur.close()
            conn.close()

        if not row or not row[0]:
            # No Stripe customer yet — nothing to manage.
            return redirect('/account')

        stripe.api_key = os.environ.get('STRIPE_SECRET_KEY')
        portal_session = stripe.billing_portal.Session.create(
            customer=row[0],
            return_url=f"{_app_base_url()}/account",
        )
        return redirect(portal_session.url, code=303)

    @app.route('/webhooks/stripe', methods=['POST'])
    def stripe_webhook():
        endpoint_secret = os.environ.get('STRIPE_WEBHOOK_SECRET')
        if not endpoint_secret:
            logger.error("[billing] /webhooks/stripe called but STRIPE_WEBHOOK_SECRET is not set")
            return jsonify({'error': 'webhook not configured'}), 503

        payload = request.get_data()
        sig_header = request.headers.get('Stripe-Signature', '')

        try:
            event = stripe.Webhook.construct_event(payload, sig_header, endpoint_secret)
        except ValueError:
            logger.error("[billing] webhook payload could not be parsed")
            return jsonify({'error': 'invalid payload'}), 400
        except stripe.error.SignatureVerificationError:
            logger.error("[billing] webhook signature verification failed")
            return jsonify({'error': 'invalid signature'}), 400

        try:
            handled = handle_stripe_event(get_db, event)
        except Exception as e:
            logger.error("[billing] error handling Stripe event %s: %s", event.get('type'), e)
            # 500 tells Stripe to retry — better than silently dropping
            # an event we failed to apply.
            return jsonify({'error': 'internal error'}), 500

        return jsonify({'status': 'ok', 'handled': handled}), 200
