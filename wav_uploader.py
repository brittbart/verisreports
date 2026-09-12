#!/usr/bin/env python3
"""wav_uploader.py - keep a capture's wav(s) in R2 while the capture runs, and leave a correct file when it ends.
For every debate_audio/event_<id>_*.wav that appears after start: an R2 multipart upload grows by one part per --interval
(parts >= 5 MB, R2's minimum; at 31 KB/s that is ~9 MB every 5 min). When --watch-pid is gone (the watchdog exited) the
finished file is re-uploaded whole to the same key (ffmpeg finalises the WAV header only on close), the multipart is
completed or aborted, and the keys are written to capture_status.r2_keys. R2 config from R2_BUCKET / R2_ENDPOINT /
R2_ACCESS_KEY_ID / R2_SECRET_ACCESS_KEY.
  venv/bin/python3 wav_uploader.py --event-id 26 --watch-pid 12345          # sidecar, started by capture_worker
  venv/bin/python3 wav_uploader.py --event-id 24 --once --wav debate_audio/event_24_x.wav   # upload one finished file now
  venv/bin/python3 wav_uploader.py --list captures/event_24/ | --delete captures/event_24/x.wav"""
import argparse, glob, json, os, sys, time, datetime
import boto3, botocore
HERE = os.path.dirname(os.path.abspath(__file__))
PART_MIN = 5 * 1024 * 1024
def s3():
    return boto3.client('s3', endpoint_url=os.environ['R2_ENDPOINT'], aws_access_key_id=os.environ['R2_ACCESS_KEY_ID'],
                        aws_secret_access_key=os.environ['R2_SECRET_ACCESS_KEY'], region_name='auto', config=botocore.config.Config(signature_version='s3v4'))
def log(m): print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] uploader: {m}", flush=True)
def key_for(eid, path): return f"captures/event_{eid}/{os.path.basename(path)}"
def pid_alive(pid):
    try: os.kill(pid, 0); return True
    except ProcessLookupError: return False
    except PermissionError: return True
def record_keys(eid, keys):
    try:
        import psycopg2
        conn = psycopg2.connect(dbname=os.environ['DB_NAME'], user=os.environ['DB_USER'], password=os.environ['DB_PASSWORD'], host=os.environ['DB_HOST'], port=os.environ['DB_PORT'], connect_timeout=10)
        cur = conn.cursor()
        cur.execute("ALTER TABLE capture_status ADD COLUMN IF NOT EXISTS r2_keys JSONB")
        cur.execute("INSERT INTO capture_status (event_id, state, r2_keys) VALUES (%s, 'exited', %s) ON CONFLICT (event_id) DO UPDATE SET r2_keys = EXCLUDED.r2_keys, updated_at = NOW()", (eid, json.dumps(keys)))
        conn.commit(); conn.close(); log(f'capture_status.r2_keys = {keys}')
    except Exception as e:
        log(f'could not record keys in capture_status: {e!r}')
def upload_whole(client, bucket, path, key):
    t0 = time.time(); client.upload_file(path, bucket, key); dt = time.time() - t0
    log(f'final upload {os.path.basename(path)} {os.path.getsize(path)/1e6:.1f} MB -> {key} in {dt:.1f}s')
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--event-id', type=int); ap.add_argument('--watch-pid', type=int); ap.add_argument('--interval', type=int, default=300)
    ap.add_argument('--once', action='store_true'); ap.add_argument('--wav'); ap.add_argument('--list'); ap.add_argument('--delete')
    a = ap.parse_args()
    bucket = os.environ['R2_BUCKET']; client = s3()
    if a.list is not None:
        r = client.list_objects_v2(Bucket=bucket, Prefix=a.list)
        for o in r.get('Contents', []): print(o['Key'], o['Size'], o['LastModified'])
        print(f"{r.get('KeyCount', 0)} object(s)"); return
    if a.delete:
        client.delete_object(Bucket=bucket, Key=a.delete); print('deleted', a.delete); return
    if a.event_id is None: sys.exit('--event-id required')
    if a.once:
        if not a.wav: sys.exit('--once needs --wav')
        k = key_for(a.event_id, a.wav); upload_whole(client, bucket, a.wav, k); record_keys(a.event_id, [k]); return
    if a.watch_pid is None: sys.exit('--watch-pid required (or --once --wav)')
    pattern = os.path.join(HERE, 'debate_audio', f'event_{a.event_id}_*.wav')
    seen_before = set(glob.glob(pattern))
    mp = {}   # path -> {'key','upload_id','parts':[],'offset':int}
    log(f'event {a.event_id}: watching pid {a.watch_pid}, pattern {os.path.relpath(pattern, HERE)}, upload every {a.interval}s, pid check every 10s')
    last_upload = 0.0
    while True:
        alive = pid_alive(a.watch_pid)
        if alive and time.time() - last_upload < a.interval:
            time.sleep(10); continue
        last_upload = time.time()
        for path in sorted(set(glob.glob(pattern)) - seen_before):
            if path not in mp:
                k = key_for(a.event_id, path)
                r = client.create_multipart_upload(Bucket=bucket, Key=k, ContentType='audio/wav')
                mp[path] = {'key': k, 'upload_id': r['UploadId'], 'parts': [], 'offset': 0}
                log(f'multipart started for {os.path.basename(path)} -> {k}')
            st = mp[path]; size = os.path.getsize(path); pending = size - st['offset']
            if pending >= PART_MIN or (not alive and pending > 0):
                with open(path, 'rb') as f:
                    f.seek(st['offset']); data = f.read(pending)
                n = len(st['parts']) + 1
                r = client.upload_part(Bucket=bucket, Key=st['key'], UploadId=st['upload_id'], PartNumber=n, Body=data)
                st['parts'].append({'PartNumber': n, 'ETag': r['ETag']}); st['offset'] = size
                log(f'part {n} {len(data)/1e6:.1f} MB for {os.path.basename(path)} (total {size/1e6:.1f} MB)')
        if not alive:
            keys = []
            for path, st in mp.items():
                try:
                    if st['parts']:
                        client.complete_multipart_upload(Bucket=bucket, Key=st['key'], UploadId=st['upload_id'], MultipartUpload={'Parts': st['parts']})
                    else:
                        client.abort_multipart_upload(Bucket=bucket, Key=st['key'], UploadId=st['upload_id'])
                except Exception as e:
                    log(f'multipart finish failed for {st["key"]}: {e!r}')
                time.sleep(2)   # let ffmpeg finish the header rewrite
                upload_whole(client, bucket, path, st['key']); keys.append(st['key'])
            record_keys(a.event_id, keys); log('done'); return
if __name__ == '__main__':
    main()
