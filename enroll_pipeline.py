#!/usr/bin/env python3
"""enroll_pipeline.py - drive the existing enrolment chain for one manifest, in stages, idempotently.
  --stage prep    : fetch every source raw (yt-dlp -x wav, skipped if present), transcribe each (s11_revai_clusters.py,
                    skipped if s11_<source>_raw_revai.json exists), then print the per-cluster label report
                    (speaking time, turns, longest monologue opening, hits for --phrases and a built-in self-identification set).
                    STOPS here: write the offset map (LIST of {clip,json,cluster,evidence}) by reading the report.
  --stage finish  : s11_pick_offsets (report, then --apply only if 4 of 4 derived) -> s11_enroll_prep --no-fetch ->
                    s11_verify_clips --transcribe (re-run once without it if jobs are pending) -> voice_verify.py enroll per
                    speaker (ONLY clips that verified OK) -> s11_print_health for the roster. Any failing gate stops the stage.
  --stage all     : prep then finish (only sensible when the map already exists).
Nothing in the chain is reimplemented: every step is the same script with the same arguments as a manual run.
  venv/bin/python3 enroll_pipeline.py --manifest s11_enroll_manifest_maine.json --stage prep --phrases "my name,i was born,senator"
  venv/bin/python3 enroll_pipeline.py --manifest s11_enroll_manifest_maine.json --map s11_offset_map_maine.json --stage finish"""
import argparse, glob, json, os, re, subprocess, sys, time
HERE = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable
SELF_ID = ['my name', "i'm running", 'i am running', 'i was born', 'thank you for having me', 'thanks for having me', 'welcome', 'thank you for joining', 'my husband', 'my wife', 'my district', 'as a']
def run(cmd, tail=None, check=True):
    """Run a step, streaming its output line by line as it happens (so a long transcription shows progress), and
    return the full output for the gates. tail is kept for signature compatibility; everything is shown live."""
    print('$', ' '.join(cmd), flush=True)
    p = subprocess.Popen(cmd, cwd=HERE, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, bufsize=1)
    lines = []
    for line in p.stdout:
        print('  ' + line.rstrip(), flush=True); lines.append(line)
    rc = p.wait(); out = ''.join(lines)
    if check and rc != 0:
        sys.exit(f'STOP: {cmd[1] if len(cmd) > 1 else cmd[0]} exited {rc}')
    return out
def prep(man, phrases):
    for c in man['clips']:
        raw = os.path.join(HERE, 'debate_audio', f"{c['source']}_raw.wav")
        if os.path.exists(raw):
            print(f"raw present: {os.path.basename(raw)} ({os.path.getsize(raw)/1e6:.0f} MB)")
        else:
            run([os.path.join(HERE, 'venv', 'bin', 'yt-dlp'), '-q', '-x', '--audio-format', 'wav', '-o', f"debate_audio/{c['source']}_raw.%(ext)s", c['url']], tail=3)
            if not os.path.exists(raw): sys.exit(f'STOP: raw not produced for {c["source"]}')
        tx = os.path.join(HERE, f"s11_{c['source']}_raw_revai.json")
        if os.path.exists(tx):
            print(f"transcript present: {os.path.basename(tx)}")
        else:
            run([PY, os.path.join(HERE, 's11_revai_clusters.py'), f"debate_audio/{c['source']}_raw.wav"], tail=2)
            if not os.path.exists(tx): sys.exit(f'STOP: transcript not produced for {c["source"]}')
    seen = set()
    for c in man['clips']:
        if c['source'] in seen: continue
        seen.add(c['source'])
        report(c['source'], phrases + SELF_ID)
    print('\nPREP DONE. Write the offset map from the reports above, then --stage finish --map <map>.')
def report(src, phrases):
    d = json.load(open(os.path.join(HERE, f's11_{src}_raw_revai.json')))
    print(f'\n=== {src}  ({len(d["monologues"])} monologues)')
    per = {}
    for m in d['monologues']:
        el = [e for e in m['elements'] if e.get('type') == 'text']
        if not el: continue
        sp = m['speaker']; t0 = el[0]['ts']; t1 = el[-1].get('end_ts', el[-1]['ts']); txt = ' '.join(e['value'] for e in el)
        p = per.setdefault(sp, {'secs': 0.0, 'words': 0, 'turns': 0, 'longest': (0, 0, ''), 'hits': []})
        p['secs'] += t1 - t0; p['words'] += len(el); p['turns'] += 1
        if t1 - t0 > p['longest'][0]: p['longest'] = (t1 - t0, t0, txt[:140])
        low = txt.lower()
        for ph in phrases:
            for mm in re.finditer(re.escape(ph.lower()), low):
                a = max(0, mm.start() - 60); b = min(len(txt), mm.end() + 60)
                p['hits'].append((ph, round(t0, 1), txt[a:b]))
    for sp, p in sorted(per.items(), key=lambda kv: -kv[1]['secs']):
        print(f'  cluster {sp}: {p["secs"]:7.1f}s  {p["words"]:5d} words  {p["turns"]:3d} turns  longest {p["longest"][0]:.1f}s @ {p["longest"][1]:.1f}s: {p["longest"][2]!r}')
        for ph, ts, ctx in p['hits'][:10]: print(f'      [{ph}] @ {ts}s: ...{ctx}...')
        if len(p['hits']) > 10: print(f'      (+{len(p["hits"]) - 10} more hits)')
def finish(man, mpath, mapfile):
    n = len(man['clips'])
    out = run([PY, os.path.join(HERE, 's11_pick_offsets.py'), '--manifest', mpath, '--map', mapfile], tail=6)
    if f'{n} of {n} offsets derived' not in out: sys.exit('STOP: not every offset derived - fix the map or a dur, then re-run finish')
    run([PY, os.path.join(HERE, 's11_pick_offsets.py'), '--manifest', mpath, '--map', mapfile, '--apply'], tail=2)
    out = run([PY, os.path.join(HERE, 's11_enroll_prep.py'), '--manifest', mpath, '--no-fetch'], tail=12)
    paths = dict(re.findall(r'PASS (\S+)\s+spk \d+ : (\S+\.wav)', out))
    if len(paths) != n: sys.exit(f'STOP: enroll_prep passed {len(paths)} of {n} clips')
    out = run([PY, os.path.join(HERE, 's11_verify_clips.py'), '--manifest', mpath, '--map', mapfile, '--transcribe'], tail=10)
    if 'Safe to enrol' not in out:
        time.sleep(60); out = run([PY, os.path.join(HERE, 's11_verify_clips.py'), '--manifest', mpath, '--map', mapfile], tail=10)
        if 'Safe to enrol' not in out: sys.exit('STOP: clips not verified')
    ok = set(re.findall(r'^(\S+)\s+\d+\s+\d+\s+\d+\s+[\d.]+\s+OK', out, re.M))
    by_spk = {}
    for c in man['clips']:
        if c['clip'] in ok and c['clip'] in paths: by_spk.setdefault((c['speaker_id'], c['name']), []).append(paths[c['clip']])
    for (sid, name), wavs in by_spk.items():
        if len(wavs) < 2: sys.exit(f'STOP: speaker {sid} {name} has {len(wavs)} verified clip(s); the two-source rule needs 2')
        run([PY, os.path.join(HERE, 'voice_verify.py'), 'enroll', '--speaker-id', str(sid), '--speaker-name', name, '--audio'] + wavs, tail=4)
    run([PY, os.path.join(HERE, 's11_print_health.py')] + [str(sid) for sid, _ in by_spk], tail=12)
    print('\nFINISH DONE. Next: generate_speaker_context.py --event-id <id>, then check exclusivity (context_check.py) and pre-flight.')
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--manifest', required=True); ap.add_argument('--map'); ap.add_argument('--stage', choices=['prep', 'finish', 'all'], required=True)
    ap.add_argument('--phrases', default='', help='comma-separated extra evidence phrases for the label report')
    a = ap.parse_args()
    man = json.load(open(os.path.join(HERE, a.manifest)))
    phrases = [p.strip() for p in a.phrases.split(',') if p.strip()]
    if a.stage in ('prep', 'all'): prep(man, phrases)
    if a.stage in ('finish', 'all'):
        if not a.map: sys.exit('--map required for finish')
        finish(man, a.manifest, a.map)
if __name__ == '__main__':
    main()
