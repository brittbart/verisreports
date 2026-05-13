#!/usr/bin/env python3
"""
resolve_speaker.py — Verum Signal speaker resolution module.

Provides find_or_create_speaker(name, context=None, conn=None) which:
  1. Normalizes the raw name (lowercase, strip titles/suffixes)
  2. Looks up by normalized_name in the speakers table
  3. Returns existing speaker_id if found
  4. Creates a new speaker record if not found
  5. Checks speaker_aliases for known alternate names (e.g. AOC)

Also maintains a speaker_aliases table (created here if not exists).

USAGE (standalone test):
  cd ~/projects/veris && source venv/bin/activate
  python3 resolve_speaker.py --test

USAGE (as module):
  from resolve_speaker import find_or_create_speaker
  speaker_id = find_or_create_speaker("Rep. Alexandria Ocasio-Cortez", conn=conn)
"""

import os
import re
import sys
import unicodedata
import argparse

try:
    import psycopg2
except ImportError:
    print("ERROR: psycopg2 not installed.", file=sys.stderr)
    sys.exit(1)


# ---------------------------------------------------------------------------
# Name normalization
# ---------------------------------------------------------------------------

# Titles to strip from the front of names
TITLES = {
    "president", "vice president", "vp",
    "senator", "sen", "representative", "rep",
    "governor", "gov", "lieutenant governor", "lt. gov",
    "secretary", "sec",
    "attorney general", "ag",
    "dr", "dr.", "mr", "mr.", "mrs", "mrs.", "ms", "ms.",
    "the honorable", "hon", "hon.",
    "general", "gen", "admiral", "adm",
    "ambassador", "amb",
    "interior secretary", "us president", "u.s. president",
    "attorney general", "ag",
}

# Suffixes to strip from the end of names
SUFFIXES = {"jr", "jr.", "sr", "sr.", "ii", "iii", "iv", "esq", "esq."}


def normalize_name(raw_name: str) -> str:
    """
    Normalize a speaker name for deduplication lookup.

    Steps:
      1. Unicode normalize (NFKC) and strip whitespace
      2. Lowercase
      3. Strip common titles from the front
      4. Strip common suffixes from the end
      5. Collapse internal whitespace
    """
    if not raw_name:
        return ""

    # Strip trailing role after comma BEFORE lowercasing/normalizing
    # e.g. "Ryne Saxe, Eco CEO" -> "Ryne Saxe"
    # Only strip if part before first comma is 1-3 words
    raw_stripped = raw_name.strip()
    # Strip leading US state name: "Minnesota Gov. Tim Walz" -> "Gov. Tim Walz"
    US_STATES = r"(?:Alabama|Alaska|Arizona|Arkansas|California|Colorado|Connecticut|Delaware|Florida|Georgia|Hawaii|Idaho|Illinois|Indiana|Iowa|Kansas|Kentucky|Louisiana|Maine|Maryland|Massachusetts|Michigan|Minnesota|Mississippi|Missouri|Montana|Nebraska|Nevada|New Hampshire|New Jersey|New Mexico|New York|North Carolina|North Dakota|Ohio|Oklahoma|Oregon|Pennsylvania|Rhode Island|South Carolina|South Dakota|Tennessee|Texas|Utah|Vermont|Virginia|Washington|West Virginia|Wisconsin|Wyoming)"
    raw_stripped = re.sub(r"^" + US_STATES + r"\s+", "", raw_stripped).strip()
    # Strip party affiliation: "Tim Walz (D)" -> "Tim Walz"
    raw_stripped = re.sub(r"\s*\([A-Z]\)$", "", raw_stripped).strip()
    if "," in raw_stripped:
        comma_parts = raw_stripped.split(",", 1)
        before = comma_parts[0].strip()
        if 1 <= len(before.split()) <= 3:
            raw_stripped = before

    # Unicode normalize and lowercase
    name = unicodedata.normalize("NFKC", raw_stripped).strip().lower()

    # Remove punctuation that varies (periods in titles like "Rep." vs "Rep")
    # but keep hyphens (Mary-Jane) and apostrophes (O'Brien)
    name = re.sub(r"[,]", "", name)

    # Strip titles from the front (greedy: strip multiple if stacked)
    changed = True
    while changed:
        changed = False
        for title in TITLES:
            pattern = r"^" + re.escape(title) + r"\.?\s+"
            new_name = re.sub(pattern, "", name).strip()
            if new_name != name:
                name = new_name
                changed = True

    # Strip suffixes from the end
    parts = name.split()
    while parts and parts[-1].rstrip(".") in SUFFIXES:
        parts.pop()
    name = " ".join(parts)

    # Strip trailing role descriptor after comma: "Roland Lescure, French Finance Minister"
    # Only strip if the part before the comma is 1-3 words (looks like a name)
    comma_parts = name.split(",", 1)
    if len(comma_parts) == 2:
        before = comma_parts[0].strip()
        if 1 <= len(before.split()) <= 3:
            name = before

    # Collapse whitespace
    name = re.sub(r"\s+", " ", name).strip()

    return name


def name_to_slug(normalized_name: str) -> str:
    """Convert a normalized name to a URL-safe slug."""
    slug = re.sub(r"[^a-z0-9]+", "-", normalized_name)
    slug = slug.strip("-")
    return slug


# ---------------------------------------------------------------------------
# Alias table bootstrap
# ---------------------------------------------------------------------------

CREATE_ALIASES_TABLE = """
CREATE TABLE IF NOT EXISTS speaker_aliases (
    id            SERIAL PRIMARY KEY,
    alias         TEXT NOT NULL,
    normalized_alias TEXT NOT NULL,
    speaker_id    INTEGER NOT NULL REFERENCES speakers(id) ON DELETE CASCADE,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT speaker_aliases_normalized_unique UNIQUE (normalized_alias)
);
CREATE INDEX IF NOT EXISTS idx_speaker_aliases_normalized
    ON speaker_aliases (normalized_alias);
"""

# Seed aliases: map from normalized alias -> canonical normalized name
# Add to this list as edge cases are discovered.
SEED_ALIASES = [
    ("aoc", "alexandria ocasio-cortez"),
    ("ocasio-cortez", "alexandria ocasio-cortez"),
    ("djt", "donald trump"),
    ("potus", None),  # None = skip seeding; too ambiguous
    ("flotus", None),
    ("veep", None),
]


def ensure_aliases_table(cur):
    """Create speaker_aliases table if it doesn't exist."""
    cur.execute(CREATE_ALIASES_TABLE)


# ---------------------------------------------------------------------------
# Core lookup / creation
# ---------------------------------------------------------------------------

def find_speaker_by_normalized(cur, normalized: str):
    """Return (id, name, normalized_name) or None."""
    cur.execute(
        "SELECT id, name, normalized_name FROM speakers WHERE normalized_name = %s",
        (normalized,)
    )
    return cur.fetchone()


def find_speaker_by_alias(cur, normalized: str):
    """Check speaker_aliases for this normalized name. Return speaker row or None."""
    cur.execute(
        """
        SELECT s.id, s.name, s.normalized_name
        FROM speaker_aliases sa
        JOIN speakers s ON s.id = sa.speaker_id
        WHERE sa.normalized_alias = %s
        """,
        (normalized,)
    )
    return cur.fetchone()


def create_speaker(cur, name: str, normalized: str, slug: str) -> int:
    """Insert a new speaker record and return the new id."""
    # Handle slug collisions by appending a counter
    base_slug = slug
    counter = 1
    while True:
        cur.execute("SELECT id FROM speakers WHERE slug = %s", (slug,))
        if cur.fetchone() is None:
            break
        slug = f"{base_slug}-{counter}"
        counter += 1

    cur.execute(
        """
        INSERT INTO speakers (name, normalized_name, slug, speaker_type)
        VALUES (%s, %s, %s, 'other')
        RETURNING id
        """,
        (name, normalized, slug)
    )
    return cur.fetchone()[0]


def find_or_create_speaker(raw_name: str, context: str = None, conn=None) -> int:
    """
    Resolve a raw speaker name to a speaker_id.

    Lookup order:
      1. Normalize the name
      2. Check speakers.normalized_name (exact match)
      3. Check speaker_aliases.normalized_alias
      4. Create new speaker record

    Args:
        raw_name: The name as it appears in the source (e.g. "Sen. Bernie Sanders")
        context:  Optional context string for future disambiguation (unused in v1.7)
        conn:     psycopg2 connection. If None, opens one from env vars.

    Returns:
        speaker_id (int)
    """
    close_conn = False
    if conn is None:
        conn = _get_connection()
        close_conn = True

    normalized = normalize_name(raw_name)
    if not normalized:
        raise ValueError(f"Could not normalize name: {raw_name!r}")

    slug = name_to_slug(normalized)

    try:
        with conn.cursor() as cur:
            ensure_aliases_table(cur)

            # 1. Direct normalized name match
            row = find_speaker_by_normalized(cur, normalized)
            if row:
                return row[0]

            # 2. Alias match
            row = find_speaker_by_alias(cur, normalized)
            if row:
                return row[0]

            # 3. Create new speaker
            speaker_id = create_speaker(cur, raw_name.strip(), normalized, slug)
            conn.commit()
            return speaker_id

    finally:
        if close_conn:
            conn.close()


# ---------------------------------------------------------------------------
# Connection helper
# ---------------------------------------------------------------------------

def _get_connection():
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


# ---------------------------------------------------------------------------
# Standalone test mode
# ---------------------------------------------------------------------------

TEST_NAMES = [
    "Sen. Bernie Sanders",
    "Senator Bernie Sanders",        # duplicate — should return same id
    "Bernie Sanders",                # duplicate — should return same id
    "Rep. Alexandria Ocasio-Cortez",
    "AOC",                           # alias — should return same id as above (after seeding)
    "President Joe Biden",
    "Joe Biden",                     # duplicate
    "Governor Gavin Newsom",
    "GOVERNOR GAVIN NEWSOM",         # case variant — should match
    "Dr. Anthony Fauci",
    "Anthony Fauci",                 # duplicate
    "Elon Musk",                     # no title
    "Elon Musk Jr.",                 # suffix strip
]


def run_tests():
    print("=" * 60)
    print("resolve_speaker.py — standalone test")
    print("=" * 60)

    conn = _get_connection()
    conn.autocommit = True

    results = {}
    for name in TEST_NAMES:
        normalized = normalize_name(name)
        try:
            speaker_id = find_or_create_speaker(name, conn=conn)
            flag = ""
            if normalized in results:
                expected_id = results[normalized]
                if speaker_id != expected_id:
                    flag = f"  *** MISMATCH (expected {expected_id})"
                else:
                    flag = "  (deduped ✓)"
            else:
                results[normalized] = speaker_id
            print(f"  {name!r:<45} -> id={speaker_id}  normalized={normalized!r}{flag}")
        except Exception as e:
            print(f"  {name!r:<45} -> ERROR: {e}")

    print()
    print("Distinct speakers created/matched:", len(set(results.values())))
    conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--test", action="store_true", help="Run test suite")
    args = parser.parse_args()

    if args.test:
        run_tests()
    else:
        print("Run with --test to execute the test suite.")
        print("Or import find_or_create_speaker for use as a module.")
