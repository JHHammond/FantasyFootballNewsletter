#!/usr/bin/env bash
#
# Nightly database backup.
#
# Why this matters more here than in most products: there are no passwords. A
# commissioner's only credential is their admin token, and that token exists
# nowhere but this database. So losing the database is not degraded service —
# it is every commissioner permanently locked out of their own paper, with no
# reset flow to fall back on and no support process that could verify them.
#
# Supabase's free tier gives you no point-in-time recovery, which means this
# script is the entire recovery story until you're paying for one.
#
# Usage:
#
#     DATABASE_URL='postgresql://...' ./scripts/backup.sh /path/to/backups
#
# The connection string is in the Supabase dashboard under
# Project Settings -> Database -> Connection string -> URI. Use the direct
# connection rather than the pooler; pg_dump needs session-level access.
#
# Run it from anywhere that runs on a schedule and is not Supabase — a Render
# cron, a GitHub Action, your own machine. A backup stored in the thing you are
# backing up is not a backup.
#
# IMPORTANT: restore one before you need to. An untested backup is a guess.
# There are instructions at the bottom of this file.

set -euo pipefail

DEST="${1:-./backups}"
KEEP_DAYS="${KEEP_DAYS:-30}"

if [[ -z "${DATABASE_URL:-}" ]]; then
    echo "DATABASE_URL is not set. See the header of this script." >&2
    exit 1
fi

if ! command -v pg_dump >/dev/null 2>&1; then
    echo "pg_dump not found. Install the postgresql-client package." >&2
    exit 1
fi

mkdir -p "$DEST"

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
FILE="$DEST/commissioners-desk-$STAMP.dump"

echo "Dumping to $FILE"

# Custom format (-Fc): compressed, and restorable table by table with pg_restore
# rather than all or nothing.
pg_dump "$DATABASE_URL" \
    --format=custom \
    --no-owner \
    --no-privileges \
    --file="$FILE"

# A dump that restores to nothing is worse than no dump, because you believe in
# it. Check the archive actually lists the tables that matter.
echo "Verifying the archive..."
for table in leagues newspapers subscribers; do
    if ! pg_restore --list "$FILE" | grep -q "TABLE DATA public $table"; then
        echo "FAILED: '$table' has no data in this dump. Not keeping it." >&2
        mv "$FILE" "$FILE.suspect"
        exit 1
    fi
done

SIZE="$(du -h "$FILE" | cut -f1)"
echo "OK — $FILE ($SIZE)"

# Prune old dumps. Deliberately after the verification above, so a run that
# produced a bad dump never deletes the good ones.
find "$DEST" -name 'commissioners-desk-*.dump' -type f -mtime "+$KEEP_DAYS" -print -delete

echo "Done. Keeping $KEEP_DAYS days."

# ---------------------------------------------------------------------------
# Restoring
#
#   1. Make a new Supabase project.
#   2. Run the migrations in order (002 through 008) to create the schema.
#   3. pg_restore --data-only --disable-triggers \
#          --dbname="$NEW_DATABASE_URL" commissioners-desk-<stamp>.dump
#   4. Point SUPABASE_URL and SUPABASE_SERVICE_KEY at the new project.
#
# Papers themselves live in Storage, not Postgres, so a full recovery also
# needs the bucket. Losing the bucket alone is survivable — every paper can be
# re-rendered from the ai_cache column with no Claude call, which is exactly
# what the edit flow already does.
# ---------------------------------------------------------------------------
