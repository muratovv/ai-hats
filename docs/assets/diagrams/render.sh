#!/usr/bin/env bash
# Render ai-hats architecture diagrams from .d2 sources.
#
# Requires:
#   - d2          (brew install d2)
#   - python3     (system) with fonttools (auto-installed in a tmp venv)
#   - Source Code Pro variable TTFs in ~/Library/Fonts (Adobe variable font;
#     install from https://github.com/adobe-fonts/source-code-pro/releases)
#
# Usage:
#   bash docs/assets/diagrams/render.sh                  # render all *.d2
#   bash docs/assets/diagrams/render.sh session-lifecycle  # render one
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
VAR_REGULAR="$HOME/Library/Fonts/SourceCodePro[wght].ttf"
VAR_ITALIC="$HOME/Library/Fonts/SourceCodePro-Italic[wght].ttf"
CACHE="${TMPDIR:-/tmp}/ai-hats-fonts"

if [[ ! -f "$VAR_REGULAR" || ! -f "$VAR_ITALIC" ]]; then
  echo "error: Source Code Pro variable TTFs not found in ~/Library/Fonts" >&2
  echo "       grab from https://github.com/adobe-fonts/source-code-pro/releases" >&2
  exit 1
fi

if ! command -v d2 >/dev/null 2>&1; then
  echo "error: d2 not in PATH (brew install d2)" >&2
  exit 1
fi

mkdir -p "$CACHE"

extract_weight() {
  local src="$1" wght="$2" out="$3"
  if [[ -f "$out" && "$out" -nt "$src" ]]; then return; fi
  local venv="$CACHE/.venv"
  if [[ ! -d "$venv" ]]; then
    python3 -m venv "$venv"
    "$venv/bin/pip" install -q fonttools >/dev/null
  fi
  "$venv/bin/python" - <<PY
from fontTools.varLib.instancer import instantiateVariableFont
from fontTools.ttLib import TTFont
f = TTFont("$src")
inst = instantiateVariableFont(f, {"wght": $wght})
inst.save("$out")
PY
}

extract_weight "$VAR_REGULAR" 500 "$CACHE/SourceCodePro-Medium.ttf"
extract_weight "$VAR_REGULAR" 600 "$CACHE/SourceCodePro-SemiBold.ttf"
extract_weight "$VAR_ITALIC"  500 "$CACHE/SourceCodePro-MediumItalic.ttf"

render() {
  local stem="$1"
  local src="$HERE/$stem.d2"
  local svg="$HERE/$stem.svg"
  [[ -f "$src" ]] || { echo "skip: $src not found"; return; }
  d2 --sketch --pad=20 \
     --font-regular "$CACHE/SourceCodePro-Medium.ttf" \
     --font-italic  "$CACHE/SourceCodePro-MediumItalic.ttf" \
     --font-bold    "$CACHE/SourceCodePro-SemiBold.ttf" \
     "$src" "$svg"
}

if [[ $# -eq 0 ]]; then
  for f in "$HERE"/*.d2; do
    stem="$(basename "$f" .d2)"
    render "$stem"
  done
else
  for stem in "$@"; do
    render "${stem%.d2}"
  done
fi
