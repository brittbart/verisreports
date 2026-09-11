#!/usr/bin/env python3
"""watchdog_debate_capture.py - runs debate_stream.py in live mode and
automatically restarts it if it crashes (non-zero exit), rather than
letting a single dropped connection end capture for the rest of the
debate. Sequential and BLOCKING by construction (subprocess.run waits
for full exit before the next iteration) -- this is what makes it safe
without a DB-level concurrent-capture lock, which was checked and does
NOT exist in the current debate_stream.py despite being described as
shipped in an earlier changelog entry. Two processes can never run at
once because this wrapper never starts a new one until the previous one
has completely terminated.

Exit code 2 is debate_stream.py's own pre-live signal (its source
comment: "exit code 2 = pre-live (not a real failure)") -- distinguished
from a genuine crash. Pre-live retries use a long, patient interval and
NEVER count against MAX_RESTARTS, since waiting for a scheduled stream
to go live can legitimately take hours and is not a failure. Any other
non-zero exit is treated as a genuine crash: short backoff, counted
against MAX_RESTARTS.

Built same-day as the actual Sept 9 capture after a real crash was
observed during the LD3 re-test (Deepgram websocket SSLEOFError,
"no close frame received or sent", no retry logic in debate_stream.py
itself). A restart after a crash is expected to resume correctly since
next_utterance_order() already resumes from existing utterances rather
than starting over -- confirmed in source, not assumed.

Known trade-off: each restart (from a genuine crash, not a pre-live
retry) starts a new WAV file (timestamped), so a mid-debate crash means
two separate audio files rather than one continuous recording, plus a
real content gap during the crash+restart window (typically well under
a minute). Accepted deliberately against the alternative of losing
capture for the rest of the debate.

Usage: identical arguments to debate_stream.py itself, passed straight
through, e.g.
  python3 watchdog_debate_capture.py --mode live --url <url> \\
      --event-slug <slug> --speaker-order <order>
Do not pass --dry-run for the real event unless a dry run repeated on
every restart is actually what's wanted."""
import sys
import os
import glob
import signal
import subprocess
import time
from datetime import datetime

MAX_RESTARTS = 20
CRASH_BACKOFF_SECONDS = 5
PRELIVE_RETRY_SECONDS = 30
PRELIVE_EXIT_CODE = 2
LIVENESS_POLL_SECONDS = 10      # how often the child and its wav are checked
STALL_SECONDS = 90              # wav not growing this long, ffmpeg still alive -> child is frozen
STALL_KILL_GRACE_SECONDS = 15   # SIGTERM -> SIGKILL if the child does not exit
AUDIO_DIR = "debate_audio"


def log(msg):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"\n=== [{ts}] WATCHDOG: {msg} ===")


def _child_ffmpeg_alive(pid):
    """True if the child still has an ffmpeg process of its own. Once the
    child has closed ffmpeg deliberately (stream ended, sweep running) a
    frozen wav is expected and must not trigger a kill."""
    r = subprocess.run(["pgrep", "-P", str(pid), "-x", "ffmpeg"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return r.returncode == 0


def _find_child_wav(preexisting):
    """The wav this child is writing: newest AUDIO_DIR/event_*.wav that did NOT
    exist when the child started. A previous run's file is never a candidate,
    however recently it was written (Sept 11 rehearsal: an orphaned ffmpeg kept
    writing run 0's wav after a restart and the check adopted it, then killed a
    healthy run 1 when that file froze). None until the child's own wav exists."""
    cands = []
    for p in glob.glob(os.path.join(AUDIO_DIR, "event_*.wav")):
        if p in preexisting:
            continue
        try:
            m = os.stat(p).st_mtime
        except OSError:
            continue
        cands.append((m, p))
    return max(cands)[1] if cands else None


def run_child(args):
    """Start debate_stream.py (-u: child output must not sit in a pipe
    buffer when it is killed) and wait for it, polling wav growth. Returns
    the exit code, or 143-style negative/positive code after a stall kill."""
    started_at = time.time()
    preexisting = set(glob.glob(os.path.join(AUDIO_DIR, "event_*.wav")))  # never watch these
    proc = subprocess.Popen([sys.executable, "-u", "debate_stream.py"] + args)
    wav = None
    last_size = -1
    last_growth = started_at
    while True:
        try:
            return proc.wait(timeout=LIVENESS_POLL_SECONDS)
        except subprocess.TimeoutExpired:
            pass
        if wav is None:
            wav = _find_child_wav(preexisting)
            if wav is None:
                continue
            log(f"liveness: watching {wav}")
            last_growth = time.time()
        try:
            size = os.stat(wav).st_size
        except OSError:
            continue
        if size != last_size:
            last_size = size
            last_growth = time.time()
            continue
        stalled = time.time() - last_growth
        if stalled >= STALL_SECONDS and _child_ffmpeg_alive(proc.pid):
            log(f"liveness: {wav} has not grown for {int(stalled)}s with ffmpeg still running "
                f"-- child is frozen. Sending SIGTERM (pid {proc.pid}).")
            proc.send_signal(signal.SIGTERM)
            try:
                return proc.wait(timeout=STALL_KILL_GRACE_SECONDS)
            except subprocess.TimeoutExpired:
                log(f"liveness: child ignored SIGTERM for {STALL_KILL_GRACE_SECONDS}s. Sending SIGKILL.")
                proc.kill()
                return proc.wait()


def main():
    args = sys.argv[1:]
    if not args:
        sys.exit("usage: watchdog_debate_capture.py <same arguments as debate_stream.py>")
    crash_attempt = 0
    while True:
        log(f"starting debate_stream.py (crash-retry {crash_attempt}/{MAX_RESTARTS})")
        print(f"=== command: python3 debate_stream.py {' '.join(args)} ===\n")
        code = run_child(args)
        if code == 0:
            log("debate_stream.py exited cleanly (code 0). Not restarting.")
            return 0
        if code == PRELIVE_EXIT_CODE:
            log(f"debate_stream.py reports pre-live (exit code {code}) -- not a failure. "
                f"Waiting {PRELIVE_RETRY_SECONDS}s before checking again. "
                f"(This does not count against the {MAX_RESTARTS} crash-retry budget.)")
            time.sleep(PRELIVE_RETRY_SECONDS)
            continue
        crash_attempt += 1
        if crash_attempt > MAX_RESTARTS:
            log(f"hit MAX_RESTARTS ({MAX_RESTARTS}) genuine crashes without a clean exit. "
                f"Stopping automatic restarts. Manual intervention needed.")
            return 1
        log(f"debate_stream.py exited with code {code} -- treating as a genuine crash "
            f"(crash-retry {crash_attempt}/{MAX_RESTARTS}). Restarting in {CRASH_BACKOFF_SECONDS}s.")
        time.sleep(CRASH_BACKOFF_SECONDS)


if __name__ == "__main__":
    sys.exit(main())
