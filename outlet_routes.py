"""
Outlet detail stub route — /outlet/<domain>
"""

import re
from flask import render_template, abort
from api_leaderboard import (
    get_leaderboard_data,
    METHODOLOGY_VERSION,
    INCLUSION_THRESHOLD,
)

DOMAIN_RE = re.compile(r"^[a-z0-9.-]+$")


_PRELIM_VERDICT_COUNT_SQL = """
SELECT COUNT(*) AS verdict_count
FROM articles a
JOIN claims c ON c.article_id = a.id
WHERE c.verdict IS NOT NULL
  AND c.claim_origin = 'outlet_claim'
  AND a.source_name = %s;
"""


def _get_preliminary_count(get_db_conn, domain):
    conn = get_db_conn()
    try:
        cur = conn.cursor()
        cur.execute(_PRELIM_VERDICT_COUNT_SQL, (domain,))
        row = cur.fetchone()
        cur.close()
        if row is None:
            return 0
        if isinstance(row, dict):
            return int(row.get("verdict_count", 0) or 0)
        return int(row[0] or 0)
    finally:
        try:
            conn.close()
        except Exception:
            pass


def register_outlet_routes(app, get_db_conn):

    @app.route("/outlet/<domain>")
    def outlet_detail_stub(domain):
        domain_lc = domain.lower()
        if not DOMAIN_RE.match(domain_lc):
            abort(400, description="Invalid outlet identifier")

        leaderboard = get_leaderboard_data(get_db_conn)
        outlet = next(
            (o for o in leaderboard["outlets"] if o["domain"] == domain_lc),
            None,
        )

        if outlet is None:
            verdict_count = _get_preliminary_count(get_db_conn, domain_lc)
            return render_template(
                "outlet_not_yet_published.html",
                domain=domain_lc,
                verdict_count=verdict_count,
                threshold=INCLUSION_THRESHOLD,
                needed=max(0, INCLUSION_THRESHOLD - verdict_count),
                methodology_version=METHODOLOGY_VERSION,
            )

        return render_template(
            "outlet_stub.html",
            outlet=outlet,
            methodology_version=leaderboard["methodology_version"],
        )
