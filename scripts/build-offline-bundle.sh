#!/usr/bin/env bash
# Run on a connected build host with Docker. No deployment credentials needed.
set -euo pipefail
cd "$(dirname "$0")/.."
platform="${1:-linux/amd64}"
release="${2:-offline}"
if [[ "$platform" == linux/amd64 ]]; then
  mode="${3:-thick}"
else
  mode="${3:-thin}"
fi
case "$mode" in
  thin) target=thin ;;
  thick) target=oracle-thick ;;
  *) echo "Mode must be thin or thick" >&2; exit 2 ;;
esac
if [[ "$mode" == thick && "$platform" != linux/amd64 ]]; then
  echo "Oracle Thick bundle currently requires linux/amd64" >&2; exit 2
fi
case "$platform" in
  linux/amd64|linux/arm64) ;;
  *) echo 'Usage: bash scripts/build-offline-bundle.sh [linux/amd64|linux/arm64] [release-tag] [thick|thin]' >&2; exit 2 ;;
esac
if [[ ! "$release" =~ ^[a-zA-Z0-9_][a-zA-Z0-9_.-]{0,127}$ ]]; then
  echo 'Invalid Docker release tag' >&2
  exit 2
fi
image="alarm-manager-server:$release"
out="dist/offline-$release-${platform#linux/}"
if [[ -e "$out" ]]; then
  echo "Output already exists: $out; choose another release tag" >&2
  exit 1
fi
command -v docker >/dev/null
mkdir -p "$out/docs"
printf 'Building %s bundle (Docker target=%s) for %s\n' "$mode" "$target" "$platform"
if [[ "$mode" == thin ]]; then
  printf 'Thin image omits Oracle Instant Client; DPI-1047 if ORACLE_MODE=thick\n' >&2
fi
docker build --platform "$platform" --target "$target" --tag "$image" .
if [[ "$mode" == thick ]]; then
  docker run --rm --network none --platform "$platform" "$image" python -c \
    'import oracledb; oracledb.init_oracle_client(); assert not oracledb.is_thin_mode(); print("Oracle Thick OK", oracledb.clientversion())'
fi
# Verify dependencies and entry points without access to any network.
docker run --rm --network none --platform "$platform" "$image" python -m pip check
docker run --rm --network none --platform "$platform" --workdir /tmp "$image" python -c \
  'import oracledb; import alarm_manager_server.plugins.registry; import alarm_manager_server.plugins.oracle_diagnostics; import alarm_manager_server.plugins.oracle_check; import alarm_manager_server.api.app; import alarm_manager_server.worker.run; from zoneinfo import ZoneInfo; ZoneInfo("Europe/Moscow"); print("Offline imports OK")'
docker run --rm --network none --platform "$platform" "$image" alarm-manager-worker --help >/dev/null
docker run --rm --network none --platform "$platform" "$image" python -m pip freeze > "$out/python-packages.txt"
docker image inspect "$image" > "$out/image-inspect.json"
docker image save --output "$out/image.tar" "$image"
cp deploy/docker-compose.offline.yml "$out/compose.yml"
cp .env.example "$out/.env.example"
printf '\nALARM_MANAGER_IMAGE=%s\n' "$image" >> "$out/.env.example"
# Set the delivered template to the selected image mode.
python3 -c '
from pathlib import Path
import re, sys
path, mode = Path(sys.argv[1]), sys.argv[2]
text, n = re.subn(r"(?m)^ORACLE_MODE=.*$", "ORACLE_MODE=" + mode, path.read_text(), count=1)
if n != 1:
    raise SystemExit("ORACLE_MODE line not found in .env.example")
path.write_text(text)
' "$out/.env.example" "$mode"
cp docs/*.md "$out/docs/"
(
  cd "$out"
  if command -v sha256sum >/dev/null; then
    sha256sum image.tar compose.yml .env.example python-packages.txt image-inspect.json docs/*.md > SHA256SUMS
  else
    shasum -a 256 image.tar compose.yml .env.example python-packages.txt image-inspect.json docs/*.md > SHA256SUMS
  fi
)
printf 'Offline bundle: %s\n' "$out"

tar -czf "${out}.tar.gz" "$out"
printf 'Bundle: %s\n' "${out}.tar.gz"
