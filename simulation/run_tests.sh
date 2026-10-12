#!/usr/bin/env bash
# Deterministic test harness for the mosquito zapper simulation.
#
# WHY THIS EXISTS
# ---------------
# Chrome's --virtual-time-budget does NOT advance requestAnimationFrame, which
# is what p5's draw loop runs on. A "30 second" headless run therefore executes
# only a handful of frames, and any measurement of zap rate taken that way is
# meaningless. This harness injects a fixed-dt stepper into the sketch so the
# simulation advances deterministically, independent of wall-clock or rAF.
#
# USAGE
#   bash run_tests.sh            # default matrix
#   bash run_tests.sh 22 1280,800
#
# REQUIREMENTS
#   Chrome at the path below (edit CHROME if yours differs), python3 for the
#   harness injection.

set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKETCH="$HERE/index.html"
CHROME="${CHROME:-/c/Program Files/Google/Chrome/Application/chrome.exe}"
TMP="${TMPDIR:-/tmp}"
HARNESS="$TMP/mz_harness.html"

# Native tools (python, chrome) cannot read MSYS paths like /c/Users/...
# Convert to Windows form for them, and remember a forward-slash Windows path
# for the file:// URL.
if command -v cygpath >/dev/null 2>&1; then
  SKETCH_WIN="$(cygpath -w "$SKETCH")"
  HARNESS_WIN="$(cygpath -w "$HARNESS")"
else
  SKETCH_WIN="$SKETCH"
  HARNESS_WIN="$HARNESS"
fi
# backslashes -> forward slashes for the URL
HARNESS_URL="$(printf '%s' "$HARNESS_WIN" | tr '\\' '/')"

if [ ! -f "$CHROME" ]; then
  echo "Chrome not found at: $CHROME" >&2
  echo "Set CHROME=/path/to/chrome and retry." >&2
  exit 1
fi

# --- inject the fixed-dt stepper into a copy of the sketch -------------------
python - "$SKETCH_WIN" "$HARNESS_WIN" <<'PY'
import pathlib, sys
src, dst = sys.argv[1], sys.argv[2]
s = pathlib.Path(src).read_text(encoding="utf-8")

# forceable dt
s = s.replace(
    "  let dt = min(deltaTime / 1000, 0.05);",
    "  let dt = (window.__forceDt !== undefined) ? window.__forceDt"
    " : min(deltaTime / 1000, 0.05);", 1)

# query-string controls
s = s.replace("<script>\n  // Fallback CDN",
    "<script>\n"
    "  window.__RUN_TEST = location.search.indexOf('test=1') >= 0;\n"
    "  (function(){\n"
    "    var mc = /[?&]mc=(\\d+)/.exec(location.search);\n"
    "    if (mc) window.__MC = parseInt(mc[1], 10);\n"
    "    var fr = /[?&]frames=(\\d+)/.exec(location.search);\n"
    "    if (fr) window.__FRAMES = parseInt(fr[1], 10);\n"
    "  })();\n"
    "  // Fallback CDN", 1)

# stepper at the end of setup(), after mosquitoes exist
tail = """  for (let i = 0; i < CONFIG.mosquitoCount; i++) {
    let m = new Mosquito();
    m.pos.set(random(width * 0.1, width * 0.9), random(height * 0.1, height * 0.7));
    mosquitoes.push(m);
  }
}"""
assert tail in s, "setup() tail not found - sketch changed shape?"
stepper = tail[:-2] + """
  if (window.__RUN_TEST) {
    noLoop();
    const N = window.__FRAMES || 1800;
    const DT = 1 / 60;
    let firstZap = -1, lockedFrames = 0, maxSimult = 0, detFrames = 0;
    for (let i = 0; i < N; i++) {
      window.__forceDt = DT;
      draw();
      if (firstZap < 0 && stats.zapped > 0) firstZap = i;
      if (lockedTarget) lockedFrames++;
      if (stats.detected > 0) detFrames++;
      maxSimult = Math.max(maxSimult, stats.detected);
    }
    const pre = document.createElement('pre');
    pre.id = 'RESULT';
    pre.textContent = 'RESULT frames=' + N
      + ' simSeconds=' + timeSec.toFixed(1)
      + ' zapped=' + stats.zapped
      + ' shots=' + stats.shots
      + ' maxDetected=' + maxSimult
      + ' detFrames=' + detFrames
      + ' accuracy=' + (stats.shots
          ? (100 * stats.zapped / stats.shots).toFixed(0) : '0') + '%'
      + ' firstZapFrame=' + firstZap
      + ' zapsPerSec=' + (stats.zapped / timeSec).toFixed(2)
      + ' lockedFrames=' + lockedFrames;
    document.body.appendChild(pre);
    return;
  }
}"""
s = s.replace(tail, stepper, 1)
s = s.replace("  for (let i = 0; i < CONFIG.mosquitoCount; i++) {",
    "  if (window.__MC) CONFIG.mosquitoCount = window.__MC;\n"
    "  for (let i = 0; i < CONFIG.mosquitoCount; i++) {", 1)
pathlib.Path(dst).write_text(s, encoding="utf-8")
print("harness written")
PY

# --- runner -----------------------------------------------------------------
run_case() {
  local count="$1" size="$2" frames="${3:-1800}"
  local out="$TMP/mz_dom.txt"
  "$CHROME" --headless=new --disable-gpu --no-sandbox \
    --window-size="$size" --virtual-time-budget=180000 --dump-dom \
    "file:///$HARNESS_URL?test=1&mc=$count&frames=$frames" >"$out" 2>/dev/null
  local line
  line="$(grep -o 'id="RESULT">[^<]*' "$out" | sed 's/id="RESULT">//')"
  if [ -z "$line" ]; then
    echo "count=$count size=$size  ->  FAILED (no result; check for JS errors)"
    return 1
  fi
  echo "count=$count size=$size  ->  $line"
}

if [ $# -eq 2 ]; then
  run_case "$1" "$2"
else
  echo "=== mosquito zapper deterministic test matrix ==="
  run_case 6   1280,800
  run_case 22  1280,800
  run_case 45  1280,800
  run_case 22  1024,640
  run_case 22  700,1000
fi
