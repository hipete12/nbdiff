#!/usr/bin/env bash
# Install nbdiff from this checkout by symlinking bin/nbdiff into a directory
# on your PATH.  Usage:  ./install.sh [BIN_DIR]     (default: ~/.local/bin)
# Uninstall with:        rm BIN_DIR/nbdiff
set -euo pipefail

bin_dir="${1:-$HOME/.local/bin}"
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
target="$bin_dir/nbdiff"

command -v python3 >/dev/null || { echo "nbdiff needs python3 (3.9+) on PATH" >&2; exit 1; }

mkdir -p "$bin_dir"
if [[ -e "$target" && ! -L "$target" ]]; then
    echo "error: $target already exists and is not a symlink (nbdime's nbdiff?)." >&2
    echo "       Remove it or pass another directory: ./install.sh ~/bin" >&2
    exit 1
fi
ln -sfn "$here/bin/nbdiff" "$target"
echo "installed $target -> $here/bin/nbdiff"

case ":$PATH:" in
    *":$bin_dir:"*) ;;
    *) echo "note: $bin_dir is not on your PATH; add:  export PATH=\"$bin_dir:\$PATH\"" ;;
esac
first="$(command -v nbdiff || true)"
if [[ -n "$first" && "$(readlink -f "$first")" != "$(readlink -f "$here/bin/nbdiff")" ]]; then
    echo "warning: '$first' comes first on PATH and will shadow this nbdiff" >&2
fi
echo "bash completion:  echo 'source $here/completions/nbdiff.bash' >> ~/.bashrc"
