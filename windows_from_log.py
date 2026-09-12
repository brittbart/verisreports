#!/usr/bin/env python3
"""windows_from_log.py <rehearsal_log> <wav_path> [min_span=20]
Pair each '[DRY RUN] utterance: speaker_id=S rev_idx=K' line with the following
'[HH:MM:SS] [...]' line, convert wall time to wav offset using the wav filename
stamp (event_N_YYYYMMDD_HHMMSS.wav), join consecutive same-index utterances
(gap <= 8 s), trim 3 s each edge, print spans >= min_span as label:start:end.
Read-only. Labels: idxK_spkS (S=None when unconfirmed)."""
import re, sys, os
from datetime import datetime, timedelta
log, wav = sys.argv[1], sys.argv[2]
min_span = float(sys.argv[3]) if len(sys.argv) > 3 else 20.0
m = re.search(r'_(\d{8})_(\d{6})\.wav$', os.path.basename(wav))
assert m, "wav name must end _YYYYMMDD_HHMMSS.wav"
t0 = datetime.strptime(m.group(1) + m.group(2), '%Y%m%d%H%M%S')
utt_re = re.compile(r'\[DRY RUN\].*?utterance: speaker_id=(\S+) rev_idx=(\d+)')
ts_re = re.compile(r'^\s*\[(\d\d):(\d\d):(\d\d)\] \[')
rows, pending = [], None
for line in open(log, errors='replace'):
    mu = utt_re.search(line)
    if mu:
        pending = (mu.group(1), int(mu.group(2))); continue
    mt = ts_re.match(line)
    if mt and pending:
        t = t0.replace(hour=int(mt.group(1)), minute=int(mt.group(2)), second=int(mt.group(3)))
        if t < t0: t += timedelta(days=1)
        rows.append((pending[0], pending[1], (t - t0).total_seconds())); pending = None
spans, cur = [], None
for spk, idx, sec in rows:
    if cur and cur[1] == idx and sec - cur[3] <= 8.0:
        cur[3] = sec
    else:
        if cur: spans.append(cur)
        cur = [spk, idx, sec - 3.0, sec]
if cur: spans.append(cur)
by_idx = {}
for spk, idx, s, e in spans:
    s, e = max(0.0, s + 3.0), e - 3.0
    if e - s >= min_span:
        by_idx.setdefault(idx, []).append((spk, s, e))
print(f"# {os.path.basename(wav)}  start {t0:%H:%M:%S}  utterances {len(rows)}  spans>={min_span:g}s")
for idx in sorted(by_idx):
    for spk, s, e in sorted(by_idx[idx], key=lambda x: x[1] - x[2])[:4]:
        print(f"idx{idx}_spk{spk}:{s:.0f}:{e:.0f}    # {e - s:.0f} s")
