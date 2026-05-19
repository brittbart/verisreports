#!/usr/bin/env python3
"""
scripts/generate_api_key.py

Issue a new Verum Signal API key. Run manually during closed beta.
The full key is printed ONCE to stdout — copy it immediately.
Only the SHA-256 hash is stored; the raw key is never logged or persisted.

Usage:
    cd ~/projects/veris && source venv/bin/activate
    python3 scripts/generate_api_key.py --email user@example.com --tier starter
    python3 scripts/generate_api_key.py --email user@example.com --tier pro --name "Beta Partner"
    python3 scripts/generate_api_key.py --email user@example.com --tier enterprise \
        --monthly-quota 1000000 --rate-limit 1200 --name "Hedge Fund X"

Tier defaults:
    starter:    5,000 calls/mo,  60/min
    pro:       50,000 calls/mo, 180/min
    enterprise: 500,000 calls/mo, 600/min  (override with --monthly-quota / --rate-limit)
"""

import argparse
import hashlib
import secrets
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from api import get_db

TIER_PRESETS = {
    'starter':    {'monthly_quota': 5_000,   'rate_limit_per_minute': 60},
    'pro':        {'monthly_quota': 50_000,  'rate_limit_per_minute': 180},
    'enterprise': {'monthly_quota': 500_000, 'rate_limit_per_minute': 600},
}


def parse_args():
    p = argparse.ArgumentParser(description='Issue a Verum Signal API key')
    p.add_argument('--email',          required=True,  help='Customer email address')
    p.add_argument('--tier',           required=True,  choices=TIER_PRESETS.keys())
    p.add_argument('--name',           default=None,   help='Label for this key (optional)')
    p.add_argument('--monthly-quota',  type=int,       default=None,
                   help='Override monthly call quota (enterprise custom deals)')
    p.add_argument('--rate-limit',     type=int,       default=None,
                   help='Override rate limit per minute (enterprise custom deals)')
    return p.parse_args()


def generate_key():
    """Generate vs_live_ + 32 URL-safe chars."""
    return 'vs_live_' + secrets.token_urlsafe(24)


def hash_key(raw_key):
    return hashlib.sha256(raw_key.encode()).hexdigest()


def main():
    args = parse_args()

    preset = TIER_PRESETS[args.tier]
    monthly_quota       = args.monthly_quota  or preset['monthly_quota']
    rate_limit_per_min  = args.rate_limit     or preset['rate_limit_per_minute']

    raw_key    = generate_key()
    key_hash   = hash_key(raw_key)
    key_prefix = 'vs_live_' + raw_key[len('vs_live_'):len('vs_live_') + 8]

    conn = get_db()
    cur  = conn.cursor()
    try:
        cur.execute("""
            INSERT INTO api_keys (
                user_email, key_hash, key_prefix, name,
                tier, monthly_quota, rate_limit_per_minute
            ) VALUES (%s, %s, %s, %s, %s, %s, %s)
            RETURNING id
        """, (args.email, key_hash, key_prefix, args.name,
              args.tier, monthly_quota, rate_limit_per_min))
        key_id = cur.fetchone()[0]
        conn.commit()
    except Exception as e:
        conn.rollback()
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        cur.close()
        conn.close()

    print()
    print("=" * 60)
    print("  NEW API KEY ISSUED")
    print("=" * 60)
    print(f"  Email  : {args.email}")
    print(f"  Tier   : {args.tier}")
    print(f"  Quota  : {monthly_quota:,} calls/month")
    print(f"  Rate   : {rate_limit_per_min}/min")
    print(f"  Name   : {args.name or '(none)'}")
    print(f"  Key ID : {key_id}")
    print(f"  Prefix : {key_prefix}")
    print()
    print(f"  API KEY (copy now — shown once):")
    print(f"  {raw_key}")
    print("=" * 60)
    print()


if __name__ == '__main__':
    main()
