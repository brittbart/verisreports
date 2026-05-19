#!/usr/bin/env python3
"""
add_ops_insights.py

Adds /ops/insights route to api.py.
Run from ~/projects/veris with venv active.
"""
import sys

path = 'api.py'
with open(path) as f:
    content = f.read()

ANCHOR = "@app.route('/ops/history', methods=['GET'])"
if content.count(ANCHOR) != 1:
    print(f"ERROR: anchor found {content.count(ANCHOR)} times")
    sys.exit(1)

INSIGHTS_CODE = r'''
# ============================================================
# /ops/insights — analytical dashboard
# ============================================================

_INSIGHTS_CACHE = {'data': None, 'expires_at': 0}


def _compute_snapshot(cur):
    try:
        cur.execute("""
            SELECT
                COUNT(*) FILTER (WHERE verdict IS NOT NULL) AS scored_claims,
                COUNT(*) AS total_claims
            FROM claims
        """)
        r = cur.fetchone()
        scored, total_claims = r

        cur.execute("SELECT COUNT(*) FROM articles")
        total_articles = cur.fetchone()[0]

        cur.execute("SELECT COUNT(DISTINCT LOWER(source_name)) FROM articles")
        outlets_tracked = cur.fetchone()[0]

        cur.execute("""
            SELECT COUNT(DISTINCT LOWER(a.source_name))
            FROM claims c JOIN articles a ON a.id = c.article_id
            WHERE c.claim_origin = 'outlet_claim' AND c.verdict IS NOT NULL
            GROUP BY LOWER(a.source_name)
            HAVING COUNT(*) >= 20
        """)
        outlets_scored = cur.rowcount
        # Re-query for count
        cur.execute("""
            SELECT COUNT(*) FROM (
                SELECT LOWER(a.source_name)
                FROM claims c JOIN articles a ON a.id = c.article_id
                WHERE c.claim_origin = 'outlet_claim' AND c.verdict IS NOT NULL
                GROUP BY LOWER(a.source_name)
                HAVING COUNT(*) >= 20
            ) t
        """)
        outlets_scored = cur.fetchone()[0]

        cur.execute("""
            SELECT COUNT(*) FROM claims
            WHERE verdict IS NOT NULL AND last_checked > NOW() - INTERVAL '7 days'
        """)
        claims_7d = cur.fetchone()[0]

        cur.execute("""
            SELECT COUNT(*) FROM claims
            WHERE verdict IS NOT NULL
              AND last_checked BETWEEN NOW() - INTERVAL '14 days' AND NOW() - INTERVAL '7 days'
        """)
        claims_prev_7d = cur.fetchone()[0]

        cur.execute("""
            SELECT COUNT(*) FROM articles WHERE fetched_at > NOW() - INTERVAL '24 hours'
        """)
        articles_24h = cur.fetchone()[0]

        cur.execute("""
            SELECT COUNT(*) FROM articles
            WHERE fetched_at BETWEEN NOW() - INTERVAL '48 hours' AND NOW() - INTERVAL '24 hours'
        """)
        articles_prev_24h = cur.fetchone()[0]

        avg_verdicts_day = round(claims_7d / 7, 1) if claims_7d else 0

        def delta(now, prev):
            if not prev: return None
            return round((now - prev) / prev * 100, 1)

        return {
            'scored_claims': scored,
            'total_articles': total_articles,
            'outlets_tracked': outlets_tracked,
            'outlets_scored': outlets_scored,
            'claims_7d': claims_7d,
            'claims_7d_delta': delta(claims_7d, claims_prev_7d),
            'articles_24h': articles_24h,
            'articles_24h_delta': delta(articles_24h, articles_prev_24h),
            'avg_verdicts_day': avg_verdicts_day,
        }
    except Exception as e:
        return {'error': str(e)}


def _compute_verdict_distribution(cur):
    try:
        cur.execute("""
            SELECT verdict, COUNT(*) as cnt
            FROM claims WHERE verdict IS NOT NULL
            GROUP BY verdict ORDER BY cnt DESC
        """)
        lifetime = {r[0]: r[1] for r in cur.fetchall()}
        total_lt = sum(lifetime.values())

        cur.execute("""
            SELECT verdict, COUNT(*) as cnt
            FROM claims
            WHERE verdict IS NOT NULL AND last_checked > NOW() - INTERVAL '7 days'
            GROUP BY verdict
        """)
        last7 = {r[0]: r[1] for r in cur.fetchall()}
        total_7d = sum(last7.values())

        cur.execute("""
            SELECT verdict, COUNT(*) as cnt
            FROM claims
            WHERE verdict IS NOT NULL
              AND last_checked BETWEEN NOW() - INTERVAL '14 days' AND NOW() - INTERVAL '7 days'
            GROUP BY verdict
        """)
        prev7 = {r[0]: r[1] for r in cur.fetchall()}
        total_prev7 = sum(prev7.values())

        verdicts = ['supported','plausible','corroborated','overstated',
                    'disputed','not_supported','not_verifiable','opinion']
        rows = []
        for v in verdicts:
            lt_cnt = lifetime.get(v, 0)
            l7_cnt = last7.get(v, 0)
            p7_cnt = prev7.get(v, 0)
            lt_pct = round(lt_cnt / total_lt * 100, 1) if total_lt else 0
            l7_pct = round(l7_cnt / total_7d * 100, 1) if total_7d else 0
            delta = None
            if p7_cnt:
                delta = round((l7_cnt - p7_cnt) / p7_cnt * 100, 1)
            rows.append({
                'verdict': v,
                'lifetime_count': lt_cnt,
                'lifetime_pct': lt_pct,
                'last7_count': l7_cnt,
                'last7_pct': l7_pct,
                'delta': delta,
            })

        # 30-day daily trend
        cur.execute("""
            SELECT
                DATE_TRUNC('day', last_checked) AS day,
                verdict,
                COUNT(*) AS cnt
            FROM claims
            WHERE verdict IS NOT NULL
              AND last_checked > NOW() - INTERVAL '30 days'
            GROUP BY 1, 2 ORDER BY 1
        """)
        trend_raw = cur.fetchall()
        trend = {}
        for row in trend_raw:
            day = row[0].strftime('%Y-%m-%d')
            if day not in trend:
                trend[day] = {}
            trend[day][row[1]] = row[2]

        return {
            'lifetime': lifetime,
            'total_lt': total_lt,
            'rows': rows,
            'trend': trend,
        }
    except Exception as e:
        return {'error': str(e)}


def _compute_outlet_table(cur):
    try:
        cur.execute("""
            SELECT
                LOWER(a.source_name) AS outlet_id,
                a.source_name AS outlet_name,
                COUNT(*) FILTER (WHERE c.verdict='supported') AS supported,
                COUNT(*) FILTER (WHERE c.verdict='plausible') AS plausible,
                COUNT(*) FILTER (WHERE c.verdict='corroborated') AS corroborated,
                COUNT(*) FILTER (WHERE c.verdict='overstated') AS overstated,
                COUNT(*) FILTER (WHERE c.verdict='disputed') AS disputed,
                COUNT(*) FILTER (WHERE c.verdict='not_supported') AS not_supported,
                COUNT(*) FILTER (WHERE c.verdict='not_verifiable') AS not_verifiable,
                COUNT(*) FILTER (WHERE c.verdict IS NOT NULL) AS total,
                MAX(c.last_checked) AS last_verdict
            FROM claims c
            JOIN articles a ON a.id = c.article_id
            WHERE c.claim_origin = 'outlet_claim' AND c.verdict IS NOT NULL
            GROUP BY LOWER(a.source_name), a.source_name
        """)
        rows = cur.fetchall()

        # Velocity: claims last 7d per outlet
        cur.execute("""
            SELECT LOWER(a.source_name), COUNT(*)
            FROM claims c JOIN articles a ON a.id = c.article_id
            WHERE c.claim_origin = 'outlet_claim'
              AND c.verdict IS NOT NULL
              AND c.last_checked > NOW() - INTERVAL '7 days'
            GROUP BY LOWER(a.source_name)
        """)
        velocity = {r[0]: r[1] for r in cur.fetchall()}

        # Score 30d ago: recompute from claims older than 30d
        cur.execute("""
            SELECT
                LOWER(a.source_name) AS outlet_id,
                COUNT(*) FILTER (WHERE c.verdict='supported') AS supported,
                COUNT(*) FILTER (WHERE c.verdict='plausible') AS plausible,
                COUNT(*) FILTER (WHERE c.verdict='corroborated') AS corroborated,
                COUNT(*) FILTER (WHERE c.verdict='overstated') AS overstated,
                COUNT(*) FILTER (WHERE c.verdict='disputed') AS disputed,
                COUNT(*) FILTER (WHERE c.verdict='not_supported') AS not_supported
            FROM claims c JOIN articles a ON a.id = c.article_id
            WHERE c.claim_origin = 'outlet_claim'
              AND c.verdict IS NOT NULL
              AND c.last_checked < NOW() - INTERVAL '30 days'
            GROUP BY LOWER(a.source_name)
        """)
        old_scores = {}
        for r in cur.fetchall():
            oid, sup, pla, cor, ove, dis, ns = r
            sc = sup + pla + cor + ove + dis + ns
            if sc > 0:
                w = sup*1.0 + pla*0.5 + cor*0.5 + ove*-0.5 + dis*-1.0 + ns*-1.5
                old_scores[oid] = round((w/sc + 1.5)/2.5*100, 1)

        def score(sup, pla, cor, ove, dis, ns):
            sc = sup + pla + cor + ove + dis + ns
            if not sc: return None
            w = sup*1.0 + pla*0.5 + cor*0.5 + ove*-0.5 + dis*-1.0 + ns*-1.5
            return round((w/sc + 1.5)/2.5*100, 1)

        def tier(total):
            if total >= 100: return 'published'
            if total >= 50: return 'stabilizing'
            if total >= 20: return 'limited_data'
            return 'tracked'

        outlets = []
        for r in rows:
            (oid, oname, sup, pla, cor, ove, dis, ns, nv, total, last_v) = r
            sc = score(sup, pla, cor, ove, dis, ns)
            scoreable = sup + pla + cor + ove + dis + ns
            old_sc = old_scores.get(oid)
            traj = round(sc - old_sc, 1) if (sc and old_sc) else None
            outlets.append({
                'outlet_id': oid,
                'outlet_name': oname,
                'score': sc,
                'tier': tier(scoreable),
                'total': total,
                'scoreable': scoreable,
                'supported_pct': round(sup/scoreable*100, 1) if scoreable else 0,
                'overstated_pct': round(ove/scoreable*100, 1) if scoreable else 0,
                'disputed_pct': round(dis/scoreable*100, 1) if scoreable else 0,
                'not_supported_pct': round(ns/scoreable*100, 1) if scoreable else 0,
                'velocity': velocity.get(oid, 0),
                'trajectory': traj,
                'last_verdict': last_v.strftime('%Y-%m-%d %H:%M') if last_v else '—',
            })

        outlets.sort(key=lambda x: (x['trajectory'] is None, -(x['trajectory'] or 0)))

        # Tier transition indicators
        tier_thresholds = [20, 50, 100]
        for o in outlets:
            o['approaching_tier'] = None
            for t in tier_thresholds:
                if t - 5 <= o['scoreable'] < t:
                    names = {20: 'limited_data', 50: 'stabilizing', 100: 'published'}
                    o['approaching_tier'] = names[t]

        return {'outlets': outlets}
    except Exception as e:
        return {'error': str(e)}


def _compute_outliers(cur):
    try:
        cur.execute("""
            SELECT
                LOWER(a.source_name) AS outlet_id,
                a.source_name AS outlet_name,
                COUNT(*) FILTER (WHERE c.verdict='overstated') AS overstated,
                COUNT(*) FILTER (WHERE c.verdict='disputed') AS disputed,
                COUNT(*) FILTER (WHERE c.verdict IS NOT NULL
                    AND c.verdict NOT IN ('not_verifiable','opinion')) AS scoreable
            FROM claims c JOIN articles a ON a.id = c.article_id
            WHERE c.claim_origin = 'outlet_claim' AND c.verdict IS NOT NULL
            GROUP BY LOWER(a.source_name), a.source_name
            HAVING COUNT(*) FILTER (WHERE c.verdict IS NOT NULL
                AND c.verdict NOT IN ('not_verifiable','opinion')) >= 5
        """)
        rows = cur.fetchall()

        # Corpus averages
        total_sc = sum(r[4] for r in rows)
        total_ov = sum(r[2] for r in rows)
        total_di = sum(r[3] for r in rows)
        avg_ov = total_ov / total_sc if total_sc else 0
        avg_di = total_di / total_sc if total_sc else 0

        # Velocity comparison
        cur.execute("""
            SELECT LOWER(a.source_name), COUNT(*) FROM claims c
            JOIN articles a ON a.id = c.article_id
            WHERE c.claim_origin='outlet_claim' AND c.verdict IS NOT NULL
              AND c.last_checked > NOW() - INTERVAL '7 days'
            GROUP BY LOWER(a.source_name)
        """)
        vel_now = {r[0]: r[1] for r in cur.fetchall()}
        cur.execute("""
            SELECT LOWER(a.source_name), COUNT(*) FROM claims c
            JOIN articles a ON a.id = c.article_id
            WHERE c.claim_origin='outlet_claim' AND c.verdict IS NOT NULL
              AND c.last_checked BETWEEN NOW() - INTERVAL '14 days' AND NOW() - INTERVAL '7 days'
            GROUP BY LOWER(a.source_name)
        """)
        vel_prev = {r[0]: r[1] for r in cur.fetchall()}

        outliers = []
        for (oid, oname, ov, di, sc) in rows:
            ov_rate = ov/sc if sc else 0
            di_rate = di/sc if sc else 0
            if avg_ov and ov_rate > avg_ov * 2:
                outliers.append({
                    'outlet': oname, 'outlet_id': oid,
                    'type': 'high_overstated',
                    'msg': f'Overstated rate {ov_rate:.1%} vs corpus avg {avg_ov:.1%} (2x+)',
                })
            if avg_di and di_rate > avg_di * 3:
                outliers.append({
                    'outlet': oname, 'outlet_id': oid,
                    'type': 'high_disputed',
                    'msg': f'Disputed rate {di_rate:.1%} vs corpus avg {avg_di:.1%} (3x+)',
                })
            vn = vel_now.get(oid, 0)
            vp = vel_prev.get(oid, 0)
            if vp >= 5 and vn < vp * 0.5:
                outliers.append({
                    'outlet': oname, 'outlet_id': oid,
                    'type': 'volume_drop',
                    'msg': f'Volume dropped: {vn} claims last 7d vs {vp} prior 7d',
                })
            if vp >= 2 and vn > vp * 2:
                outliers.append({
                    'outlet': oname, 'outlet_id': oid,
                    'type': 'volume_spike',
                    'msg': f'Volume spiked: {vn} claims last 7d vs {vp} prior 7d',
                })

        return {
            'outliers': outliers,
            'avg_overstated_pct': round(avg_ov * 100, 1),
            'avg_disputed_pct': round(avg_di * 100, 1),
        }
    except Exception as e:
        return {'error': str(e)}


def _compute_corpus_growth(cur):
    try:
        cur.execute("""
            SELECT DATE_TRUNC('day', fetched_at) AS day, COUNT(*) AS articles
            FROM articles WHERE fetched_at > NOW() - INTERVAL '30 days'
            GROUP BY 1 ORDER BY 1
        """)
        ingestion = [{'day': r[0].strftime('%Y-%m-%d'), 'articles': r[1]} for r in cur.fetchall()]

        cur.execute("""
            SELECT DATE_TRUNC('day', last_checked) AS day, COUNT(*) AS verdicts
            FROM claims WHERE verdict IS NOT NULL AND last_checked > NOW() - INTERVAL '30 days'
            GROUP BY 1 ORDER BY 1
        """)
        throughput = [{'day': r[0].strftime('%Y-%m-%d'), 'verdicts': r[1]} for r in cur.fetchall()]

        # Backlog
        cur.execute("""
            SELECT COUNT(*) FROM articles WHERE processed = FALSE OR processed IS NULL
        """)
        unextracted = cur.fetchone()[0]

        cur.execute("""
            SELECT COUNT(*) FROM claims WHERE verdict IS NULL AND claim_origin = 'outlet_claim'
        """)
        unverified = cur.fetchone()[0]

        # Rates
        cur.execute("""
            SELECT COUNT(*) FROM articles WHERE fetched_at > NOW() - INTERVAL '24 hours'
        """)
        ing_24h = cur.fetchone()[0]
        cur.execute("""
            SELECT COUNT(*) FROM claims WHERE verdict IS NOT NULL AND last_checked > NOW() - INTERVAL '24 hours'
        """)
        ver_24h = cur.fetchone()[0]
        cur.execute("""
            SELECT COUNT(*) FROM claims WHERE last_checked > NOW() - INTERVAL '24 hours'
        """)
        ext_24h = cur.fetchone()[0]

        extraction_rate = round(ext_24h / ing_24h, 2) if ing_24h else None
        verdict_rate = round(ver_24h / ext_24h, 2) if ext_24h else None

        return {
            'ingestion': ingestion,
            'throughput': throughput,
            'unextracted': unextracted,
            'unverified': unverified,
            'extraction_rate': extraction_rate,
            'verdict_rate': verdict_rate,
            'ing_24h': ing_24h,
            'ver_24h': ver_24h,
        }
    except Exception as e:
        return {'error': str(e)}


def _compute_debates(cur):
    try:
        cur.execute("""
            SELECT
                e.id, e.event_name, e.event_date, e.is_public,
                COUNT(c.id) AS total,
                COUNT(c.id) FILTER (WHERE c.verdict IS NOT NULL) AS verified
            FROM events e
            LEFT JOIN claims c ON c.event_id = e.id AND c.claim_origin = 'debate_claim'
            GROUP BY e.id, e.event_name, e.event_date, e.is_public
            ORDER BY e.event_date DESC
        """)
        debates = []
        for r in cur.fetchall():
            eid, name, date, is_public, total, verified = r
            pct = round(verified/total*100) if total else None
            debates.append({
                'id': eid, 'name': name,
                'date': date.strftime('%Y-%m-%d') if date else '—',
                'is_public': is_public,
                'total': total, 'verified': verified,
                'unverified': total - verified,
                'pct': pct,
                'api_visible': is_public and verified > 0,
            })
        return {'debates': debates}
    except Exception as e:
        return {'error': str(e)}


def _compute_cost_and_usage(cur):
    try:
        cur.execute("""
            SELECT
                ROUND((
                    SUM(input_tokens) * 3.00 / 1000000 +
                    SUM(output_tokens) * 15.00 / 1000000 +
                    COALESCE(SUM(cache_creation_input_tokens), 0) * 3.75 / 1000000 +
                    COALESCE(SUM(cache_read_input_tokens), 0) * 0.30 / 1000000
                )::numeric, 2) AS cost_30d
            FROM job_runs
            WHERE started_at > NOW() - INTERVAL '30 days'
        """ if False else "SELECT NULL")
        # job_runs doesn't have token columns — use the existing cost endpoint pattern
        cur.execute("""
            SELECT
                ROUND(SUM(
                    input_tokens * 3.00 / 1000000.0 +
                    output_tokens * 15.00 / 1000000.0
                )::numeric, 2) AS cost
            FROM job_runs
            WHERE started_at > NOW() - INTERVAL '30 days'
              AND input_tokens IS NOT NULL
        """)
        cost_30d = cur.fetchone()[0]
    except Exception:
        cost_30d = None

    try:
        cur.execute("SELECT COUNT(*) FROM api_keys WHERE revoked_at IS NULL")
        active_keys = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM api_usage WHERE created_at > NOW() - INTERVAL '7 days'")
        api_calls_7d = cur.fetchone()[0]
        cur.execute("""
            SELECT endpoint, COUNT(*) as cnt FROM api_usage
            WHERE created_at > NOW() - INTERVAL '7 days'
            GROUP BY endpoint ORDER BY cnt DESC LIMIT 1
        """)
        top_endpoint = cur.fetchone()
        return {
            'cost_30d': float(cost_30d) if cost_30d else None,
            'active_keys': active_keys,
            'api_calls_7d': api_calls_7d,
            'top_endpoint': top_endpoint[0] if top_endpoint else None,
        }
    except Exception as e:
        return {'error': str(e)}


def _compute_insights_context():
    from time import time as _time
    import datetime
    conn = get_db()
    cur = conn.cursor()
    ctx = {'generated_at': datetime.datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}
    try:
        sections = [
            ('snapshot', _compute_snapshot),
            ('verdict', _compute_verdict_distribution),
            ('outlets', _compute_outlet_table),
            ('outliers', _compute_outliers),
            ('corpus', _compute_corpus_growth),
            ('debates', _compute_debates),
            ('cost', _compute_cost_and_usage),
        ]
        for key, fn in sections:
            try:
                t0 = _time()
                ctx[key] = fn(cur)
                elapsed = _time() - t0
                if elapsed > 5:
                    app.logger.warning(f'insights section {key} took {elapsed:.1f}s')
            except Exception as e:
                ctx[key] = {'error': str(e)}
                app.logger.error(f'insights section {key} failed: {e}')
    finally:
        cur.close()
        conn.close()
    return ctx


def build_insights_context():
    from time import time as _time
    if _INSIGHTS_CACHE['data'] and _time() < _INSIGHTS_CACHE['expires_at']:
        return _INSIGHTS_CACHE['data']
    data = _compute_insights_context()
    _INSIGHTS_CACHE['data'] = data
    _INSIGHTS_CACHE['expires_at'] = _time() + 600
    return data


_OPS_INSIGHTS_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Ops Insights — Verum Signal</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js"></script>
<style>
:root{--bg:#0a0a0f;--surface:#12121a;--border:rgba(255,255,255,0.08);--text:rgba(255,255,255,0.9);--text2:rgba(255,255,255,0.55);--text3:rgba(255,255,255,0.3);--violet:#a855f7;--green:#22c55e;--amber:#f59e0b;--red:#dc2626;--red-light:#f87171;--mono:'JetBrains Mono',ui-monospace,monospace}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--text);font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;font-size:14px;line-height:1.5}
a{color:var(--violet);text-decoration:none}
a:hover{text-decoration:underline}
.wrap{max-width:1400px;margin:0 auto;padding:0 24px 80px}
.top-bar{display:flex;align-items:center;justify-content:space-between;padding:20px 0 32px;border-bottom:0.5px solid var(--border);margin-bottom:32px}
.top-bar h1{font-size:20px;font-weight:600;letter-spacing:-0.3px}
.top-bar .links{display:flex;gap:16px;font-size:13px}
.refresh-note{font-size:12px;color:var(--text3);margin-top:4px}
.section{margin-bottom:48px}
.section-head{margin-bottom:20px}
.section-head h2{font-size:16px;font-weight:600;letter-spacing:-0.2px;margin-bottom:4px}
.section-head p{font-size:12px;color:var(--text2)}
.cards{display:flex;flex-wrap:wrap;gap:12px;margin-bottom:24px}
.card{background:var(--surface);border:0.5px solid var(--border);border-radius:10px;padding:16px 20px;min-width:160px;flex:1}
.card-num{font-size:28px;font-weight:700;font-family:var(--mono);letter-spacing:-1px;color:var(--text)}
.card-label{font-size:11px;color:var(--text2);margin-top:4px;text-transform:uppercase;letter-spacing:.06em}
.card-delta{font-size:11px;margin-top:6px}
.delta-up{color:var(--green)}
.delta-dn{color:var(--red-light)}
.pill{display:inline-block;padding:3px 10px;border-radius:100px;font-size:11px;font-family:var(--mono);border:0.5px solid var(--border);color:var(--violet)}
.two-col{display:grid;grid-template-columns:1fr 1fr;gap:20px}
.chart-card{background:var(--surface);border:0.5px solid var(--border);border-radius:10px;padding:20px}
.chart-card h3{font-size:13px;font-weight:600;margin-bottom:16px;color:var(--text2)}
.chart-wrap{position:relative;height:220px}
table{width:100%;border-collapse:collapse;font-size:13px}
th{text-align:left;font-size:11px;font-weight:500;color:var(--text3);text-transform:uppercase;letter-spacing:.06em;padding:8px 10px;border-bottom:0.5px solid var(--border);cursor:pointer;white-space:nowrap}
th:hover{color:var(--text)}
td{padding:8px 10px;border-bottom:0.5px solid rgba(255,255,255,0.04);vertical-align:middle}
tr:last-child td{border-bottom:0}
.tbl-wrap{background:var(--surface);border:0.5px solid var(--border);border-radius:10px;overflow:auto;max-height:520px}
.tbl-wrap table{min-width:900px}
.traj-pos{color:var(--green);font-family:var(--mono)}
.traj-neg{color:var(--red-light);font-family:var(--mono)}
.traj-neu{color:var(--text3);font-family:var(--mono)}
.cell-amber{background:rgba(245,158,11,0.12)}
.cell-red{background:rgba(220,38,38,0.10)}
.tier-badge{display:inline-block;padding:2px 7px;border-radius:4px;font-size:10px;font-family:var(--mono)}
.tier-published{background:rgba(34,197,94,0.15);color:var(--green)}
.tier-stabilizing{background:rgba(168,85,247,0.15);color:var(--violet)}
.tier-limited_data{background:rgba(245,158,11,0.12);color:var(--amber)}
.tier-tracked{background:rgba(255,255,255,0.06);color:var(--text3)}
.approaching{font-size:10px;color:var(--amber);margin-left:6px}
.callouts{display:grid;grid-template-columns:1fr 1fr 1fr;gap:12px;margin-top:16px}
.callout{background:var(--surface);border:0.5px solid var(--border);border-radius:10px;padding:14px 16px}
.callout h4{font-size:11px;font-weight:500;color:var(--text2);text-transform:uppercase;letter-spacing:.06em;margin-bottom:10px}
.callout-item{font-size:12px;padding:4px 0;border-bottom:0.5px solid rgba(255,255,255,0.04)}
.callout-item:last-child{border-bottom:0}
.outlier-list{display:flex;flex-direction:column;gap:8px}
.outlier-item{background:var(--surface);border:0.5px solid var(--border);border-radius:8px;padding:12px 16px;display:flex;gap:12px;align-items:flex-start}
.outlier-badge{font-size:10px;font-family:var(--mono);padding:2px 8px;border-radius:4px;white-space:nowrap;margin-top:2px}
.badge-ov{background:rgba(245,158,11,0.15);color:var(--amber)}
.badge-di{background:rgba(220,38,38,0.15);color:var(--red-light)}
.badge-vol{background:rgba(168,85,247,0.15);color:var(--violet)}
.outlier-msg{font-size:13px;color:var(--text)}
.outlier-outlet{font-size:11px;color:var(--text2);margin-top:2px}
.no-outliers{color:var(--text3);font-size:13px;padding:20px 0}
.err{color:var(--red-light);font-size:12px;padding:12px;background:rgba(220,38,38,0.08);border-radius:6px}
.debate-table td:nth-child(4){text-align:center}
.dot-green{display:inline-block;width:8px;height:8px;border-radius:50%;background:var(--green)}
.dot-gray{display:inline-block;width:8px;height:8px;border-radius:50%;background:var(--text3)}
.rate-card{font-family:var(--mono);font-size:22px}
.rate-ok{color:var(--green)}
.rate-warn{color:var(--amber)}
.rate-bad{color:var(--red-light)}
@media(max-width:900px){.two-col,.callouts{grid-template-columns:1fr}.cards{flex-direction:column}}
</style>
</head>
<body>
<div class="wrap">
<div class="top-bar">
  <div>
    <h1>Ops Insights</h1>
    <div class="refresh-note">Data as of {{ generated_at }} &nbsp;·&nbsp; <a href="/ops/insights?refresh=1">Refresh now</a> &nbsp;·&nbsp; <a href="/ops">← Pipeline</a> &nbsp;·&nbsp; <a href="/ops/history">History</a></div>
  </div>
</div>

<!-- ============ SECTION 1: SNAPSHOT ============ -->
<div class="section">
  <div class="section-head"><h2>Snapshot</h2><p>Key corpus metrics at a glance.</p></div>
  {% set s = snapshot %}
  {% if s.get('error') %}<div class="err">{{ s.error }}</div>{% else %}
  <div class="cards">
    <div class="card">
      <div class="card-num">{{ "{:,}".format(s.scored_claims) }}</div>
      <div class="card-label">Scored claims</div>
    </div>
    <div class="card">
      <div class="card-num">{{ "{:,}".format(s.total_articles) }}</div>
      <div class="card-label">Total articles</div>
    </div>
    <div class="card">
      <div class="card-num">{{ "{:,}".format(s.outlets_tracked) }}</div>
      <div class="card-label">Outlets tracked</div>
    </div>
    <div class="card">
      <div class="card-num">{{ "{:,}".format(s.outlets_scored) }}</div>
      <div class="card-label">Outlets scored (≥20)</div>
    </div>
    <div class="card">
      <div class="card-num">{{ "{:,}".format(s.claims_7d) }}</div>
      <div class="card-label">Claims scored (7d)</div>
      {% if s.claims_7d_delta is not none %}
        <div class="card-delta {{ 'delta-up' if s.claims_7d_delta >= 0 else 'delta-dn' }}">
          {{ '↑' if s.claims_7d_delta >= 0 else '↓' }} {{ s.claims_7d_delta|abs }}% vs prior 7d
        </div>
      {% endif %}
    </div>
    <div class="card">
      <div class="card-num">{{ "{:,}".format(s.articles_24h) }}</div>
      <div class="card-label">Articles ingested (24h)</div>
      {% if s.articles_24h_delta is not none %}
        <div class="card-delta {{ 'delta-up' if s.articles_24h_delta >= 0 else 'delta-dn' }}">
          {{ '↑' if s.articles_24h_delta >= 0 else '↓' }} {{ s.articles_24h_delta|abs }}% vs prior 24h
        </div>
      {% endif %}
    </div>
    <div class="card">
      <div class="card-num">{{ s.avg_verdicts_day }}</div>
      <div class="card-label">Avg verdicts/day (7d)</div>
    </div>
    <div class="card">
      <div class="card-num"><span class="pill">v1.6</span></div>
      <div class="card-label">Methodology version</div>
    </div>
  </div>
  {% endif %}
</div>

<!-- ============ SECTION 2: VERDICT DISTRIBUTION ============ -->
<div class="section">
  <div class="section-head"><h2>Verdict Distribution</h2><p>What the methodology is producing — lifetime and 30-day trend. Scoring robustness changes deployed May 12 should show overstated rising from ~18.8%.</p></div>
  {% set v = verdict %}
  {% if v.get('error') %}<div class="err">{{ v.error }}</div>{% else %}
  <div class="two-col" style="margin-bottom:20px">
    <div class="chart-card">
      <h3>Lifetime verdict mix</h3>
      <div class="chart-wrap"><canvas id="c-donut"></canvas></div>
    </div>
    <div class="chart-card">
      <h3>30-day daily trend</h3>
      <div class="chart-wrap"><canvas id="c-trend"></canvas></div>
    </div>
  </div>
  <div class="tbl-wrap">
    <table>
      <thead><tr>
        <th>Verdict</th>
        <th>Lifetime</th>
        <th>Lifetime %</th>
        <th>Last 7d</th>
        <th>Last 7d %</th>
        <th>7d vs prior 7d</th>
      </tr></thead>
      <tbody>
        {% for r in v.rows %}
        <tr>
          <td>{{ r.verdict }}</td>
          <td>{{ "{:,}".format(r.lifetime_count) }}</td>
          <td>{{ r.lifetime_pct }}%</td>
          <td>{{ "{:,}".format(r.last7_count) }}</td>
          <td>{{ r.last7_pct }}%</td>
          <td>{% if r.delta is not none %}<span class="{{ 'traj-pos' if r.delta >= 0 else 'traj-neg' }}">{{ '+' if r.delta >= 0 else '' }}{{ r.delta }}%</span>{% else %}<span class="traj-neu">—</span>{% endif %}</td>
        </tr>
        {% endfor %}
      </tbody>
    </table>
  </div>
  {% endif %}
</div>

<!-- ============ SECTION 3: OUTLET TABLE ============ -->
<div class="section">
  <div class="section-head"><h2>Outlet Detail</h2><p>Every tracked outlet with trajectory, velocity, and verdict breakdown. Default: most improved first. Click headers to sort.</p></div>
  {% set o = outlets %}
  {% if o.get('error') %}<div class="err">{{ o.error }}</div>{% else %}
  <div class="tbl-wrap">
    <table id="outlet-table">
      <thead><tr>
        <th onclick="sortTable(0)">Outlet</th>
        <th onclick="sortTable(1)">Score</th>
        <th onclick="sortTable(2)">Tier</th>
        <th onclick="sortTable(3)">Claims</th>
        <th onclick="sortTable(4)">Supported%</th>
        <th onclick="sortTable(5)">Overstated%</th>
        <th onclick="sortTable(6)">Disputed%</th>
        <th onclick="sortTable(7)">Not-supp%</th>
        <th onclick="sortTable(8)">Velocity 7d</th>
        <th onclick="sortTable(9)">Trajectory 30d</th>
        <th onclick="sortTable(10)">Last verdict</th>
      </tr></thead>
      <tbody>
        {% for outlet in o.outlets %}
        <tr>
          <td><a href="/outlet/{{ outlet.outlet_id }}">{{ outlet.outlet_name }}</a>{% if outlet.approaching_tier %}<span class="approaching">→ {{ outlet.approaching_tier }}</span>{% endif %}</td>
          <td>{{ outlet.score if outlet.score is not none else '—' }}</td>
          <td><span class="tier-badge tier-{{ outlet.tier }}">{{ outlet.tier }}</span></td>
          <td>{{ outlet.total }}</td>
          <td>{{ outlet.supported_pct }}%</td>
          <td class="{{ 'cell-amber' if outlet.overstated_pct > 30 else '' }}">{{ outlet.overstated_pct }}%</td>
          <td class="{{ 'cell-red' if outlet.disputed_pct > 5 else '' }}">{{ outlet.disputed_pct }}%</td>
          <td>{{ outlet.not_supported_pct }}%</td>
          <td>{{ outlet.velocity }}</td>
          <td>{% if outlet.trajectory is not none %}<span class="{{ 'traj-pos' if outlet.trajectory >= 0 else 'traj-neg' }}">{{ '+' if outlet.trajectory >= 0 else '' }}{{ outlet.trajectory }}</span>{% else %}<span class="traj-neu">—</span>{% endif %}</td>
          <td style="font-size:11px;color:var(--text2)">{{ outlet.last_verdict }}</td>
        </tr>
        {% endfor %}
      </tbody>
    </table>
  </div>
  <div class="callouts">
    <div class="callout">
      <h4>Most improved (30d)</h4>
      {% for outlet in o.outlets | selectattr('trajectory', '!=', none) | sort(attribute='trajectory', reverse=True) | list | first(3) %}
      <div class="callout-item"><a href="/outlet/{{ outlet.outlet_id }}">{{ outlet.outlet_name }}</a> <span class="traj-pos">+{{ outlet.trajectory }}</span></div>
      {% else %}<div class="callout-item" style="color:var(--text3)">No data</div>
      {% endfor %}
    </div>
    <div class="callout">
      <h4>Most declined (30d)</h4>
      {% for outlet in o.outlets | selectattr('trajectory', '!=', none) | sort(attribute='trajectory') | list | first(3) %}
      {% if outlet.trajectory < 0 %}
      <div class="callout-item"><a href="/outlet/{{ outlet.outlet_id }}">{{ outlet.outlet_name }}</a> <span class="traj-neg">{{ outlet.trajectory }}</span></div>
      {% endif %}
      {% else %}<div class="callout-item" style="color:var(--text3)">No data</div>
      {% endfor %}
    </div>
    <div class="callout">
      <h4>Approaching tier change</h4>
      {% set approachers = o.outlets | selectattr('approaching_tier', '!=', none) | list %}
      {% for outlet in approachers %}
      <div class="callout-item"><a href="/outlet/{{ outlet.outlet_id }}">{{ outlet.outlet_name }}</a> → <span style="color:var(--amber)">{{ outlet.approaching_tier }}</span></div>
      {% else %}<div class="callout-item" style="color:var(--text3)">None within 5 claims</div>
      {% endfor %}
    </div>
  </div>
  {% endif %}
</div>

<!-- ============ SECTION 4: OUTLIERS ============ -->
<div class="section">
  <div class="section-head"><h2>Outlier Detection</h2><p>Outlets with statistically unusual behavior. Corpus averages: overstated {{ outliers.get('avg_overstated_pct', '—') }}% · disputed {{ outliers.get('avg_disputed_pct', '—') }}%.</p></div>
  {% set ol = outliers %}
  {% if ol.get('error') %}<div class="err">{{ ol.error }}</div>
  {% elif ol.outliers %}
  <div class="outlier-list">
    {% for item in ol.outliers %}
    <div class="outlier-item">
      <span class="outlier-badge {{ 'badge-ov' if 'overstated' in item.type else ('badge-di' if 'disputed' in item.type else 'badge-vol') }}">{{ item.type.replace('_', ' ') }}</span>
      <div>
        <div class="outlier-msg">{{ item.msg }}</div>
        <div class="outlier-outlet"><a href="/outlet/{{ item.outlet_id }}">{{ item.outlet }}</a></div>
      </div>
    </div>
    {% endfor %}
  </div>
  {% else %}
  <div class="no-outliers">✓ No outliers detected against current corpus averages.</div>
  {% endif %}
</div>

<!-- ============ SECTION 5: CORPUS GROWTH ============ -->
<div class="section">
  <div class="section-head"><h2>Corpus Growth &amp; Pipeline Health</h2><p>30-day ingestion and verdict throughput. Rates near 1.0 = healthy pipeline.</p></div>
  {% set c = corpus %}
  {% if c.get('error') %}<div class="err">{{ c.error }}</div>{% else %}
  <div class="two-col" style="margin-bottom:20px">
    <div class="chart-card">
      <h3>Daily ingestion (articles)</h3>
      <div class="chart-wrap"><canvas id="c-ing"></canvas></div>
    </div>
    <div class="chart-card">
      <h3>Daily verdict throughput</h3>
      <div class="chart-wrap"><canvas id="c-verd"></canvas></div>
    </div>
  </div>
  <div class="cards">
    <div class="card">
      <div class="card-num">{{ "{:,}".format(c.unextracted) }}</div>
      <div class="card-label">Unextracted backlog</div>
    </div>
    <div class="card">
      <div class="card-num">{{ "{:,}".format(c.unverified) }}</div>
      <div class="card-label">Unverified backlog</div>
    </div>
    <div class="card">
      {% set er = c.extraction_rate %}
      <div class="card-num rate-card {{ 'rate-ok' if er and er >= 0.8 else ('rate-warn' if er and er >= 0.5 else 'rate-bad') }}">{{ er if er is not none else '—' }}</div>
      <div class="card-label">Extraction rate (24h)</div>
    </div>
    <div class="card">
      {% set vr = c.verdict_rate %}
      <div class="card-num rate-card {{ 'rate-ok' if vr and vr >= 0.8 else ('rate-warn' if vr and vr >= 0.5 else 'rate-bad') }}">{{ vr if vr is not none else '—' }}</div>
      <div class="card-label">Verdict rate (24h)</div>
    </div>
  </div>
  {% endif %}
</div>

<!-- ============ SECTION 6: DEBATES ============ -->
<div class="section">
  <div class="section-head"><h2>Debate Pipeline</h2><p>All debate events and their verification status.</p></div>
  {% set db = debates %}
  {% if db.get('error') %}<div class="err">{{ db.error }}</div>{% else %}
  <div class="tbl-wrap">
    <table class="debate-table">
      <thead><tr>
        <th>Event</th><th>Date</th><th>Public</th><th>Total claims</th><th>Verified</th><th>Pending</th><th>Verified%</th><th>API visible</th>
      </tr></thead>
      <tbody>
        {% for d in db.debates %}
        <tr>
          <td>{{ d.name }}</td>
          <td style="font-family:var(--mono);font-size:12px">{{ d.date }}</td>
          <td>{% if d.is_public %}<span class="dot-green"></span>{% else %}<span class="dot-gray"></span>{% endif %}</td>
          <td>{{ d.total }}</td>
          <td>{{ d.verified }}</td>
          <td>{{ d.unverified }}</td>
          <td>{{ d.pct|string + '%' if d.pct is not none else '—' }}</td>
          <td>{% if d.api_visible %}✓{% elif d.total == 0 %}<span style="color:var(--text3)">upcoming</span>{% else %}<span style="color:var(--text3)">gated</span>{% endif %}</td>
        </tr>
        {% endfor %}
      </tbody>
    </table>
  </div>
  {% endif %}
</div>

<!-- ============ SECTION 7: COST & API USAGE ============ -->
<div class="section">
  <div class="section-head"><h2>Cost &amp; API Usage</h2><p>Anthropic API spend and public API customer activity.</p></div>
  {% set cu = cost %}
  {% if cu.get('error') %}<div class="err">{{ cu.error }}</div>{% else %}
  <div class="cards">
    <div class="card">
      <div class="card-num">{{ '$' + '%.2f'|format(cu.cost_30d) if cu.cost_30d else '—' }}</div>
      <div class="card-label">Anthropic cost (30d)</div>
    </div>
    <div class="card">
      <div class="card-num">{{ cu.active_keys }}</div>
      <div class="card-label">Active API keys</div>
    </div>
    <div class="card">
      <div class="card-num">{{ "{:,}".format(cu.api_calls_7d) }}</div>
      <div class="card-label">API calls (7d)</div>
    </div>
    <div class="card">
      <div class="card-num" style="font-size:14px;padding-top:6px">{{ cu.top_endpoint or '—' }}</div>
      <div class="card-label">Top endpoint (7d)</div>
    </div>
  </div>
  {% if cu.api_calls_7d == 0 %}<p style="color:var(--text3);font-size:12px;margin-top:8px">No active API customers yet.</p>{% endif %}
  {% endif %}
</div>

</div><!-- /wrap -->

<script>
// Chart colors
const VC = {
  supported:     '#22c55e',
  plausible:     '#16a34a',
  corroborated:  '#15803d',
  overstated:    '#f59e0b',
  disputed:      '#f87171',
  not_supported: '#dc2626',
  not_verifiable:'#6b7280',
  opinion:       '#4b5563',
};
const DARK = {plugins:{legend:{labels:{color:'rgba(255,255,255,0.55)',font:{size:11}}}},scales:{x:{ticks:{color:'rgba(255,255,255,0.4)',font:{size:10}},grid:{color:'rgba(255,255,255,0.05)'}},y:{ticks:{color:'rgba(255,255,255,0.4)',font:{size:10}},grid:{color:'rgba(255,255,255,0.05)'}}}};

// Section 2: donut
{% if not verdict.get('error') %}
(function(){
  const lt = {{ verdict.lifetime | tojson }};
  const labels = Object.keys(lt);
  const data = Object.values(lt);
  const colors = labels.map(l => VC[l] || '#888');
  new Chart(document.getElementById('c-donut'), {
    type: 'doughnut',
    data: { labels, datasets: [{ data, backgroundColor: colors, borderWidth: 1, borderColor: '#12121a' }] },
    options: { plugins: { legend: { position: 'right', labels: { color: 'rgba(255,255,255,0.55)', font: { size: 11 }, boxWidth: 12 } } }, cutout: '60%' }
  });

  // Trend chart
  const trend = {{ verdict.trend | tojson }};
  const days = Object.keys(trend).sort();
  const verdicts = ['supported','plausible','corroborated','overstated','disputed','not_supported','not_verifiable'];
  const datasets = verdicts.map(v => ({
    label: v, data: days.map(d => trend[d]?.[v] || 0),
    backgroundColor: VC[v] + 'cc', borderColor: VC[v], borderWidth: 0, fill: true
  }));
  new Chart(document.getElementById('c-trend'), {
    type: 'bar',
    data: { labels: days, datasets },
    options: { ...DARK, plugins: { ...DARK.plugins, legend: { ...DARK.plugins.legend, display: false } }, scales: { x: { ...DARK.scales.x, stacked: true }, y: { ...DARK.scales.y, stacked: true } } }
  });
})();
{% endif %}

// Section 5: ingestion + throughput
{% if not corpus.get('error') %}
(function(){
  const ing = {{ corpus.ingestion | tojson }};
  const thr = {{ corpus.throughput | tojson }};
  new Chart(document.getElementById('c-ing'), {
    type: 'bar',
    data: { labels: ing.map(r=>r.day), datasets: [{ data: ing.map(r=>r.articles), backgroundColor: 'rgba(168,85,247,0.6)', borderRadius: 2 }] },
    options: { ...DARK, plugins: { legend: { display: false } } }
  });
  new Chart(document.getElementById('c-verd'), {
    type: 'bar',
    data: { labels: thr.map(r=>r.day), datasets: [{ data: thr.map(r=>r.verdicts), backgroundColor: 'rgba(34,197,94,0.6)', borderRadius: 2 }] },
    options: { ...DARK, plugins: { legend: { display: false } } }
  });
})();
{% endif %}

// Sortable outlet table
var sortDir = {};
function sortTable(col) {
  var table = document.getElementById('outlet-table');
  var tbody = table.tBodies[0];
  var rows = Array.from(tbody.rows);
  var asc = !sortDir[col];
  sortDir = {};
  sortDir[col] = asc;
  rows.sort(function(a, b) {
    var av = a.cells[col].textContent.trim();
    var bv = b.cells[col].textContent.trim();
    var an = parseFloat(av.replace(/[^0-9.\-]/g, ''));
    var bn = parseFloat(bv.replace(/[^0-9.\-]/g, ''));
    if (!isNaN(an) && !isNaN(bn)) return asc ? an - bn : bn - an;
    return asc ? av.localeCompare(bv) : bv.localeCompare(av);
  });
  rows.forEach(function(r) { tbody.appendChild(r); });
}
</script>
</body>
</html>"""


@app.route('/ops/insights', methods=['GET'])
def ops_insights():
    auth_err = _ops_auth()
    if auth_err is not None:
        return auth_err
    if request.args.get('refresh') == '1':
        _INSIGHTS_CACHE['expires_at'] = 0
    ctx = build_insights_context()
    from flask import render_template_string
    return render_template_string(_OPS_INSIGHTS_HTML, **ctx)


''' + ANCHOR

content = content.replace(ANCHOR, INSIGHTS_CODE)

with open(path, 'w') as f:
    f.write(content)

print("✓ /ops/insights route added to api.py")
