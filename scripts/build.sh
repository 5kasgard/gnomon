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
# Copy then rename: a running process (an MCP server, a hook) keeps the
# library it loaded; overwriting that file in place would crash it.
cp "target/release/$lib" "python/gnomon/$lib.tmp"
mv -f "python/gnomon/$lib.tmp" "python/gnomon/$lib"
echo "built python/gnomon/$lib"
