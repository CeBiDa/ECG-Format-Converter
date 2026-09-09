#!/usr/bin/env sh
# Keep the agent skill in sync across harness locations.
#
#   sh skills/sync.sh            copy the canonical skill into .claude/ and .opencode/
#   sh skills/sync.sh --check    verify the copies match (exit 1 if not) -- for CI
#   sh skills/sync.sh --user     also install into the current user's global skill dirs
#
# Canonical source: skills/<name>/SKILL.md
# Claude Code reads .claude/skills/<name>/SKILL.md; opencode reads
# .opencode/skills/<name>/SKILL.md (and .claude/skills/ as a documented fallback).
set -eu

SKILL_NAME=ecg-format-converter
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
SRC="$ROOT/skills/$SKILL_NAME/SKILL.md"

[ -f "$SRC" ] || { echo "missing canonical skill: $SRC" >&2; exit 1; }

MODE=sync
case "${1:-}" in
    --check) MODE=check ;;
    --user)  MODE=user ;;
    "")      ;;
    *)       echo "usage: $0 [--check|--user]" >&2; exit 2 ;;
esac

install_to() {
    dest="$1/$SKILL_NAME/SKILL.md"
    if [ "$MODE" = check ]; then
        if ! cmp -s "$SRC" "$dest"; then
            echo "OUT OF SYNC: $dest" >&2
            return 1
        fi
        echo "ok: $dest"
    else
        mkdir -p "$1/$SKILL_NAME"
        cp "$SRC" "$dest"
        echo "wrote: $dest"
    fi
}

rc=0
install_to "$ROOT/.claude/skills"   || rc=1
install_to "$ROOT/.opencode/skills" || rc=1

if [ "$MODE" = user ]; then
    install_to "$HOME/.claude/skills" || rc=1
    install_to "${XDG_CONFIG_HOME:-$HOME/.config}/opencode/skills" || rc=1
fi

[ "$MODE" = check ] && [ "$rc" -ne 0 ] && echo "run: sh skills/sync.sh" >&2
exit "$rc"
