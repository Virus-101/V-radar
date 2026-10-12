#!/usr/bin/env bash
# ===========================================================================
#  V-Radar — full verification suite
#
#  Runs every self-test in the repository and reports pass/fail. This is what
#  the claims in BRAINSHEET.md §7 are backed by: if this script is green, the
#  "verified" column is true.
#
#  Usage:
#      bash run_all_tests.sh              # everything
#      bash run_all_tests.sh --quick      # skip the slow simulation matrix
#
#  Requires: python3 with numpy. The simulation needs Chrome (optional).
# ===========================================================================

set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$HERE"

QUICK=0
[ "${1:-}" = "--quick" ] && QUICK=1

PASS=0
FAIL=0
SKIP=0
declare -a FAILED

# --- interpreter with numpy -------------------------------------------------
PY="python"
if ! "$PY" -c "import numpy" >/dev/null 2>&1; then
    for cand in python3 py; do
        if command -v "$cand" >/dev/null 2>&1 && \
           "$cand" -c "import numpy" >/dev/null 2>&1; then
            PY="$cand"; break
        fi
    done
fi
if ! "$PY" -c "import numpy" >/dev/null 2>&1; then
    echo "ERROR: no Python with numpy found."
    echo "       Install it:  $PY -m pip install numpy"
    exit 2
fi
echo "interpreter: $($PY -c 'import sys;print(sys.version.split()[0])')"
echo "numpy:       $($PY -c 'import numpy;print(numpy.__version__)')"
echo

# --- runner -----------------------------------------------------------------
# check <label> <expected-substring> <workdir> <command...>
check() {
    local label="$1"; shift
    local expect="$1"; shift
    local dir="$1"; shift
    local out
    out="$(cd "$dir" && "$@" 2>&1)"
    if printf '%s' "$out" | grep -qF "$expect"; then
        printf '  \033[92mPASS\033[0m  %-34s %s\n' "$label" "$expect"
        PASS=$((PASS + 1))
    else
        printf '  \033[91mFAIL\033[0m  %-34s expected: %s\n' "$label" "$expect"
        printf '%s\n' "$out" | tail -6 | sed 's/^/          | /'
        FAIL=$((FAIL + 1))
        FAILED+=("$label")
    fi
}

skip() {
    printf '  \033[93mSKIP\033[0m  %-34s %s\n' "$1" "$2"
    SKIP=$((SKIP + 1))
}

# ===========================================================================
echo "=== ADS-B / MLAT  (system A, A+) ==="
# ===========================================================================
check "adsb decoder"            "all checks passed"  adsb  "$PY" decoder.py
check "mlat solver"             "all solver checks passed" adsb "$PY" mlat.py --demo
check "position fusion"         "10/10 checks passed" adsb "$PY" position_fusion.py
check "pps clock monitor"       "5/5 clock modes classified correctly" adsb \
      "$PY" pps_monitor.py --demo
check "adsb receiver"           "ok"                 adsb  "$PY" receiver.py
check "bringup (software side)" "software checks only" adsb "$PY" bringup.py --skip-dongle

# ===========================================================================
echo
echo "=== Detection / optics  (system C) ==="
# ===========================================================================
check "wingbeat classifier"     "12/12 passed"       detection "$PY" wingbeat.py

# ===========================================================================
echo
echo "=== Insect radar  (system B) ==="
# ===========================================================================
check "radar trigger"           "11/11 checks passed" system "$PY" radar_trigger.py

# ===========================================================================
echo
echo "=== Integrated system ==="
# ===========================================================================
check "system unit tests"       "passed"             system "$PY" -m pytest test_system.py -q

# ===========================================================================
echo
echo "=== Standalone desktop build ==="
# ===========================================================================
DESKTOP="$HERE/../Desktop/ADSB_Monitor/ADSB_Monitor.py"
if [ -f "$DESKTOP" ]; then
    check "standalone monitor"  "all checks passed"  "$(dirname "$DESKTOP")" \
          "$PY" ADSB_Monitor.py --selftest
else
    skip "standalone monitor" "(not present beside this repo)"
fi

# ===========================================================================
echo
echo "=== Simulation  (p5.js) ==="
# ===========================================================================
if [ "$QUICK" = "1" ]; then
    skip "simulation matrix" "--quick"
else
    CHROME="${CHROME:-/c/Program Files/Google/Chrome/Application/chrome.exe}"
    if [ -f "$CHROME" ] || command -v google-chrome >/dev/null 2>&1; then
        out="$(cd simulation && bash run_tests.sh 22 1280,800 2>/dev/null)"
        if printf '%s' "$out" | grep -q "accuracy=100%"; then
            printf '  \033[92mPASS\033[0m  %-34s accuracy=100%%\n' "simulation matrix"
            PASS=$((PASS + 1))
        else
            printf '  \033[91mFAIL\033[0m  %-34s no 100%% accuracy result\n' "simulation matrix"
            FAIL=$((FAIL + 1)); FAILED+=("simulation matrix")
        fi
    else
        skip "simulation matrix" "(Chrome not found; set CHROME=...)"
    fi
fi

# ===========================================================================
echo
echo "=== Python syntax across the tree ==="
# ===========================================================================
syn_out="$("$PY" - <<'PYEOF'
import ast, pathlib
bad = n = 0
for f in pathlib.Path(".").rglob("*.py"):
    if "__pycache__" in str(f):
        continue
    n += 1
    try:
        ast.parse(f.read_text(encoding="utf-8"))
    except SyntaxError:
        bad += 1
        print("SYNTAX ERROR:", f)
print(f"{n} files, {bad} errors")
PYEOF
)"
if printf '%s' "$syn_out" | grep -q " 0 errors"; then
    printf '  \033[92mPASS\033[0m  %-34s %s\n' "syntax" "$syn_out"
    PASS=$((PASS + 1))
else
    printf '  \033[91mFAIL\033[0m  %-34s %s\n' "syntax" "$syn_out"
    FAIL=$((FAIL + 1)); FAILED+=("syntax")
fi

# ===========================================================================
echo
echo "==========================================================================="
printf '  %d passed, %d failed, %d skipped\n' "$PASS" "$FAIL" "$SKIP"
if [ "$FAIL" -gt 0 ]; then
    echo
    echo "  failed:"
    for f in "${FAILED[@]}"; do echo "    - $f"; done
fi
echo "==========================================================================="
[ "$FAIL" -eq 0 ] || exit 1
