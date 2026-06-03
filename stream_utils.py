"""
stream_utils.py — Shared YouTube stream resolution for debate pipeline.

Resolution strategy (in order):
1. YouTube Data API v3 (YOUTUBE_API_KEY env var) — official, no bot detection
2. yt_dlp fallback — for non-YouTube URLs or if API key not set
"""
import os
import re
import urllib.request
import urllib.parse
import json
import yt_dlp

class PreLiveError(Exception):
    """Raised when YouTube reports the stream hasn't started yet."""
    pass

def _extract_video_id(youtube_url):
    """Extract YouTube video ID from a URL."""
    patterns = [
        r'(?:v=|/v/|youtu\.be/|/embed/|/shorts/)([a-zA-Z0-9_-]{11})',
    ]
    for pat in patterns:
        m = re.search(pat, youtube_url)
        if m:
            return m.group(1)
    return None

def _check_live_via_api(video_id, api_key):
    """
    Use YouTube Data API v3 to verify a stream is live before yt_dlp resolution.
    Returns True if confirmed live.
    Raises PreLiveError if scheduled but not yet live.
    Raises Exception on API error (caller falls back to yt_dlp).
    """
    params = urllib.parse.urlencode({
        'part': 'snippet,liveStreamingDetails',
        'id': video_id,
        'key': api_key,
    })
    url = f'https://www.googleapis.com/youtube/v3/videos?{params}'
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            data = json.loads(resp.read().decode('utf-8'))
    except Exception as e:
        raise Exception(f"YouTube Data API request failed: {e}")

    items = data.get('items', [])
    if not items:
        raise Exception(f"YouTube Data API: video {video_id} not found")

    item = items[0]
    snippet = item.get('snippet', {})
    live_details = item.get('liveStreamingDetails', {})
    live_broadcast_content = snippet.get('liveBroadcastContent', 'none')

    if live_broadcast_content == 'upcoming':
        scheduled = live_details.get('scheduledStartTime', 'unknown')
        raise PreLiveError(f"Stream not yet live (scheduled: {scheduled})")

    if live_broadcast_content != 'live':
        raise Exception(f"Video {video_id} is not a live stream (status: {live_broadcast_content})")

    return True

def resolve_stream_url(youtube_url):
    """
    Resolve a YouTube URL to a direct HLS/audio stream URL.
    Returns the resolved stream URL string.
    Raises PreLiveError if the stream exists but has not started yet.
    Raises Exception for all other failures.

    Strategy:
    1. If YOUTUBE_API_KEY set: verify stream is live via API first (avoids
       bot detection on yt_dlp resolution attempts against pre-live streams).
    2. Resolve actual HLS URL via yt_dlp regardless (API gives metadata only).
    3. If API key not set: pure yt_dlp (original behavior).
    """
    # Already a direct stream URL — pass through
    if (youtube_url.startswith('https://manifest.googlevideo.com')
            or youtube_url.startswith('https://rr')
            or youtube_url.endswith('.m3u8')):
        return youtube_url

    api_key = os.environ.get('YOUTUBE_API_KEY')
    video_id = _extract_video_id(youtube_url)

    # Step 1: API live-status check (prevents thrashing yt_dlp on pre-live streams)
    if api_key and video_id:
        try:
            print(f"  [YouTube API] Verifying live status for {video_id}...")
            _check_live_via_api(video_id, api_key)
            print(f"  [YouTube API] Stream confirmed live")
        except PreLiveError:
            raise
        except Exception as e:
            print(f"  [YouTube API] Check failed ({e}) — falling back to yt_dlp")

    # Step 2: Resolve HLS URL via yt_dlp
    ydl_opts = {
        'format': 'bestaudio/best',
        'quiet': True,
        'no_warnings': True,
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(youtube_url, download=False)
            if info.get('live_status') == 'is_upcoming':
                scheduled = info.get('release_timestamp') or info.get('release_date', 'unknown')
                raise PreLiveError(f"Stream not yet live (scheduled: {scheduled})")
            url = info.get('url')
            if not url:
                formats = info.get('formats', [])
                if formats:
                    url = formats[-1].get('url')
            if not url:
                raise ValueError("yt_dlp returned no stream URL")
            return url
    except PreLiveError:
        raise
    except yt_dlp.utils.DownloadError as e:
        msg = str(e).lower()
        if 'will begin' in msg or 'not started' in msg or 'upcoming' in msg or 'premiere' in msg:
            raise PreLiveError(f"Stream not yet live: {e}")
        raise
