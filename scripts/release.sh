#!/usr/bin/env bash
set -euo pipefail

usage() {
    echo "usage: scripts/release.sh <version>" >&2
    echo "  <version> is a bare semver, e.g. 0.4.0 (no leading 'v')" >&2
    exit 1
}

[ $# -eq 1 ] || usage

VERSION="$1"
case "$VERSION" in
    [0-9]*.[0-9]*.[0-9]*) ;;
    *)
        echo "error: '$VERSION' does not look like a semver (X.Y.Z)" >&2
        exit 1
        ;;
esac

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if ! git diff --quiet || ! git diff --cached --quiet; then
    echo "error: working tree is dirty; commit or stash changes first" >&2
    exit 1
fi

TAG="v$VERSION"
if git rev-parse "$TAG" >/dev/null 2>&1; then
    echo "error: tag $TAG already exists" >&2
    exit 1
fi

echo "==> Bumping pyproject.toml to $VERSION"
python3 - "$VERSION" <<'PY'
import re
import sys

version = sys.argv[1]
path = "pyproject.toml"
with open(path, encoding="utf-8") as f:
    text = f.read()

new_text, count = re.subn(
    r'(?m)^version = "[^"]+"$',
    f'version = "{version}"',
    text,
    count=1,
)
if count != 1:
    raise SystemExit("error: could not find a `version = \"...\"` line in pyproject.toml")

with open(path, "w", encoding="utf-8") as f:
    f.write(new_text)
PY

echo "==> Refreshing uv.lock"
uv lock

LAST_TAG="$(git describe --tags --abbrev=0 2>/dev/null || true)"
if [ -n "$LAST_TAG" ]; then
    RANGE="$LAST_TAG..HEAD"
    echo "==> Collecting commits since $LAST_TAG"
else
    RANGE="HEAD"
    echo "==> Collecting all commits (no previous tag found)"
fi

SUBJECTS_FILE="$(mktemp)"
trap 'rm -f "$SUBJECTS_FILE"' EXIT
git log --no-merges --pretty=format:'%s' "$RANGE" > "$SUBJECTS_FILE"

if [ ! -s "$SUBJECTS_FILE" ]; then
    echo "error: no commits found in range $RANGE" >&2
    exit 1
fi

SECTION_FILE="$(mktemp)"
trap 'rm -f "$SUBJECTS_FILE" "$SECTION_FILE"' EXIT

TODAY="$(date +%Y-%m-%d)"
{
    echo "## [$VERSION] - $TODAY"
    echo
} > "$SECTION_FILE"

GROUP_ORDER_FILE="$(mktemp)"
trap 'rm -f "$SUBJECTS_FILE" "$SECTION_FILE" "$GROUP_ORDER_FILE"' EXIT

while IFS= read -r subject; do
    [ -n "$subject" ] || continue
    word="${subject%%[: ]*}"
    if ! grep -qxF "$word" "$GROUP_ORDER_FILE" 2>/dev/null; then
        echo "$word" >> "$GROUP_ORDER_FILE"
    fi
done < "$SUBJECTS_FILE"

while IFS= read -r word; do
    echo "### $word" >> "$SECTION_FILE"
    while IFS= read -r subject; do
        [ -n "$subject" ] || continue
        this_word="${subject%%[: ]*}"
        if [ "$this_word" = "$word" ]; then
            echo "- $subject" >> "$SECTION_FILE"
        fi
    done < "$SUBJECTS_FILE"
    echo >> "$SECTION_FILE"
done < "$GROUP_ORDER_FILE"

echo "==> Prepending CHANGELOG.md section for $VERSION"
CHANGELOG="CHANGELOG.md"
if [ ! -f "$CHANGELOG" ]; then
    echo "# Changelog" > "$CHANGELOG"
    echo >> "$CHANGELOG"
fi

NEW_CHANGELOG="$(mktemp)"
trap 'rm -f "$SUBJECTS_FILE" "$SECTION_FILE" "$GROUP_ORDER_FILE" "$NEW_CHANGELOG"' EXIT

INSERT_LINE="$(grep -n '^## ' "$CHANGELOG" | head -1 | cut -d: -f1 || true)"
if [ -n "$INSERT_LINE" ]; then
    head -n "$((INSERT_LINE - 1))" "$CHANGELOG" > "$NEW_CHANGELOG"
    cat "$SECTION_FILE" >> "$NEW_CHANGELOG"
    tail -n "+$INSERT_LINE" "$CHANGELOG" >> "$NEW_CHANGELOG"
else
    cat "$CHANGELOG" > "$NEW_CHANGELOG"
    echo >> "$NEW_CHANGELOG"
    cat "$SECTION_FILE" >> "$NEW_CHANGELOG"
fi
mv "$NEW_CHANGELOG" "$CHANGELOG"

echo "==> Committing release: $TAG"
git add pyproject.toml uv.lock CHANGELOG.md
git commit -m "release: $TAG"

echo "==> Tagging $TAG"
git tag -a "$TAG" -m "$TAG"

echo
echo "Done. Review the commit and tag, then push with:"
echo
echo "    git push origin HEAD $TAG"
echo
