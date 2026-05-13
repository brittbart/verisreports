#!/usr/bin/env python3
"""
backfill_speaker_ids.py — Verum Signal v1.7 speaker backfill.

Resolves speaker_id on existing attributed_claim rows where speaker IS NOT NULL
and speaker_id IS NULL.

Skips non-person entries (organizations, anonymous sources, compound attributions)
and logs them to a skip file for review.

USAGE:
  cd ~/projects/veris && source venv/bin/activate
  python3 backfill_speaker_ids.py --dry-run   # preview without writing
  python3 backfill_speaker_ids.py             # run for real in batches of 100

OUTPUT:
  backfill_skipped.txt  — speaker values that were skipped (not person names)
  backfill_resolved.txt — speaker values that were resolved to a speaker_id
"""

import argparse
import os
import re
import sys

try:
    import psycopg2
except ImportError:
    print("ERROR: psycopg2 not installed.", file=sys.stderr)
    sys.exit(1)

# Must be in the same directory
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from resolve_speaker import find_or_create_speaker, normalize_name

BATCH_SIZE = 100

# ---------------------------------------------------------------------------
# Skip detection
# ---------------------------------------------------------------------------

# Patterns that indicate this is NOT a named individual
SKIP_PATTERNS = [
    r"\s*/\s*",                          # compound attribution: "Al Jazeera / Trump admin"
    r"\bas reported by\b",           # "(as reported by Reuters)"
    r"\bas cited by\b",
    r"\bvia \b",
    r"\bpress release\b",
    r"\breport cited by\b",
    r"\bciting\b",
    r"\barticle\b",
    r"\bheadline\b",
    r"\bcontent\b",
    r"\bdescription\b",
]

SKIP_EXACT = {
    # Anonymous / vague
    "anonymous", "anonymous sources", "analysts", "analyst", "analysts (unnamed)",
    "analyst (unnamed)", "unnamed", "unnamed sources", "unnamed expert",
    "an unnamed expert", "an unnamed official", "a senior official",
    "a government official", "a us official", "a former official",
    "a western official", "a source", "sources", "insiders",
    # Generic roles without names
    "a doctor", "a fisherman", "a fishermen's leader", "a trader",
    "an oil market expert", "an economist",
    "faa spokesperson", "fada president", "chief rabbi",
    "military officials", "ministers (unnamed)", "citi analysts",
    "unnamed iranian official", "authorities", "officials", "researchers",
    "prosecutors", "strategists", "unnamed critics", "unnamed experts",
    "unnamed radio host", "the pope", "mossad chief", "dhs secretary",
    "the white house", "pentagon", "hezbollah", "un", "bjp", "virginia gop",
    "legal aid group", "emergency alert",
}

# Prefixes that indicate an organization, not a person
ORG_PREFIXES = {
    "abc", "al jazeera", "amnesty", "apple", "adobe", "anadolu",
    "associated press", "ap", "bbc", "bloomberg", "cbs", "cnn",
    "fox news", "reuters", "nbc", "nyt", "new york times",
    "washington post", "the guardian", "politico", "axios",
    "white house", "state department", "department of", "ministry of",
    "european union", "eu", "un ", "united nations", "nato",
    "fbi", "cia", "doj", "sec ", "fed ", "federal reserve",
    "npr", "pbs", "booking", "booking.com", "airbnb", "uber", "lyft",
    "coindesk", "cointelegraph", "decrypt", "zycrypto", "ravedao",
    "crypto briefing", "the atlantic", "the new york times", "the wall street journal",
    "business insider", "economic times", "financial stability board",
    "us securities", "islamic revolutionary guard", "trinidad",
    "indonesian government", "motilal oswal",
    "world bank", "imf", "who ", "cdc",
    "all progressives", "argenx", "arbitrum", "aave",
}


def should_skip(speaker: str) -> tuple[bool, str]:
    """
    Returns (True, reason) if this speaker value should be skipped,
    (False, "") otherwise.
    """
    if not speaker or not speaker.strip():
        return True, "empty"

    s = speaker.strip()
    s_lower = s.lower()

    # Exact match against known non-persons
    if s_lower in SKIP_EXACT:
        return True, "anonymous/vague"

    # Pattern match
    for pattern in SKIP_PATTERNS:
        if re.search(pattern, s, re.IGNORECASE):
            return True, f"pattern: {pattern}"

    # Org prefix match
    for prefix in ORG_PREFIXES:
        if s_lower.startswith(prefix):
            return True, f"org prefix: {prefix}"

    # Parenthetical-only entries (entire value is a note)
    if s.startswith("(") and s.endswith(")"):
        return True, "parenthetical"

    # Very long values are likely compound/descriptive, not a name
    if len(s) > 80:
        return True, "too long (likely descriptive)"

    # Contains digits — probably a stat or org identifier
    if re.search(r"\d", s):
        return True, "contains digits"

    # Academic author lists
    if " & " in s:
        return True, "academic author list"

    # Known single-word orgs/countries
    SINGLE_WORD_ORGS = {
        "iran", "russia", "china", "israel", "ukraine", "grinex", "pepeto",
        "apple", "google", "meta", "tesla", "twitter", "spacex",
    }
    if s_lower in SINGLE_WORD_ORGS:
        return True, "single-word org/country"

    # Parenthetical role descriptor e.g. "Abed Abou Shhadeh (Political commentator)"
    if re.search(r"\((?:political commentator|commentator|spokesperson|analyst|editor|journalist|reporter|activist|lawyer|attorney|professor|researcher|expert|advisor|adviser|consultant|host|anchor|author|writer|columnist|pundit|strategist|executive|ceo|cfo|coo|founder|director|chair|chairman).*\)", s, re.IGNORECASE):
        return True, "parenthetical role descriptor"

    # Anonymous regional sources e.g. "Iranian sources"
    if re.search(r"^(?:iranian|russian|chinese|israeli|us|american|western|senior|unnamed|anonymous)\s+(?:sources?|officials?|authorities|government|military|intelligence)", s, re.IGNORECASE):
        return True, "anonymous sources"

    # Government/org parenthetical: "Iran (Iranian government)"
    if re.search(r"\(.*(?:government|representative|ministry|official|implied|attributed).*\)", s, re.IGNORECASE):
        return True, "government/org entity"

    # Role-only entries (no actual name, just a title/role)
    ROLE_ONLY = {
        "the prime minister", "the president", "the minister",
        "uk's eu minister", "the chancellor", "the secretary",
    }
    if s_lower in ROLE_ONLY:
        return True, "role-only, no name"

    # Contains org/media keywords indicating not a person
    if re.search(r"\b(?:youtube|channel|community analyst|joint chiefs|chiefs of staff|navy|army|air force|marines|coast guard|spokesperson|police|sheriff|citi analyst|citi analysts|blockworks|glassnode|cyvers|watchdog|junta|bureau|institute|council|foundation|committee|coalition|ministry|agency|league|association|organization|organisation|centre|center|monitor|flotilla|indictment|prosecutors|investigators|authorities|officials|researchers|strategists|aides|workers|lawyers|activists|members|critics|experts|readers|stakeholders)\b", s, re.IGNORECASE):
        return True, "org/role keyword"

    # Complex geographic+title prefixes with no clean name extraction
    if re.search(r"^(?:andhra pradesh|uttar pradesh|tamil nadu|west bengal|u\.s\.|u\.s ambassador)", s, re.IGNORECASE):
        return True, "geographic title prefix"

    # Possessive + office/department = org, not a person
    if re.search(r"'s\s+(?:office|department|administration|team|spokesperson|press secretary)", s, re.IGNORECASE):
        return True, "org possessive"

    return False, ""


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def get_connection():
    database_url = os.environ.get("DATABASE_URL")
    if database_url:
        return psycopg2.connect(database_url)
    return psycopg2.connect(
        host=os.environ.get("PGHOST"),
        port=os.environ.get("PGPORT", "5432"),
        user=os.environ.get("PGUSER"),
        password=os.environ.get("PGPASSWORD"),
        dbname=os.environ.get("PGDATABASE", "railway"),
    )


def fetch_batch(conn, offset: int):
    """Fetch a batch of attributed claims needing speaker resolution."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, speaker
            FROM claims
            WHERE claim_origin = 'attributed_claim'
              AND speaker IS NOT NULL
              AND speaker_id IS NULL
            ORDER BY id
            LIMIT %s OFFSET %s
            """,
            (BATCH_SIZE, offset),
        )
        return cur.fetchall()


def update_speaker_id(conn, claim_id: int, speaker_id: int):
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE claims SET speaker_id = %s WHERE id = %s",
            (speaker_id, claim_id),
        )
    conn.commit()


# ---------------------------------------------------------------------------
# Main backfill
# ---------------------------------------------------------------------------

def run_backfill(dry_run: bool):
    print("=" * 68)
    print("Verum Signal v1.7 — speaker_id backfill")
    print(f"Mode: {'DRY RUN (no writes)' if dry_run else 'APPLY'}")
    print("=" * 68)

    conn = get_connection()
    conn.autocommit = True

    # Count total
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT COUNT(*) FROM claims
            WHERE claim_origin = 'attributed_claim'
              AND speaker IS NOT NULL
              AND speaker_id IS NULL
            """
        )
        total = cur.fetchone()[0]

    print(f"Total claims to process: {total}")
    print()

    resolved_log = []
    skipped_log = []
    error_log = []

    offset = 0
    resolved_count = 0
    skipped_count = 0
    error_count = 0

    while True:
        batch = fetch_batch(conn, offset)
        if not batch:
            break

        print(f"Batch offset={offset} ({len(batch)} rows)")

        for claim_id, speaker in batch:
            skip, reason = should_skip(speaker)

            if skip:
                skipped_count += 1
                skipped_log.append(f"{claim_id}\t{reason}\t{speaker}")
                continue

            try:
                if not dry_run:
                    speaker_id = find_or_create_speaker(speaker, conn=conn)
                    update_speaker_id(conn, claim_id, speaker_id)
                    resolved_log.append(f"{claim_id}\t{speaker_id}\t{speaker}")
                else:
                    normalized = normalize_name(speaker)
                    resolved_log.append(f"{claim_id}\t[dry-run]\t{speaker}\t->\t{normalized}")
                resolved_count += 1
            except Exception as e:
                error_count += 1
                error_log.append(f"{claim_id}\tERROR: {e}\t{speaker}")
                print(f"  ERROR claim {claim_id}: {e}")

        offset += BATCH_SIZE

        # Spot-check prompt every batch
        print(f"  resolved={resolved_count}  skipped={skipped_count}  errors={error_count}  processed={offset}")

    print()
    print("=" * 68)
    print(f"DONE. resolved={resolved_count}  skipped={skipped_count}  errors={error_count}")
    print("=" * 68)

    # Write logs
    with open("backfill_resolved.txt", "w") as f:
        f.write("claim_id\tspeaker_id\tspeaker\n")
        f.write("\n".join(resolved_log))

    with open("backfill_skipped.txt", "w") as f:
        f.write("claim_id\treason\tspeaker\n")
        f.write("\n".join(skipped_log))

    if error_log:
        with open("backfill_errors.txt", "w") as f:
            f.write("claim_id\terror\tspeaker\n")
            f.write("\n".join(error_log))
        print("Errors written to backfill_errors.txt — review before proceeding.")

    print("Logs written to backfill_resolved.txt and backfill_skipped.txt")
    conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="Preview without writing")
    args = parser.parse_args()
    run_backfill(dry_run=args.dry_run)
