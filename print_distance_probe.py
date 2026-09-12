#!/usr/bin/env python3
"""print_distance_probe.py - embed named windows of a capture wav with voice_verify's model and report cosine distance to
enrolled prints. Read-only. Usage: venv/bin/python3 print_distance_probe.py <wav> <print_ids csv> <label:start:end> ...
  e.g. ... debate_audio/event_27_x.wav 185,253 turek1:172:222 turek2:559:585 wahls:282:340"""
import json, sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import voice_verify as vv
wav = sys.argv[1]; ids = [int(x) for x in sys.argv[2].split(',')]
prints = {i: json.load(open(f'speaker_embeddings/speaker_{i}.json')) for i in ids}
for spec in sys.argv[3:]:
    label, a, b = spec.split(':'); a, b = float(a), float(b)
    emb = vv.extract_embedding(wav, start_sec=a, end_sec=b)
    dists = {f"{i} {prints[i]['speaker_name']}": round(vv.cosine_distance(emb, prints[i]['embedding']), 3) for i in ids}
    print(f'{label:10s} {a:6.0f}-{b:4.0f}s  {dists}')
