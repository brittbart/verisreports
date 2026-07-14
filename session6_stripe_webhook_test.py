"""
session6_stripe_webhook_test.py
Verum Signal — Stripe billing (pre-LLC build)

Tests the two things that DON'T require a live Stripe account:
  1. Webhook signature verification — constructs a real, validly-signed
     payload using Stripe's own documented scheme (Stripe-Signature:
     t=<timestamp>,v1=<hmac_sha256_hex>, over f"{timestamp}.{payload}"),
     and confirms stripe.Webhook.construct_event() accepts a correctly
     signed payload and rejects a tampered one.
  2. handle_stripe_event() — fed hand-built but shape-accurate event
     payloads (matching Stripe's real, stable, documented webhook
     object shapes) for all 3 DB-touching event types, against the
     real DB, confirming the subscriptions/users rows land correctly.

What this does NOT test (needs a real Stripe account, still blocked
on the LLC): /billing/checkout and /billing/portal's actual calls to
stripe.Customer.create() / stripe.checkout.Session.create() /
stripe.billing_portal.Session.create() — those need a real API key to
execute at all. Nothing to test there yet beyond "does it import and
compile," which it does.
"""
import os
import sys
import hmac
import hashlib
import json
import time

import psycopg2
import stripe

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from billing_routes import handle_stripe_event, STRIPE_STATUS_MAP  # noqa: E402

FAKE_WEBHOOK_SECRET = 'whsec_test_fake_secret_for_local_signature_testing_only'


def db():
    return psycopg2.connect(
        dbname=os.environ.get('DB_NAME', 'railway'),
        user=os.environ.get('DB_USER', 'postgres'),
        password=os.environ.get('DB_PASSWORD', 'PXLJKUdf14OB8bq4dWgF2P0gCs4FjVP'),
        host=os.environ.get('DB_HOST', 'shinkansen.proxy.rlwy.net'),
        port=os.environ.get('DB_PORT', '35370'),
        connect_timeout=10,
    )


def sign_payload(payload_bytes, secret):
    """Stripe's own documented signing scheme — same one their SDK verifies."""
    timestamp = str(int(time.time()))
    signed_payload = f"{timestamp}.{payload_bytes.decode()}"
    signature = hmac.new(secret.encode(), signed_payload.encode(), hashlib.sha256).hexdigest()
    return f"t={timestamp},v1={signature}"


def setup_test_user(email):
    conn = db()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO users (email, email_verified, updated_at, last_seen_at)
        VALUES (%s, TRUE, NOW(), NOW())
        ON CONFLICT (email) DO UPDATE SET last_seen_at = NOW()
        RETURNING id
    """, (email,))
    user_id = cur.fetchone()[0]
    conn.commit()
    cur.close()
    conn.close()
    return user_id


def test_signature_verification():
    print("=== Test 1: webhook signature verification ===")
    # Real Stripe events always carry a top-level "object": "event" field —
    # required or stripe-python's own post-verification handling errors.
    payload = json.dumps({"id": "evt_test", "object": "event", "type": "ping", "data": {"object": {}}}).encode()

    good_sig = sign_payload(payload, FAKE_WEBHOOK_SECRET)
    try:
        event = stripe.Webhook.construct_event(payload, good_sig, FAKE_WEBHOOK_SECRET)
        print(f"  Correctly signed payload: ACCEPTED (type={event['type']})")
    except stripe.error.SignatureVerificationError as e:
        print(f"  [FAIL] Correctly signed payload was REJECTED: {e}")

    tampered_payload = json.dumps({"id": "evt_test", "object": "event", "type": "ping", "data": {"object": {"tampered": True}}}).encode()
    try:
        stripe.Webhook.construct_event(tampered_payload, good_sig, FAKE_WEBHOOK_SECRET)
        print("  [FAIL] Tampered payload with a stale signature was ACCEPTED — should have been rejected.")
    except stripe.error.SignatureVerificationError:
        print("  Tampered payload with a stale signature: correctly REJECTED.")

    wrong_secret_sig = sign_payload(payload, 'whsec_wrong_secret')
    try:
        stripe.Webhook.construct_event(payload, wrong_secret_sig, FAKE_WEBHOOK_SECRET)
        print("  [FAIL] Payload signed with the WRONG secret was ACCEPTED — should have been rejected.")
    except stripe.error.SignatureVerificationError:
        print("  Payload signed with the wrong secret: correctly REJECTED.")
    print()


def test_checkout_completed():
    print("=== Test 2: checkout.session.completed -> subscriptions row ===")
    user_id = setup_test_user('britt+stripe-webhook-test@verumsignal.com')
    event = {
        "type": "checkout.session.completed",
        "data": {"object": {
            "customer": "cus_test_fake123",
            "subscription": "sub_test_fake456",
            "metadata": {"user_id": str(user_id), "product": "consumer"},
        }},
    }
    handle_stripe_event(db, event)

    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT stripe_customer_id FROM users WHERE id = %s", (user_id,))
    stripe_customer_id = cur.fetchone()[0]
    cur.execute("""
        SELECT tier, status, stripe_subscription_id FROM subscriptions
        WHERE user_id = %s AND product = 'consumer'
    """, (user_id,))
    row = cur.fetchone()
    cur.close()
    conn.close()

    print(f"  users.stripe_customer_id = {stripe_customer_id!r} (expect 'cus_test_fake123')")
    print(f"  subscriptions row = {row} (expect ('pro', 'active', 'sub_test_fake456'))")
    print()
    return user_id


def test_subscription_updated():
    print("=== Test 3: customer.subscription.updated -> status mapping ===")
    for stripe_status, expected_local in STRIPE_STATUS_MAP.items():
        event = {
            "type": "customer.subscription.updated",
            "data": {"object": {"id": "sub_test_fake456", "status": stripe_status}},
        }
        handle_stripe_event(db, event)
        conn = db()
        cur = conn.cursor()
        cur.execute("SELECT status FROM subscriptions WHERE stripe_subscription_id = %s", ("sub_test_fake456",))
        actual = cur.fetchone()[0]
        cur.close()
        conn.close()
        mark = "OK" if actual == expected_local else "MISMATCH"
        print(f"  Stripe status '{stripe_status}' -> local '{actual}' (expected '{expected_local}') [{mark}]")
    print()


def test_subscription_deleted():
    print("=== Test 4: customer.subscription.deleted -> canceled ===")
    event = {
        "type": "customer.subscription.deleted",
        "data": {"object": {"id": "sub_test_fake456"}},
    }
    handle_stripe_event(db, event)
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT status FROM subscriptions WHERE stripe_subscription_id = %s", ("sub_test_fake456",))
    actual = cur.fetchone()[0]
    cur.close()
    conn.close()
    print(f"  status = {actual!r} (expect 'canceled')")
    print()


if __name__ == "__main__":
    test_signature_verification()
    test_checkout_completed()
    test_subscription_updated()
    test_subscription_deleted()
    print("=== Done ===")
