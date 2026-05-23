"""
notification_jobs.py — Verum Signal Mobile Push Notification Jobs
=================================================================
Handles all push notification logic for the mobile app.

Three job types:
  1. debate_start_alerts   — fires T-15min and T+0 for followed events
  2. verdict_alerts        — fires during live debates for followed speakers
                             (max 5 per user per debate, rate limited)
  3. daily_digest          — nightly job for opted-in users

Push delivery is handled by Expo Notifications (managed service).
The _send_push() function is the single integration point — wire in
the Expo Push API once the Expo account and push tokens are configured.

Usage (called from Railway cron or inline):
    from notification_jobs import (
        run_debate_start_alerts,
        run_verdict_alerts,
        run_daily_digest,
    )
    run_debate_start_alerts(get_db)
    run_verdict_alerts(get_db)
    run_daily_digest(get_db)

Quiet hours: respected per user timezone and user_notification_prefs.
Rate limiting: verdict_alerts capped at 5 per user per debate.
Dedup: notification_log used to prevent duplicate sends.
"""

import os
import json
import traceback
from datetime import datetime, timezone, timedelta, time as dtime


# ── Push delivery stub ─────────────────────────────────────────────────────
# Wire in Expo Push API here once Expo account is configured.
# Docs: https://docs.expo.dev/push-notifications/sending-notifications/
#
# Replace _send_push() body with:
#   import httpx
#   response = httpx.post(
#       "https://exp.host/--/api/v2/push/send",
#       json={"to": push_token, "title": title, "body": body, "data": data},
#       headers={"Accept": "application/json", "Content-Type": "application/json"},
#   )
#   return response.status_code == 200

PUSH_ENABLED = os.environ.get('EXPO_PUSH_ENABLED', 'false').lower() == 'true'

def _send_push(push_token: str, title: str, body: str, data: dict = None) -> bool:
    """
    Send a push notification via Expo Push API.
    Currently stubbed — logs the notification that would be sent.
    
    Returns True if sent successfully, False otherwise.
    """
    if not PUSH_ENABLED:
        print(f"  [PUSH STUB] token={push_token[:20]}... | {title} | {body}")
        return True  # stub always succeeds

    # TODO: wire in Expo Push API
    # import httpx
    # response = httpx.post(
    #     "https://exp.host/--/api/v2/push/send",
    #     json={
    #         "to": push_token,
    #         "title": title,
    #         "body": body,
    #         "data": data or {},
    #         "sound": "default",
    #         "channelId": "default",
    #     },
    #     headers={
    #         "Accept": "application/json",
    #         "Content-Type": "application/json",
    #     },
    #     timeout=10,
    # )
    # return response.status_code == 200
    print(f"  [PUSH] EXPO_PUSH_ENABLED=true but Expo not yet wired")
    return False


# ── Quiet hours check ──────────────────────────────────────────────────────

def _is_quiet_hours(user_prefs: dict) -> bool:
    """
    Check if current time falls within user's quiet hours.
    Returns True if notifications should be suppressed.
    """
    try:
        import pytz
        tz_name = user_prefs.get('timezone', 'America/Denver')
        tz = pytz.timezone(tz_name)
        local_now = datetime.now(tz).time()

        quiet_start = user_prefs.get('quiet_hours_start')
        quiet_end = user_prefs.get('quiet_hours_end')

        if not quiet_start or not quiet_end:
            return False

        # Handle overnight quiet hours (e.g. 22:00 - 07:00)
        if quiet_start > quiet_end:
            return local_now >= quiet_start or local_now <= quiet_end
        else:
            return quiet_start <= local_now <= quiet_end
    except Exception:
        return False  # if tz fails, don't suppress


# ── Notification log helpers ───────────────────────────────────────────────

def _already_sent(cur, user_id: int, notification_type: str,
                  related_event_id: int = None,
                  related_article_id: int = None,
                  within_hours: int = 24) -> bool:
    """
    Check notification_log to prevent duplicate sends.
    Returns True if this notification type was already sent to this user
    within the specified window.
    """
    cur.execute("""
        SELECT id FROM notification_log
        WHERE user_id = %s
          AND notification_type = %s
          AND sent_at > NOW() - INTERVAL '%s hours'
          AND (related_event_id = %s OR %s IS NULL)
          AND (related_article_id = %s OR %s IS NULL)
        LIMIT 1
    """, (
        user_id, notification_type, within_hours,
        related_event_id, related_event_id,
        related_article_id, related_article_id,
    ))
    return cur.fetchone() is not None


def _log_notification(cur, user_id: int, device_id: int,
                      notification_type: str, payload: dict,
                      related_event_id: int = None,
                      related_article_id: int = None):
    """Write a sent notification to the log."""
    cur.execute("""
        INSERT INTO notification_log
            (user_id, device_id, notification_type, payload,
             sent_at, related_event_id, related_article_id)
        VALUES (%s, %s, %s, %s, NOW(), %s, %s)
    """, (
        user_id, device_id, notification_type,
        json.dumps(payload),
        related_event_id, related_article_id,
    ))


def _get_user_devices(cur, user_id: int) -> list:
    """Get all push-enabled devices for a user."""
    cur.execute("""
        SELECT id, push_token, platform, timezone
        FROM user_devices
        WHERE user_id = %s
          AND push_enabled = TRUE
          AND push_token IS NOT NULL
        ORDER BY last_seen_at DESC
    """, (user_id,))
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def _get_user_prefs(cur, user_id: int) -> dict:
    """Get notification preferences for a user."""
    cur.execute("""
        SELECT debate_start_alerts, debate_verdict_alerts,
               daily_digest, quiet_hours_start, quiet_hours_end, timezone
        FROM user_notification_prefs
        WHERE user_id = %s
    """, (user_id,))
    row = cur.fetchone()
    if not row:
        # Return defaults
        return {
            'debate_start_alerts': True,
            'debate_verdict_alerts': False,
            'daily_digest': False,
            'quiet_hours_start': dtime(22, 0),
            'quiet_hours_end': dtime(7, 0),
            'timezone': 'America/Denver',
        }
    cols = [d[0] for d in cur.description]
    return dict(zip(cols, row))


# ── Job 1: Debate start alerts ─────────────────────────────────────────────

def run_debate_start_alerts(get_db):
    """
    Fire T-15min and T+0 push notifications for upcoming debates.
    
    Targets users who:
      - Follow the event (user_follows_event)
      - Have debate_start_alerts = TRUE in notification prefs
      - Have push-enabled devices
      - Haven't already received this alert
      - Are not in quiet hours
    
    Called every 5 minutes by Railway cron (or inline).
    """
    db = get_db()
    cur = db.cursor()
    sent_count = 0

    try:
        # Find events starting in next 20 minutes (catch T-15 window)
        # or started in last 5 minutes (catch T+0 window)
        cur.execute("""
            SELECT e.id, e.slug, e.event_name, e.event_date, e.start_time, e.timezone
            FROM events e
            WHERE e.event_date = CURRENT_DATE
              AND e.start_time IS NOT NULL
        """)
        events = cur.fetchall()
        event_cols = [d[0] for d in cur.description]

        now_utc = datetime.now(timezone.utc)

        for event_row in events:
            event = dict(zip(event_cols, event_row))
            event_id = event['id']

            # Reconstruct event datetime (events store date + time separately)
            try:
                import pytz
                tz_name = event.get('timezone') or 'America/Denver'
                tz = pytz.timezone(tz_name)
                event_dt = datetime.combine(event['event_date'], event['start_time'])
                event_dt = tz.localize(event_dt).astimezone(timezone.utc)
            except Exception:
                continue

            minutes_until = (event_dt - now_utc).total_seconds() / 60
            minutes_since = (now_utc - event_dt).total_seconds() / 60

            # T-15 window: 13-17 minutes before
            is_t_minus_15 = 13 <= minutes_until <= 17
            # T+0 window: 0-5 minutes after start
            is_t_zero = 0 <= minutes_since <= 5

            if not is_t_minus_15 and not is_t_zero:
                continue

            notif_type = 'debate_starting_soon' if is_t_minus_15 else 'debate_live'
            title = f"{event['event_name']}"
            body = (
                f"Starts in 15 minutes. Tap to follow along."
                if is_t_minus_15 else
                f"The debate is live now."
            )
            deep_link = f"verumsignal://debate/{event['slug']}"

            # Find all users following this event
            cur.execute("""
                SELECT ufe.user_id
                FROM user_follows_event ufe
                JOIN users u ON u.id = ufe.user_id
                WHERE ufe.event_id = %s
                  AND u.deleted_at IS NULL
            """, (event_id,))
            followers = [r[0] for r in cur.fetchall()]

            for user_id in followers:
                # Dedup check
                if _already_sent(cur, user_id, notif_type,
                                  related_event_id=event_id, within_hours=2):
                    continue

                prefs = _get_user_prefs(cur, user_id)
                if not prefs.get('debate_start_alerts', True):
                    continue
                if _is_quiet_hours(prefs):
                    continue

                devices = _get_user_devices(cur, user_id)
                for device in devices:
                    payload = {
                        'title': title,
                        'body': body,
                        'deep_link': deep_link,
                        'event_id': event_id,
                        'event_slug': event['slug'],
                        'notification_type': notif_type,
                    }
                    success = _send_push(
                        device['push_token'], title, body,
                        data={'deep_link': deep_link, 'event_slug': event['slug']}
                    )
                    if success:
                        _log_notification(
                            cur, user_id, device['id'],
                            notif_type, payload,
                            related_event_id=event_id
                        )
                        sent_count += 1

        db.commit()
        print(f"[notification_jobs] debate_start_alerts: {sent_count} sent")
        return sent_count

    except Exception as e:
        print(f"[notification_jobs] debate_start_alerts error: {e}")
        traceback.print_exc()
        try:
            db.rollback()
        except Exception:
            pass
        return 0
    finally:
        cur.close()
        db.close()


# ── Job 2: Verdict alerts (during live debates) ────────────────────────────

VERDICT_ALERT_CAP = 5  # max notifications per user per debate

def run_verdict_alerts(get_db):
    """
    Fire push notifications for new verdicts during live debates.
    
    Targets users who:
      - Follow a speaker in a currently-live debate
      - Have debate_verdict_alerts = TRUE
      - Haven't hit the per-debate cap (5 notifications)
      - Have push-enabled devices
      - Are not in quiet hours
    
    "Live" = event_date is today and start_time was within last 3 hours.
    Called every 5 minutes during active debates.
    """
    db = get_db()
    cur = db.cursor()
    sent_count = 0

    try:
        # Find today's events that are likely live (started in last 3 hours)
        cur.execute("""
            SELECT e.id, e.slug, e.event_name, e.event_date, e.start_time, e.timezone
            FROM events e
            WHERE e.event_date = CURRENT_DATE
              AND e.start_time IS NOT NULL
        """)
        events = cur.fetchall()
        event_cols = [d[0] for d in cur.description]

        now_utc = datetime.now(timezone.utc)

        for event_row in events:
            event = dict(zip(event_cols, event_row))
            event_id = event['id']

            try:
                import pytz
                tz_name = event.get('timezone') or 'America/Denver'
                tz = pytz.timezone(tz_name)
                event_dt = datetime.combine(event['event_date'], event['start_time'])
                event_dt = tz.localize(event_dt).astimezone(timezone.utc)
            except Exception:
                continue

            minutes_since_start = (now_utc - event_dt).total_seconds() / 60
            # Only fire for events that started in last 3 hours
            if not (0 <= minutes_since_start <= 180):
                continue

            # Get new claims from last 6 minutes (slight overlap to catch missed)
            cur.execute("""
                SELECT c.id, c.claim_text, c.verdict, c.speaker_id,
                       c.verdict_summary, s.name AS speaker_name
                FROM claims c
                LEFT JOIN speakers s ON s.id = c.speaker_id
                WHERE c.event_id = %s
                  AND c.verdict IS NOT NULL
                  AND c.first_seen > NOW() - INTERVAL '6 minutes'
                  AND c.speaker_id IS NOT NULL
                ORDER BY c.first_seen ASC
            """, (event_id,))
            new_claims = cur.fetchall()
            claim_cols = [d[0] for d in cur.description]

            if not new_claims:
                continue

            for claim_row in new_claims:
                claim = dict(zip(claim_cols, claim_row))
                speaker_id = claim['speaker_id']

                # Find users following this speaker who want verdict alerts
                cur.execute("""
                    SELECT ufs.user_id
                    FROM user_follows_speaker ufs
                    JOIN users u ON u.id = ufs.user_id
                    WHERE ufs.speaker_id = %s
                      AND u.deleted_at IS NULL
                """, (speaker_id,))
                followers = [r[0] for r in cur.fetchall()]

                for user_id in followers:
                    # Check per-debate cap
                    cur.execute("""
                        SELECT COUNT(*) FROM notification_log
                        WHERE user_id = %s
                          AND notification_type = 'debate_verdict'
                          AND related_event_id = %s
                          AND sent_at > NOW() - INTERVAL '24 hours'
                    """, (user_id, event_id))
                    cap_count = cur.fetchone()[0]
                    if cap_count >= VERDICT_ALERT_CAP:
                        continue

                    # Dedup: don't send same claim twice
                    if _already_sent(cur, user_id, 'debate_verdict',
                                     related_event_id=event_id, within_hours=1):
                        continue

                    prefs = _get_user_prefs(cur, user_id)
                    if not prefs.get('debate_verdict_alerts', False):
                        continue
                    if _is_quiet_hours(prefs):
                        continue

                    verdict_label = (claim['verdict'] or 'analyzed').replace('_', ' ').title()
                    title = f"{claim['speaker_name'] or 'A speaker'} made a claim"
                    body = f"Verum Signal scored it: {verdict_label}"
                    deep_link = f"verumsignal://debate/{event['slug']}"

                    devices = _get_user_devices(cur, user_id)
                    for device in devices:
                        payload = {
                            'title': title,
                            'body': body,
                            'deep_link': deep_link,
                            'claim_id': claim['id'],
                            'event_id': event_id,
                            'event_slug': event['slug'],
                            'notification_type': 'debate_verdict',
                        }
                        success = _send_push(
                            device['push_token'], title, body,
                            data={
                                'deep_link': deep_link,
                                'event_slug': event['slug'],
                                'claim_id': claim['id'],
                            }
                        )
                        if success:
                            _log_notification(
                                cur, user_id, device['id'],
                                'debate_verdict', payload,
                                related_event_id=event_id
                            )
                            sent_count += 1

        db.commit()
        print(f"[notification_jobs] verdict_alerts: {sent_count} sent")
        return sent_count

    except Exception as e:
        print(f"[notification_jobs] verdict_alerts error: {e}")
        traceback.print_exc()
        try:
            db.rollback()
        except Exception:
            pass
        return 0
    finally:
        cur.close()
        db.close()


# ── Job 3: Daily digest ────────────────────────────────────────────────────

def run_daily_digest(get_db):
    """
    Nightly digest for users opted into daily_digest = TRUE.
    Sends a summary of new articles from followed outlets.
    
    Fires once per day per user (dedup via notification_log).
    Respects quiet hours — if user is in quiet hours at digest time,
    skip for today (don't queue for later; digest fires nightly at ~8am local).
    """
    db = get_db()
    cur = db.cursor()
    sent_count = 0

    try:
        # Find users opted into daily digest with push-enabled devices
        cur.execute("""
            SELECT DISTINCT u.id
            FROM users u
            JOIN user_notification_prefs p ON p.user_id = u.id
            JOIN user_devices d ON d.user_id = u.id
            WHERE p.daily_digest = TRUE
              AND d.push_enabled = TRUE
              AND d.push_token IS NOT NULL
              AND u.deleted_at IS NULL
        """)
        user_ids = [r[0] for r in cur.fetchall()]

        for user_id in user_ids:
            # Dedup: only one digest per day
            if _already_sent(cur, user_id, 'daily_digest', within_hours=20):
                continue

            prefs = _get_user_prefs(cur, user_id)
            if _is_quiet_hours(prefs):
                continue

            # Count new articles from followed outlets in last 24h
            cur.execute("""
                SELECT COUNT(DISTINCT a.id)
                FROM articles a
                JOIN user_follows_outlet ufo ON ufo.outlet_domain = a.source_name
                WHERE ufo.user_id = %s
                  AND a.published_at > NOW() - INTERVAL '24 hours'
                  AND a.excluded_from_extraction = FALSE
            """, (user_id,))
            article_count = cur.fetchone()[0]

            if article_count == 0:
                continue

            title = "Your daily Verum Signal digest"
            body = (
                f"{article_count} new article{'s' if article_count != 1 else ''} "
                f"from outlets you follow."
            )
            deep_link = "verumsignal://articles?filter=following"

            devices = _get_user_devices(cur, user_id)
            for device in devices:
                payload = {
                    'title': title,
                    'body': body,
                    'deep_link': deep_link,
                    'article_count': article_count,
                    'notification_type': 'daily_digest',
                }
                success = _send_push(
                    device['push_token'], title, body,
                    data={'deep_link': deep_link}
                )
                if success:
                    _log_notification(
                        cur, user_id, device['id'],
                        'daily_digest', payload
                    )
                    sent_count += 1
                    break  # one notification per user (first active device)

        db.commit()
        print(f"[notification_jobs] daily_digest: {sent_count} sent")
        return sent_count

    except Exception as e:
        print(f"[notification_jobs] daily_digest error: {e}")
        traceback.print_exc()
        try:
            db.rollback()
        except Exception:
            pass
        return 0
    finally:
        cur.close()
        db.close()


# ── Standalone runner ──────────────────────────────────────────────────────

if __name__ == '__main__':
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from api import get_db

    job = sys.argv[1] if len(sys.argv) > 1 else 'all'

    if job in ('debate_start', 'all'):
        print("\n--- debate_start_alerts ---")
        run_debate_start_alerts(get_db)

    if job in ('verdict', 'all'):
        print("\n--- verdict_alerts ---")
        run_verdict_alerts(get_db)

    if job in ('digest', 'all'):
        print("\n--- daily_digest ---")
        run_daily_digest(get_db)
