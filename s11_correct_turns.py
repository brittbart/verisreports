#!/usr/bin/env python3
"""s11_correct_turns.py - attribute by TURN, not by utterance."""
import argparse
import json
import os
import sys
THRESHOLD = 0.55
NOMATCH_FLOOR = 0.70   # best distance at/above this = matched nobody enrolled (Sept 9 moderators: 0.81-1.09)
MODERATOR_MARGIN = 0.05
SAMPLES = 6
WINDOW = 8.0
MIN_TURN = 3.0
CONTAIN_TOL = 1.0
def cluster_turns(monos):
    """Contiguous same-cluster stretches: {cluster: [(start, end, words), ...]}."""
    out, cur, cid = {}, None, None
    for m in monos:
        ts = [e for e in m.get("elements", []) if e.get("ts") is not None]
        if not ts:
            continue
        a = float(ts[0]["ts"])
        b = float(ts[-1].get("end_ts") or ts[-1]["ts"])
        c = m.get("speaker")
        if c == cid and cur is not None:
            cur = (cur[0], b, cur[2] + len(ts))
        else:
            if cur is not None:
                out.setdefault(cid, []).append(cur)
            cur, cid = (a, b, len(ts)), c
    if cur is not None:
        out.setdefault(cid, []).append(cur)
    return out
def find_cluster(turns, ts, tol=CONTAIN_TOL):
    """Cluster whose span contains ts. Exact containment is tried across ALL clusters
    first; only if none hits is the nearest span starting within tol AFTER ts used.
    timestamp_seconds is int(float_ts) (debate_stream.py:885) so the stored value can
    undershoot the true time by up to 1s and can never overshoot - left edge only.
    Returns (cluster_id, gap): gap 0.0 exact, >0 recovered, (None, None) no hit."""
    for cid, tl in turns.items():
        if any(a <= ts <= b for a, b, _w in tl):
            return cid, 0.0
    best, bestd = None, None
    for cid, tl in turns.items():
        for a, b, _w in tl:
            if a - tol < ts < a and (bestd is None or a - ts < bestd):
                best, bestd = cid, a - ts
    return best, bestd
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--event-id", type=int, required=True)
    ap.add_argument("--run", action="append", required=True,
                    help="wav:async_json for each capture run, in run order")
    ap.add_argument("--extra-speaker", action="append", type=int, default=[])
    ap.add_argument("--alias", action="append", default=[])
    ap.add_argument("--moderator-id", type=int, default=3)
    ap.add_argument("--score-only", action="store_true")
    ap.add_argument("--gold", default="s11_gold_standard.json")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) or ".")
    from verdict_engine import get_connection
    from voice_verify import load_enrolled_embeddings, cosine_distance
    from s11_embed_cache import CachedEmbedder
    import numpy as np
    ALIAS = {}
    for a in args.alias:
        k, v = a.split("=")
        ALIAS[int(k)] = int(v)
    norm = lambda s: ALIAS.get(s, s)
    conn = get_connection(); cur = conn.cursor()
    cur.execute("""SELECT es.speaker_id FROM event_speakers es
                   JOIN speakers s ON s.id=es.speaker_id
                   WHERE es.event_id=%s AND s.speaker_type IN ('politician','official')
                     AND es.is_active""", (args.event_id,))
    roster = [r[0] for r in cur.fetchall()]
    for x in args.extra_speaker:
        if x not in roster:
            roster.append(x)
    enrolled = load_enrolled_embeddings(allowed_speaker_ids=roster)
    print("roster %s ; enrolled %s" % (roster, sorted(enrolled)))
    if len(enrolled) != len(roster):
        sys.exit("ABORT: coverage is all-or-none. Missing %s"
                 % [s for s in roster if s not in enrolled])
    mod_ids = {args.moderator_id} | {k for k, v in ALIAS.items() if v == args.moderator_id}
    cur.execute("""SELECT id, speaker_id, utterance_order, timestamp_seconds
                   FROM speaker_utterances WHERE event_id=%s ORDER BY utterance_order""",
                (args.event_id,))
    rows = [dict(id=r[0], spk=r[1], order=r[2], ts=r[3]) for r in cur.fetchall()]
    runs, prev = [[]], None
    for r in rows:
        if prev is not None and r["ts"] is not None and r["ts"] < prev - 1:
            runs.append([])
        runs[-1].append(r)
        if r["ts"] is not None:
            prev = r["ts"]
    print("utterances %d in %d run(s)" % (len(rows), len(runs)))
    if len(args.run) != len(runs):
        sys.exit("ABORT: %d run(s) inferred, %d --run given" % (len(runs), len(args.run)))
    assigned = {}
    for run_rows, spec in zip(runs, args.run):
        wav, aj = spec.split(":", 1)
        monos = json.load(open(aj))["monologues"]
        turns = cluster_turns(monos)
        emb = CachedEmbedder(wav)
        print("\n%s -> %s : %d clusters"
              % (os.path.basename(wav), os.path.basename(aj), len(turns)))
        cluster_spk = {}
        cluster_conf = {}   # per cluster: 'named' | 'weak' | 'no_match' | 'no_evidence'
        for cid, tl in sorted(turns.items(), key=lambda kv: -sum(t[1]-t[0] for t in kv[1])):
            usable = sorted([t for t in tl if t[1]-t[0] >= MIN_TURN],
                            key=lambda t: -(t[1]-t[0]))[:SAMPLES]
            total = sum(t[1]-t[0] for t in tl)
            if not usable:
                print("  cluster %-3s %6.0fs  no turn >= %.0fs - unnamed" % (cid, total, MIN_TURN))
                cluster_spk[cid] = None; cluster_conf[cid] = "no_evidence"; continue
            vecs = []
            for a, b, _w in usable:
                mid = (a + b) / 2.0
                half = min(WINDOW, b - a) / 2.0
                v = emb.embed(max(0.0, mid - half), min(mid + half, emb.duration))
                if v is not None:
                    vecs.append(v)
            if not vecs:
                cluster_spk[cid] = None; cluster_conf[cid] = "no_evidence"; continue
            avg = np.mean(np.vstack(vecs), axis=0)
            avg = avg / np.linalg.norm(avg)
            d = {s: float(cosine_distance(avg, e["embedding"])) for s, e in enrolled.items()}
            best = min(d, key=d.get)
            mod_best = min((d[s] for s in d if s in mod_ids), default=None)
            if (mod_best is not None and best not in mod_ids
                    and mod_best - d[best] < MODERATOR_MARGIN):
                best = min(mod_ids & set(d), key=lambda s: d[s])
            spk = best if d[best] < THRESHOLD else None
            cluster_spk[cid] = spk
            cluster_conf[cid] = "named" if spk is not None else ("no_match" if d[best] >= NOMATCH_FLOOR else "weak")
            print("  cluster %-3s %6.0fs  %2d turns, %d samples -> %-6s (%s)"
                  % (cid, total, len(tl), len(vecs), spk,
                     ", ".join("%s=%.3f" % (k, v) for k, v in sorted(d.items()))))
        emb.save()
        n_exact = n_recov = n_miss = 0
        for r in run_rows:
            if r["ts"] is None:
                assigned[r["id"]] = None; n_miss += 1; continue
            hit, gap = find_cluster(turns, r["ts"])
            if hit is None:
                n_miss += 1
            elif gap > 0.0:
                n_recov += 1
            else:
                n_exact += 1
            assigned[r["id"]] = cluster_spk.get(hit) if hit is not None else None
        print("  containment: %d exact, %d recovered within %.1fs, %d outside any span"
              % (n_exact, n_recov, CONTAIN_TOL, n_miss))
        if args.score_only:
            gold = json.load(open(args.gold))
            cmap = {int(k): v["speaker_id"] for k, v in gold["clusters"].items()}
            scored = wrong = right = abst = 0
            danger = cover = 0
            unmatched_wrong = 0
            rec_scored = rec_right = rec_wrong = rec_abst = 0
            for r in run_rows:
                truth = None
                tgap = None
                if r["ts"] is not None:
                    tcid, tgap = find_cluster(turns, r["ts"])
                    if tcid is not None:
                        truth = cmap.get(tcid)
                if truth is None:
                    continue
                is_rec = tgap is not None and tgap > 0.0
                scored += 1
                if is_rec:
                    rec_scored += 1
                got = assigned[r["id"]]
                truth_is_candidate = norm(truth) not in mod_ids and norm(truth) != args.moderator_id
                if got is None and cluster_conf.get(tcid) == "no_match" and truth_is_candidate:
                    # evidence says nobody enrolled spoke here, live says a candidate did: that is wrong, not unknown
                    wrong += 1; danger += 1; unmatched_wrong += 1
                    if is_rec:
                        rec_wrong += 1
                elif got is None:
                    abst += 1
                    if is_rec:
                        rec_abst += 1
                elif norm(got) == norm(truth):
                    right += 1
                    if is_rec:
                        rec_right += 1
                else:
                    wrong += 1
                    if is_rec:
                        rec_wrong += 1
                    if norm(got) in mod_ids or norm(got) == args.moderator_id:
                        cover += 1
                    else:
                        danger += 1
            print("\n  scored %d : correct %d, WRONG %d, abstained %d"
                  % (scored, right, wrong, abst))
            print("  of the wrong: %d DANGEROUS (candidate credited with other speech), "
                  "%d coverage-only (sent to moderator)" % (danger, cover))
            print("  of the dangerous: %d asserted from clusters matching NO enrolled print (best >= %.2f)"
                  % (unmatched_wrong, NOMATCH_FLOOR))
            print("  recovered by the %.1fs tolerance: %d scored - correct %d, WRONG %d, "
                  "abstained %d" % (CONTAIN_TOL, rec_scored, rec_right, rec_wrong, rec_abst))
    if args.score_only or not args.apply:
        print("\nnothing written.")
        return 0
    for uid, spk in assigned.items():
        cur.execute("""UPDATE speaker_utterances SET speaker_id=%s, attribution_uncertain=%s
                       WHERE id=%s""", (spk, spk is None, uid))
    conn.commit()
    print("\nWROTE %d utterance(s)" % len(assigned))
    cur.close(); conn.close()
    return 0
if __name__ == "__main__":
    sys.exit(main())
