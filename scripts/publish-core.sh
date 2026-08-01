#!/usr/bin/env bash
# Publish packages/rsg-hermes-core to the standalone repo the app repos install.
#
# SOURCE OF TRUTH IS THIS REPO. github.com/googrlc/rsg-hermes-core is a mirror,
# published from here — never commit to it directly. The alternative (editing
# the core in its own repo and vendoring it back) gives two places to change one
# file, which is the failure mode this whole split exists to remove.
#
# The mirror is what the other app repos pin, by commit:
#   rsg-hermes-core @ git+https://github.com/googrlc/rsg-hermes-core@<sha>
#
# Run after any change under packages/rsg-hermes-core, then bump the pin in the
# consumers that need it. Consumers are pinned by sha precisely so that
# publishing does not move anyone until they choose to move.
#
# Note: `git subtree split` cannot follow the rename that created this
# directory, so the mirror's history starts there. The full history stays in
# this repo, which is another reason the mirror is not the source of truth.

set -euo pipefail

PREFIX="packages/rsg-hermes-core"
REMOTE="https://github.com/googrlc/rsg-hermes-core.git"
BRANCH="_core-publish"

cd "$(git rev-parse --show-toplevel)"

if [[ -n "$(git status --porcelain -- "$PREFIX")" ]]; then
  echo "refusing to publish: uncommitted changes under $PREFIX" >&2
  exit 1
fi

git branch -D "$BRANCH" >/dev/null 2>&1 || true
SHA="$(git subtree split --prefix="$PREFIX" -b "$BRANCH" 2>/dev/null | tail -1)"

# Force, because a subtree split is a rewrite of the mirror's history, not an
# append to it. Safe only because nothing is authored in the mirror.
git push --force "$REMOTE" "$BRANCH:main"
git branch -D "$BRANCH" >/dev/null 2>&1 || true

echo
echo "published $PREFIX -> $REMOTE"
echo "pin consumers at: $SHA"
echo
echo "  rsg-hermes-core @ git+${REMOTE%.git}@$SHA"
