#!/usr/bin/env bash
set -euo pipefail

# Usage:
#   scripts/prepare_flathub_release.sh v0.1.0
#   scripts/prepare_flathub_release.sh v0.1.0 --manifest flatpak/de.pasuki.audiorouter.flathub.yml

TAG="${1:-}"
MANIFEST="flatpak/de.pasuki.audiorouter.flathub.yml"

if [[ -z "$TAG" ]]; then
  echo "Usage: $0 <git-tag> [--manifest <path>]" >&2
  exit 1
fi

shift || true
while [[ $# -gt 0 ]]; do
  case "$1" in
    --manifest)
      MANIFEST="${2:-}"
      shift 2
      ;;
    *)
      echo "Unknown argument: $1" >&2
      exit 1
      ;;
  esac
done

if [[ ! -f "$MANIFEST" ]]; then
  echo "Manifest not found: $MANIFEST" >&2
  exit 1
fi

if ! git rev-parse -q --verify "refs/tags/$TAG" >/dev/null; then
  echo "Tag '$TAG' not found locally. Create/fetch it first." >&2
  exit 1
fi

COMMIT="$(git rev-list -n1 "$TAG")"

python - "$MANIFEST" "$TAG" "$COMMIT" <<'PY'
import re
import sys
from pathlib import Path

manifest = Path(sys.argv[1])
tag = sys.argv[2]
commit = sys.argv[3]
text = manifest.read_text(encoding="utf-8")

new_text = re.sub(r"(?m)^(\s*tag:\s*).*$", rf"\1{tag}", text)
new_text = re.sub(r"(?m)^(\s*commit:\s*).*$", rf"\1{commit}", new_text)

if new_text == text:
    print("No changes made. Could not find tag/commit fields?", file=sys.stderr)
    sys.exit(2)

manifest.write_text(new_text, encoding="utf-8")
print(f"Updated {manifest}:")
print(f"  tag: {tag}")
print(f"  commit: {commit}")
PY

echo
echo "Done. Next steps:"
echo "  1) git diff $MANIFEST"
echo "  2) flatpak-builder --force-clean --install-deps-from=flathub build-dir $MANIFEST"
echo "  3) git add $MANIFEST && git commit"
