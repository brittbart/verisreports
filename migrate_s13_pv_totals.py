#!/usr/bin/env python3
"""S13 privacy: daily page-view totals kept after the 90-day window (no visitor data in them).
Dry run by default; --apply creates the tables. Safe to re-run (IF NOT EXISTS)."""
import os, sys, psycopg2
from dotenv import load_dotenv
load_dotenv(os.path.expanduser('~/projects/veris/.env'))
DDL = """
CREATE TABLE IF NOT EXISTS page_view_daily (
    day date NOT NULL, path text NOT NULL, is_mobile_app boolean NOT NULL DEFAULT false,
    views integer NOT NULL, sessions integer NOT NULL,
    PRIMARY KEY (day, path, is_mobile_app));
CREATE TABLE IF NOT EXISTS page_view_referrers_daily (
    day date NOT NULL, referrer_host text NOT NULL, views integer NOT NULL,
    PRIMARY KEY (day, referrer_host));
"""
c = psycopg2.connect(dbname=os.getenv('DB_NAME', 'railway'), user=os.getenv('DB_USER', 'postgres'),
                     password=os.environ['DB_PASSWORD'], host=os.getenv('DB_HOST', 'shinkansen.proxy.rlwy.net'),
                     port=os.getenv('DB_PORT', '35370'))
cur = c.cursor()
print(DDL)
if '--apply' not in sys.argv:
    print('DRY RUN -- nothing written'); sys.exit(0)
cur.execute(DDL)
c.commit()
cur.execute("SELECT to_regclass('public.page_view_daily'), to_regclass('public.page_view_referrers_daily')")
print('created:', cur.fetchone())
print('rollback: DROP TABLE page_view_daily, page_view_referrers_daily;')
