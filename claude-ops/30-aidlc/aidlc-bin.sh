#!/usr/bin/env bash
# Wrapper cho aidlc CLI built ở /tmp/aidlc-test (NOT global install).
#
# Why: anh confirm "/var/www/html/ragbot làm ở đây thôi, đừng đưa ra ngoài
# ảnh hưởng". npm install -g aidlc sẽ install ở /usr/local/lib/node_modules
# = system-wide. Build local trong /tmp/ + wrap qua script này = scope local
# tới ragbot only.
#
# Usage:
#   bash claude-ops/30-aidlc/aidlc-bin.sh init
#   bash claude-ops/30-aidlc/aidlc-bin.sh validate
#   bash claude-ops/30-aidlc/aidlc-bin.sh list
#   bash claude-ops/30-aidlc/aidlc-bin.sh run start <stream>
#
# Or alias trong shell rc:
#   alias aidlc='bash /var/www/html/ragbot/claude-ops/30-aidlc/aidlc-bin.sh'

set -u

AIDLC_BUILD_DIR="${AIDLC_BUILD_DIR:-/tmp/aidlc-test}"
CLI_PATH="$AIDLC_BUILD_DIR/packages/cli/dist/index.js"

if [ ! -f "$CLI_PATH" ]; then
  echo "[aidlc-bin] CLI binary not found at $CLI_PATH" >&2
  echo "[aidlc-bin] Re-build: cd $AIDLC_BUILD_DIR && pnpm install && pnpm build" >&2
  echo "[aidlc-bin] Or set AIDLC_BUILD_DIR env var to your build location." >&2
  exit 2
fi

# Workspace defaults to /var/www/html/ragbot if running from anywhere else.
WORKSPACE="${AIDLC_WORKSPACE:-/var/www/html/ragbot}"

exec node "$CLI_PATH" --workspace "$WORKSPACE" "$@"
