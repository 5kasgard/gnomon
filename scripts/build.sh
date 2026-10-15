#!/usr/bin/env bash
# Build the Rust core and place the shared library inside the Python package.
set -euo pipefail
cd "$(dirname "$0")/.."
cargo build --release --offline
case "$(uname -s)" in
  Darwin) lib=libgnomon_core.dylib ;;
  MINGW*|MSYS*|CYGWIN*) lib=gnomon_core.dll ;;
  *) lib=libgnomon_core.so ;;
esac
cp "target/release/$lib" "python/gnomon/$lib"
echo "built python/gnomon/$lib"
