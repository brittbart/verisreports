#!/usr/bin/env python3
"""s11_embed_fast.py - embed many segments of one audio file without re-reading it.

voice_verify.extract_embedding() re-reads and re-decodes the ENTIRE file on every
call (open defect S11-4). At one call per gold span that is merely slow; at one
call per rolling window across a 48-minute capture it is roughly a thousand full
re-reads of 93 MB and the experiment becomes unrunnable.

This loads once and slices in memory. It is a HOIST, not a reimplementation:
extract_embedding resamples the whole file and only then crops, so doing the load
and resample once and cropping per window yields bit-identical waveforms. The
operations and their order are unchanged.

Because "bit-identical" is a claim and not a fact until measured, --control
embeds the same spans BOTH ways and compares. If they diverge, every score built
on this module would describe a pipeline that does not exist - the exact failure
where a fixture replay used a matcher the live path never calls. So the control
is not optional decoration; import-time users should run it first.

Usage:
    venv/bin/python3 s11_embed_fast.py --control debate_audio/event_20_*.wav
    venv/bin/python3 s11_embed_fast.py --control <wav> --spans 12 --dur 6.0
"""

import argparse
import os
import sys

MAX_COS = 1e-6      # cosine distance between the two embeddings
MAX_ABS = 1e-4      # largest per-dimension absolute difference
MIN_SEGMENT = 0.5   # extract_embedding returns None below this


class FastEmbedder:
    """Load an audio file once; embed arbitrary [start, end) spans from memory."""

    def __init__(self, path, verbose=True):
        import soundfile as sf
        import torch
        self._torch = torch
        if not os.path.exists(path):
            raise SystemExit("ABORT: %s not found" % path)
        data, sr = sf.read(path, dtype="float32")
        if data.ndim > 1:                      # same mono mix as extract_embedding
            data = data.mean(axis=1)
        if sr != 16000:                        # same resample, applied once
            import torchaudio
            wav = torch.tensor(data).unsqueeze(0)
            wav = torchaudio.transforms.Resample(sr, 16000)(wav)
            sr = 16000
        else:
            wav = torch.tensor(data).unsqueeze(0)
        self.waveform = wav
        self.sample_rate = sr
        self.duration = wav.shape[1] / float(sr)
        self.calls = 0
        if verbose:
            print("loaded %s: %.1f s at %d Hz, %d samples in memory"
                  % (os.path.basename(path), self.duration, sr, wav.shape[1]))

    def embed(self, start_sec, end_sec):
        """Mirrors extract_embedding's crop, minimum-length rule and encode."""
        import voice_verify
        a = max(0, int(start_sec * self.sample_rate))
        b = min(self.waveform.shape[1], int(end_sec * self.sample_rate))
        if b <= a:
            return None
        seg = self.waveform[:, a:b]
        if seg.shape[1] < int(MIN_SEGMENT * self.sample_rate):
            return None
        model = voice_verify.get_model()
        self.calls += 1
        with self._torch.no_grad():
            return model.encode_batch(seg).squeeze().numpy()


def control(path, n_spans, dur):
    """Embed the same spans both ways. Refuse to certify on any divergence."""
    import numpy as np
    import voice_verify

    fast = FastEmbedder(path)
    if fast.duration < dur * 2:
        raise SystemExit("ABORT: %s is only %.1f s" % (path, fast.duration))

    step = (fast.duration - dur) / float(n_spans + 1)
    print("\n%-10s %-10s %12s %12s  %s" % ("start", "dur", "cos_dist", "max_abs", ""))
    worst_cos, worst_abs, checked, problems = 0.0, 0.0, 0, []

    for i in range(1, n_spans + 1):
        s = round(step * i, 2)
        e = round(s + dur, 2)
        slow = voice_verify.extract_embedding(path, s, e)
        quick = fast.embed(s, e)
        if slow is None and quick is None:
            print("%-10.2f %-10.2f %12s %12s  both None (agreed)" % (s, dur, "-", "-"))
            continue
        if slow is None or quick is None:
            problems.append("span %.2f: one path returned None, the other did not" % s)
            print("%-10.2f %-10.2f %12s %12s  MISMATCH" % (s, dur, "-", "-"))
            continue
        cos = float(voice_verify.cosine_distance(slow, quick))
        mab = float(np.max(np.abs(np.asarray(slow) - np.asarray(quick))))
        ok = cos <= MAX_COS and mab <= MAX_ABS
        worst_cos, worst_abs, checked = max(worst_cos, cos), max(worst_abs, mab), checked + 1
        if not ok:
            problems.append("span %.2f: cos %.3e, max_abs %.3e" % (s, cos, mab))
        print("%-10.2f %-10.2f %12.3e %12.3e  %s" % (s, dur, cos, mab, "OK" if ok else "DIVERGES"))

    print("\n== CONTROL ==")
    print("  spans compared      %d" % checked)
    print("  worst cosine dist   %.3e  (limit %.0e)" % (worst_cos, MAX_COS))
    print("  worst abs diff      %.3e  (limit %.0e)" % (worst_abs, MAX_ABS))
    if problems or not checked:
        print("\n  FAILED - the fast path does NOT reproduce extract_embedding.")
        for p in problems[:10]:
            print("    %s" % p)
        print("  Do not score anything with this module until it agrees.")
        return 1
    print("\n  PASS - the fast path reproduces extract_embedding within tolerance.")
    print("  Safe to use for rolling-window scoring, and this is also the S11-4 fix.")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("audio")
    ap.add_argument("--control", action="store_true")
    ap.add_argument("--spans", type=int, default=8)
    ap.add_argument("--dur", type=float, default=5.0)
    args = ap.parse_args()
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    if not args.control:
        print("nothing to do without --control; this module is imported by the scorer")
        return 0
    return control(args.audio, args.spans, args.dur)


if __name__ == "__main__":
    sys.exit(main())
