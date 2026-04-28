#!/usr/bin/env bash
# verify_handoff.sh — drift check for the agent handoff kit.
#
# Run in pre-commit (and CI) to catch stale docs:
#   1. dangling links in docs/*.md, AGENTS.md, .cursor/rules/*.mdc
#   2. references to files that no longer exist on disk
#   3. docs/HANDOFF.md "last verified vs <SHA>" header drift > 30 commits
#   4. docs/MAP.md stale relative to gen_map.sh
#
# Exit 0 on green; 1 on any failure with a descriptive message.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

fail=0
warn() { printf '[verify_handoff] WARN  %s\n' "$*" >&2; }
err()  { printf '[verify_handoff] ERROR %s\n' "$*" >&2; fail=1; }

# ---------------------------------------------------------------- 1. dangling md links
# Scope: only the new agent-handoff kit. Pre-existing docs (the ~96 legacy
# files) are cleaned up in Phase 4 of the cleanup plan; ignore them here.
KIT_FILES=(
    "AGENTS.md"
    "docs/HANDOFF.md"
    "docs/HANDOFF_INDEX.md"
    "docs/PLAYBOOK.md"
    "docs/DECISIONS.md"
    "docs/GOTCHAS.md"
    "docs/CONVENTIONS.md"
    "docs/CHANGELOG.md"
    "docs/ROADMAP.md"
    "docs/MAP.md"
)
md_files=()
for f in "${KIT_FILES[@]}"; do
    [[ -f "$f" ]] && md_files+=("$f")
done
while IFS= read -r f; do md_files+=("$f"); done < <(find .cursor/rules -type f -name '*.mdc' 2>/dev/null | LC_ALL=C sort)

for md in "${md_files[@]}"; do
    [[ -f "$md" ]] || continue
    base="$(dirname "$md")"
    while IFS= read -r link; do
        target="${link#*](}"
        target="${target%)*}"
        target="${target%%#*}"
        [[ -z "$target" ]] && continue
        case "$target" in
            http*|mailto:*|\#*) continue ;;
        esac
        if [[ "$target" == /* ]]; then
            [[ -e "$target" ]] || err "$md links to missing absolute path: $target"
        else
            [[ -e "$base/$target" || -e "$target" ]] || err "$md links to missing path: $target"
        fi
    done < <(grep -oE '\]\([^)]+\)' "$md" 2>/dev/null || true)
done

# ---------------------------------------------------------------- 2. file:line citations exist
for md in "${md_files[@]}"; do
    [[ -f "$md" ]] || continue
    while IFS= read -r ref; do
        path="${ref%%:*}"
        [[ -z "$path" ]] && continue
        case "$path" in
            http*|mailto:*) continue ;;
        esac
        [[ -e "$path" ]] || warn "$md cites missing path: $ref"
    done < <(grep -oE '`[a-zA-Z0-9_./-]+\.py:[0-9]+(-[0-9]+)?`' "$md" 2>/dev/null | tr -d '`' || true)
done

# ---------------------------------------------------------------- 3. HANDOFF SHA drift
if [[ -f docs/HANDOFF.md ]]; then
    last_sha=$(grep -oE '<!-- Last verified.*commit [0-9a-f]{7,40}' docs/HANDOFF.md | grep -oE '[0-9a-f]{7,40}$' | head -1 || true)
    if [[ -n "$last_sha" ]]; then
        if git rev-parse --verify "$last_sha" >/dev/null 2>&1; then
            distance=$(git rev-list --count "$last_sha..HEAD" 2>/dev/null || echo 0)
            if [[ "$distance" -gt 30 ]]; then
                warn "docs/HANDOFF.md was last verified $distance commits ago — refresh the header"
            fi
        else
            warn "docs/HANDOFF.md verified-SHA $last_sha is not in git history"
        fi
    else
        warn "docs/HANDOFF.md is missing the 'Last verified ... commit <sha>' header"
    fi
fi

# ---------------------------------------------------------------- 4. MAP.md staleness
if [[ -x scripts/gen_map.sh && -f docs/MAP.md ]]; then
    if ! scripts/gen_map.sh --check >/dev/null 2>&1; then
        err "docs/MAP.md is stale — run: scripts/gen_map.sh"
    fi
fi

if [[ $fail -ne 0 ]]; then
    printf '[verify_handoff] FAILED\n' >&2
    exit 1
fi
echo "[verify_handoff] OK"
