#!/usr/bin/env python3
"""capture_probe.py - MEASUREMENT for the capture worker: run N concurrent DRY-RUN captures of a VOD and sample
CPU, memory and wav throughput every --interval seconds for --duration seconds. Writes nothing to the DB (children
run with --dry-run) and nothing to R2 unless --r2-test-mb is given (then uploads that many MB of random bytes once
and reports MB/s). Refuses to start while a real watchdog is running (the liveness check adopts the newest wav).
  venv/bin/python3 capture_probe.py --n 2 --duration 600 --url https://www.youtube.com/watch?v=VmNCTGl9yxw \
      --event-slug s12-rehearsal3-cd7-20260902 --speaker-order 3,234,235 [--r2-test-mb 200]
Output: logs/capture_probe_<stamp>.jsonl (one sample per interval) and a summary on stdout."""
import argparse, json, os, shlex, subprocess, sys, time, datetime, glob
import psutil
HERE = os.path.dirname(os.path.abspath(__file__))
def r2_test(mb):
    import boto3, botocore
    need = ['R2_BUCKET', 'R2_ENDPOINT', 'R2_ACCESS_KEY_ID', 'R2_SECRET_ACCESS_KEY']
    if any(not os.environ.get(k) for k in need):
        return {'skipped': 'R2 env not set'}
    s3 = boto3.client('s3', endpoint_url=os.environ['R2_ENDPOINT'], aws_access_key_id=os.environ['R2_ACCESS_KEY_ID'],
                      aws_secret_access_key=os.environ['R2_SECRET_ACCESS_KEY'], region_name='auto', config=botocore.config.Config(signature_version='s3v4'))
    key = f"probe/{datetime.datetime.utcnow().strftime('%Y%m%d_%H%M%S')}_{mb}mb.bin"
    blob = os.urandom(mb * 1024 * 1024)
    t0 = time.time(); s3.put_object(Bucket=os.environ['R2_BUCKET'], Key=key, Body=blob); dt = time.time() - t0
    s3.delete_object(Bucket=os.environ['R2_BUCKET'], Key=key)
    return {'mb': mb, 'seconds': round(dt, 2), 'mb_per_s': round(mb / dt, 2), 'key': key, 'deleted': True}
_PROC = {}
def tree(p):
    """Process objects must persist across samples: psutil's cpu_percent(interval=None) is 0.0 on a fresh object."""
    try: found = [p] + p.children(recursive=True)
    except psutil.Error: found = [p]
    out = []
    for x in found:
        out.append(_PROC.setdefault(x.pid, x))
    return out
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--n', type=int, default=2); ap.add_argument('--duration', type=int, default=600); ap.add_argument('--interval', type=int, default=30)
    ap.add_argument('--url', required=True); ap.add_argument('--event-slug', required=True); ap.add_argument('--speaker-order', required=True)
    ap.add_argument('--stagger', type=float, default=3.0, help='seconds between child starts (distinct wav stamps)')
    ap.add_argument('--r2-test-mb', type=int, default=0)
    a = ap.parse_args()
    for p in psutil.process_iter(['cmdline']):
        toks = p.info['cmdline'] or []
        if any(t.endswith('watchdog_debate_capture.py') for t in toks) or (any(t.endswith('debate_stream.py') for t in toks) and '--dry-run' not in toks):
            sys.exit(f'REFUSED: a real capture is running (pid {p.pid}): {" ".join(toks)[:100]}')
    os.makedirs(os.path.join(HERE, 'logs'), exist_ok=True)
    stamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
    out = os.path.join(HERE, 'logs', f'capture_probe_{stamp}.jsonl')
    py = sys.executable
    child_cmd = os.environ.get('PROBE_CHILD_CMD')  # test-only override
    procs, wav_before = [], set(glob.glob(os.path.join(HERE, 'debate_audio', 'event_*.wav')))
    for i in range(a.n):
        cmd = shlex.split(child_cmd) if child_cmd else [py, '-u', os.path.join(HERE, 'debate_stream.py'), '--mode', 'live', '--url', a.url,
                                                          '--event-slug', a.event_slug, '--speaker-order', a.speaker_order, '--dry-run']
        logf = open(os.path.join(HERE, 'logs', f'capture_probe_{stamp}_child{i}.log'), 'w')
        procs.append((subprocess.Popen(cmd, cwd=HERE, stdout=logf, stderr=subprocess.STDOUT), logf))
        print(f'started child {i} pid {procs[-1][0].pid}'); time.sleep(a.stagger)
    print(f'sampling every {a.interval}s for {a.duration}s -> {out}')
    sys_before = psutil.cpu_percent(interval=None)
    t_start = time.time(); last_sizes = {}; samples = []
    with open(out, 'w') as fo:
        while time.time() - t_start < a.duration:
            time.sleep(a.interval)
            wavs = sorted(set(glob.glob(os.path.join(HERE, 'debate_audio', 'event_*.wav'))) - wav_before)
            sizes = {os.path.basename(w): os.path.getsize(w) for w in wavs}
            rates = {k: round((v - last_sizes.get(k, v)) / a.interval / 1024, 1) for k, v in sizes.items()}
            last_sizes = sizes
            per = []
            for i, (p, _) in enumerate(procs):
                if p.poll() is not None:
                    per.append({'child': i, 'exited': p.returncode}); continue
                ps = tree(psutil.Process(p.pid))
                cpu = sum(x.cpu_percent(interval=None) for x in ps)
                rss = sum(x.memory_info().rss for x in ps) / 1e6
                per.append({'child': i, 'procs': len(ps), 'cpu_pct': round(cpu, 1), 'rss_mb': round(rss, 1),
                            'names': sorted({x.name() for x in ps})})
            vm = psutil.virtual_memory()
            s = {'t': round(time.time() - t_start), 'sys_cpu_pct': psutil.cpu_percent(interval=None), 'sys_mem_used_mb': round((vm.total - vm.available) / 1e6),
                 'sys_mem_avail_mb': round(vm.available / 1e6), 'children': per, 'wav_kb_per_s': rates, 'wav_mb': {k: round(v / 1e6, 1) for k, v in sizes.items()}}
            samples.append(s); fo.write(json.dumps(s) + '\n'); fo.flush()
            print(json.dumps({k: s[k] for k in ('t', 'sys_cpu_pct', 'sys_mem_avail_mb', 'wav_kb_per_s')}), [ (c.get('cpu_pct'), c.get('rss_mb'), c.get('exited')) for c in per])
    for p, logf in procs:
        if p.poll() is None:
            p.terminate()
            try: p.wait(15)
            except subprocess.TimeoutExpired: p.kill()
        logf.close()
    alive = [s for s in samples if s['children'] and all('exited' not in c for c in s['children'])]
    if alive:
        peak_cpu = max(sum(c['cpu_pct'] for c in s['children']) for s in alive)
        peak_rss = max(sum(c['rss_mb'] for c in s['children']) for s in alive)
        min_avail = min(s['sys_mem_avail_mb'] for s in alive)
        print(f'\nSUMMARY n={a.n}: peak children CPU {peak_cpu:.0f}% (of {psutil.cpu_count()} cores = {psutil.cpu_count()*100}%), peak children RSS {peak_rss:.0f} MB, min system avail {min_avail} MB, samples {len(alive)}/{len(samples)}')
        exp = 16000 * 2 / 1024
        rates = [r for s in alive for r in s['wav_kb_per_s'].values() if r > 0]
        print(f'wav write rates KB/s: min {min(rates) if rates else 0}, max {max(rates) if rates else 0} (16 kHz mono PCM = {exp:.1f} KB/s per capture)')
    else:
        print('\nSUMMARY: no sample with all children alive - read the child logs')
    if a.r2_test_mb:
        print('R2 test:', json.dumps(r2_test(a.r2_test_mb)))
    print('log:', out)
if __name__ == '__main__':
    main()
