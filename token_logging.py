"""
token_logging.py — write Anthropic API token usage to Postgres.

One function: log_usage(stage, response, model).

Failure-safe: any exception during logging is caught and a warning is
printed. The caller's behavior is unaffected. Logging is best-effort.

Schema (created separately, see patch_20 SQL):

    CREATE TABLE token_usage (
        id BIGSERIAL PRIMARY KEY,
        timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        stage TEXT NOT NULL,
        model TEXT NOT NULL,
        input_tokens INTEGER NOT NULL,
        output_tokens INTEGER NOT NULL,
        cache_creation_input_tokens INTEGER DEFAULT 0,
        cache_read_input_tokens INTEGER DEFAULT 0,
        request_id TEXT
    );
    CREATE INDEX idx_token_usage_stage_ts ON token_usage (stage, timestamp DESC);

Cost analysis (do after 24-48h of data accumulates):

    SELECT
        stage,
        COUNT(*) AS calls,
        SUM(input_tokens) AS total_input,
        SUM(output_tokens) AS total_output,
        SUM(cache_read_input_tokens) AS total_cache_reads,
        -- Approximate cost in USD (sonnet 4 pricing as of May 2026)
        ROUND( (
            SUM(input_tokens) * 3.00 / 1000000 +
            SUM(output_tokens) * 15.00 / 1000000 +
            SUM(cache_creation_input_tokens) * 3.75 / 1000000 +
            SUM(cache_read_input_tokens) * 0.30 / 1000000
        )::numeric, 4) AS approx_usd
    FROM token_usage
    WHERE timestamp >= NOW() - INTERVAL '24 hours'
    GROUP BY stage
    ORDER BY approx_usd DESC;
"""
import os
import psycopg2
from dotenv import load_dotenv

if os.path.exists(".env"):
    load_dotenv(override=False)


def _connect():
    """Open a Postgres connection. Uses same env vars as the rest of the app."""
    return psycopg2.connect(
        dbname=os.environ.get("DB_NAME", "railway"),
        user=os.environ.get("DB_USER", "postgres"),
        password=os.environ.get(
            "DB_PASSWORD",
            # Same hardcoded fallback as api.py:33 for Railway Runtime V2.
            # Do NOT remove — required when env var stripping happens.
            "PXLJKUdf14OB8bq4dWgF2P0gCs4FjVP",
        ),
        host=os.environ.get("DB_HOST", "shinkansen.proxy.rlwy.net"),
        port=os.environ.get("DB_PORT", "35370"),
        connect_timeout=10,
        keepalives=1,
        keepalives_idle=30,
        keepalives_interval=10,
        keepalives_count=3,
        application_name="veris-token-logging",
    )


def log_usage(stage, response, model="claude-sonnet-4-6"):
    """Write one row to token_usage based on an Anthropic Messages API response.

    Args:
        stage: short label like 'extract', 'verdicts', 'api_web_search'
        response: the object returned by anthropic_client.messages.create(...)
        model: model name string; defaults to claude-sonnet-4-6 which is
               what all current call sites use.

    Returns:
        None. Failures are warned but never raised.
    """
    try:
        usage = getattr(response, "usage", None)
        if usage is None:
            print(f"[token_log] {stage}: no .usage on response (skipping)")
            return

        # Standard fields, present on all responses
        input_tokens = getattr(usage, "input_tokens", 0) or 0
        output_tokens = getattr(usage, "output_tokens", 0) or 0

        # Cache fields, only present when prompt caching is used.
        # getattr with default avoids AttributeError on uncached responses.
        cache_creation = getattr(usage, "cache_creation_input_tokens", 0) or 0
        cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0

        # Request ID for spot-checking against the Anthropic console
        request_id = getattr(response, "id", None)

        conn = _connect()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO token_usage
                        (stage, model, input_tokens, output_tokens,
                         cache_creation_input_tokens, cache_read_input_tokens,
                         request_id)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        stage, model, input_tokens, output_tokens,
                        cache_creation, cache_read, request_id,
                    ),
                )
            conn.commit()
        finally:
            conn.close()
    except Exception as e:
        # Never let logging block actual work
        print(f"[token_log] {stage}: write failed: {type(e).__name__}: {e}")
