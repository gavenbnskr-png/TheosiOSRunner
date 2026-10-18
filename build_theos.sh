#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$PWD/incoming"
WORK="$PWD/work"
OUT="$PWD/output"
mkdir -p "$WORK" "$OUT"

# Extract safely: reject absolute paths and parent traversal.
python3 - <<'PY'
import zipfile, pathlib
zpath = pathlib.Path("incoming/project.zip")
dest = pathlib.Path("work/project").resolve()
dest.mkdir(parents=True, exist_ok=True)
with zipfile.ZipFile(zpath) as z:
    for info in z.infolist():
        name = info.filename.replace("\\", "/")
        p = pathlib.PurePosixPath(name)
        if p.is_absolute() or ".." in p.parts:
            raise SystemExit("Unsafe ZIP path rejected: " + name)
        target = (dest / name).resolve()
        if dest not in target.parents and target != dest:
            raise SystemExit("ZIP path escapes extraction directory")
        if info.file_size > 100 * 1024 * 1024:
            raise SystemExit("A single ZIP member is larger than 100 MiB")
    z.extractall(dest)
PY

MAKEFILE="$(find "$WORK/project" -type f -name Makefile -print -quit)"
if [[ -z "$MAKEFILE" ]]; then
  echo "ERROR: No Makefile found in the ZIP."
  exit 2
fi
PROJECT_DIR="$(dirname "$MAKEFILE")"
cd "$PROJECT_DIR"

sudo apt-get update
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y \
  build-essential clang git perl make python3 curl ca-certificates \
  dpkg-dev fakeroot ldid xz-utils unzip libplist-dev

# Install Theos into a runner-local path.
export THEOS="$HOME/theos"
if [[ ! -d "$THEOS" ]]; then
  git clone --depth=1 https://github.com/theos/theos.git "$THEOS"
fi
export PATH="$THEOS/bin:$PATH"

# Fetch public SDKs. Some projects need a specific SDK version; adjust this block as needed.
git clone --depth=1 https://github.com/theos/sdks.git "$HOME/theos-sdks"
mkdir -p "$THEOS/sdks"
find "$HOME/theos-sdks" -maxdepth 1 -type d -name 'iPhoneOS*.sdk' -exec cp -R {} "$THEOS/sdks/" \;

# Install/update theos toolchain where supported by the current upstream installer.
if [[ -x "$THEOS/bin/bootstrap.sh" ]]; then
  "$THEOS/bin/bootstrap.sh" || true
fi

echo "Using project directory: $PROJECT_DIR"
echo "THEOS=$THEOS"
echo "Available SDKs:"
find "$THEOS/sdks" -maxdepth 1 -mindepth 1 -type d -printf '%f\n' || true

# Build only; do not run any produced binaries.
make clean package FINALPACKAGE=1

mkdir -p "$OUT"
find packages -maxdepth 1 -type f -name '*.deb' -exec cp {} "$OUT/" \; 2>/dev/null || true
find . -maxdepth 3 -type f -name '*.deb' -exec cp -n {} "$OUT/" \; 2>/dev/null || true
if ! find "$OUT" -maxdepth 1 -type f -name '*.deb' | grep -q .; then
  echo "ERROR: make finished but no .deb package was found."
  exit 3
fi
