#!/usr/bin/env python3
"""S13 pre-flight: confirm the Anthropic account is accepting requests before a debate.

One request for a single token to the cheapest model (a fraction of a cent). It confirms the
account works; it can't tell how much credit is left, so keep a buffer (the console shows the
balance). Run it with production's key:
    railway run --service verisreports python3 check_ai_credit.py
Exit 0 = OK; 2 = credit balance too low; 1 = any other problem.
"""
import os
import sys

import anthropic

MODEL = os.environ.get('CREDIT_CHECK_MODEL', 'claude-haiku-4-5-20251001')
key = os.environ.get('ANTHROPIC_API_KEY')
if not key:
    print('FAIL: ANTHROPIC_API_KEY is not set -- run with: railway run --service verisreports python3 check_ai_credit.py')
    sys.exit(1)
try:
    anthropic.Anthropic(api_key=key).messages.create(
        model=MODEL, max_tokens=1, messages=[{'role': 'user', 'content': 'ok'}])
except anthropic.BadRequestError as e:
    if 'credit balance' in str(e).lower():
        print('FAIL: Anthropic credit balance too low -- top up before the debate (console.anthropic.com > Settings > Billing)')
        sys.exit(2)
    print(f'FAIL: request rejected: {str(e)[:200]}')
    sys.exit(1)
except Exception as e:
    print(f'FAIL: {type(e).__name__}: {str(e)[:200]}')
    sys.exit(1)
print(f'OK: Anthropic account is accepting requests ({MODEL})')
