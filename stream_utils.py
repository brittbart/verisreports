"""
stream_utils.py — Shared YouTube stream resolution for debate pipeline.

Uses the Python yt_dlp library (not CLI) which handles YouTube's JS challenge
natively without requiring deno. Safe to run on Railway.
"""
import yt_dlp


class PreLiveError(Exception):
    """Raised when YouTube reports the stream hasn't started yet."""
    pass


def resolve_stream_url(youtube_url):
    """
    Resolve a YouTube URL to a direct HLS/audio stream URL.

    Returns the resolved stream URL string.
    Raises PreLiveError if the stream exists but hasn't started yet.
    Raises Exception for all other failures.
    """
    # Already a direct stream URL — pass through
    if (youtube_url.startswith('https://manifest.googlevideo.com')
            or youtube_url.endswith('.m3u8')):
        return youtube_url

    ydl_opts = {
        'format': 'bestaudio/best',
        'quiet': True,
        'no_warnings': True,
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(youtube_url, download=False)

            # Check for pre-live state
            if info.get('live_status') == 'is_upcoming':
                scheduled = info.get('release_timestamp') or info.get('release_date', 'unknown')
                raise PreLiveError(f"Stream not yet live (scheduled: {scheduled})")

            url = info.get('url')
            if not url:
                # Fallback: check formats list
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
