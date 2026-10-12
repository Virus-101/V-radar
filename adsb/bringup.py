"""
Phase 1 bring-up — first contact with an RTL-SDR dongle.

A guided checklist that tells you exactly what is wrong when the hardware does
not work. The software suite proves the LOGIC; this proves the HARDWARE. They
are different exercises and this is the second one.

    python bringup.py                  # run every check, guided
    python bringup.py --lat 40.0 --lon 0.0
    python bringup.py --skip-dongle    # only the software-side checks

PASSIVE RECEIVE ONLY. rtl_adsb demodulates 1090 MHz; it does not transmit.

WHAT IT CHECKS
    1  Python + numpy present
    2  rtl_test finds a dongle
    3  the dongle can actually tune (sample-rate check)
    4  rtl_adsb produces frames        <- the real test
    5  frames pass CRC
    6  aircraft appear and get positions
    7  range report vs the radio horizon

Each step prints what to do if it fails, because the failure modes are specific:
no driver on Windows, no antenna, wrong gain, a dongle that enumerates but has a
dead tuner.
"""

from __future__ import annotations

import argparse
import math
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from decoder import Decoder, crc_ok, radio_horizon_km   # noqa: E402
from receiver import Tracker, RTLSDBSource              # noqa: E402

OK, WARN, FAIL = "PASS", "WARN", "FAIL"
results: list[tuple[str, str, str]] = []


def record(status: str, name: str, detail: str = "") -> None:
    colour = {OK: "\033[92m", WARN: "\033[93m", FAIL: "\033[91m"}.get(status, "")
    reset = "\033[0m" if colour else ""
    print(f"  {colour}{status:4s}{reset}  {name}" + (f"   {detail}" if detail else ""))
    results.append((status, name, detail))


def hint(*lines: str) -> None:
    for l in lines:
        print(f"          {l}")


def run(cmd: list[str], timeout: int = 30) -> tuple[int, str]:
    """Run a tool, return (exit_code, combined_output)."""
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except FileNotFoundError:
        return 127, f"{cmd[0]}: not found"
    except subprocess.TimeoutExpired:
        return 124, f"{cmd[0]}: timed out"


# ---------------------------------------------------------------------------
# Steps
# ---------------------------------------------------------------------------

def step_python() -> bool:
    print("\n[1/7] Python environment")
    try:
        import numpy
        record(OK, "numpy present", f"v{numpy.__version__}")
        record(OK, "python", sys.version.split()[0])
        return True
    except ImportError:
        record(FAIL, "numpy missing")
        hint(f"{sys.executable} -m pip install -r requirements-dev.txt")
        return False


def step_dongle() -> bool:
    print("\n[2/7] Dongle detected")
    if shutil.which("rtl_test") is None:
        record(FAIL, "rtl_test not found")
        hint("Linux:   sudo apt install rtl-sdr",
             "macOS:   brew install librtlsdr",
             "Windows: install the RTL-SDR driver via Zadig (zadig.akeo.ie),",
             "         then put rtl_adsb.exe and rtl_test.exe on PATH")
        return False

    code, out = run(["rtl_test", "-t"], timeout=20)
    if "No supported devices found" in out or "usb_open error" in out:
        record(FAIL, "no device found")
        hint("Is the dongle plugged in?",
             "Windows: the driver is probably still the default DVB one -",
             "         run Zadig and replace it with WinUSB.",
             "Linux:   check `lsusb` shows Realtek, and that no kernel DVB",
             "         driver (dvb_usb_rtl28xxu) has claimed it:")
        hint("         sudo rmmod dvb_usb_rtl28xxu rtl2832")
        return False
    if "Found 1 device" in out or "Found" in out:
        first = next((l.strip() for l in out.splitlines() if "Found" in l), "")
        record(OK, "device found", first)
        return True
    record(WARN, "rtl_test inconclusive", out.splitlines()[0] if out else "")
    return True


def step_tune() -> bool:
    print("\n[3/7] Tuner responds")
    code, out = run(["rtl_test", "-s", "2400000"], timeout=25)
    if code == 124 or "Lost at least" in out:
        # a few dropped samples at 2.4 MSPS is normal on USB 2.0
        record(WARN, "sample drops at 2.4 MSPS",
               "normal on USB 2.0; not fatal for ADS-B")
        return True
    if "No supported devices" in out:
        record(FAIL, "tuner did not open")
        return False
    record(OK, "tuned to 2.4 MSPS")
    return True


def step_frames(seconds: float = 15.0) -> tuple[bool, list[str]]:
    print(f"\n[4/7] Receiving frames  ({seconds:.0f}s, this is the real test)")
    if shutil.which("rtl_adsb") is None:
        record(FAIL, "rtl_adsb not found")
        hint("Same package as rtl_test. See step 2.")
        return False, []

    frames: list[str] = []
    try:
        p = subprocess.Popen(["rtl_adsb"], stdout=subprocess.PIPE,
                             stderr=subprocess.DEVNULL, text=True, bufsize=1)
    except FileNotFoundError:
        record(FAIL, "could not start rtl_adsb")
        return False, []

    t0 = time.time()
    try:
        assert p.stdout is not None
        for line in p.stdout:
            line = line.strip().lstrip("*;")
            if len(line) >= 28:
                frames.append(line[:28])
            if time.time() - t0 > seconds:
                break
    finally:
        p.terminate()
        try:
            p.wait(timeout=3)
        except subprocess.TimeoutExpired:
            p.kill()

    n = len(frames)
    rate = n / max(time.time() - t0, 0.1)
    if n == 0:
        record(FAIL, "no frames in 15 s")
        hint("The dongle works but nothing is being heard. In order of likelihood:",
             "  1. Antenna not connected, or a bad SMA joint",
             "  2. No aircraft in range - try near a window or outdoors",
             "  3. Gain too low: rtl_adsb -g 42.4   (try 30-49 dB)",
             "  4. Wrong band - check the antenna is 1090 MHz, not 978 MHz")
        return False, []
    if n < 20:
        record(WARN, "few frames", f"{n} in {seconds:.0f}s ({rate:.1f}/s)")
        hint("Receiving, but weakly. Raise the gain and the antenna:")
        hint("  rtl_adsb -g 42.4")
        return True, frames
    record(OK, "receiving frames", f"{n} in {seconds:.0f}s ({rate:.1f}/s)")
    return True, frames


def step_crc(frames: list[str]) -> bool:
    print("\n[5/7] Frame integrity")
    if not frames:
        record(WARN, "no frames to check")
        return False
    good = sum(1 for f in frames if crc_ok(bytes.fromhex(f)))
    pct = 100.0 * good / len(frames)
    if pct >= 80:
        record(OK, "CRC clean", f"{good}/{len(frames)} ({pct:.0f}%)")
        return True
    if pct >= 30:
        record(WARN, "some CRC failures", f"{good}/{len(frames)} ({pct:.0f}%)")
        hint("Normal in a busy RF environment. If it is very low:")
        hint("  add a 1090 MHz SAW filter - strong out-of-band signals")
        hint("  (cell towers) desensitise the front end")
        return True
    record(FAIL, "most frames corrupt", f"{good}/{len(frames)} ({pct:.0f}%)")
    hint("Almost certainly RF overload or a bad cable. Try:")
    hint("  lower the gain:  rtl_adsb -g 20")
    hint("  add a filter, or move away from a transmitter")
    return False


def step_positions(frames: list[str], lat: float, lon: float) -> bool:
    print("\n[6/7] Positions resolved")
    if not frames:
        record(WARN, "no frames to decode")
        return False
    dec = Decoder()
    now = time.time()
    for f in frames:
        dec.feed_hex(f, now=now)
    positioned = [a for a in dec.aircraft.values() if a.lat is not None]
    if not positioned:
        record(WARN, "no positions yet",
               f"{len(dec.aircraft)} aircraft heard, 0 with a position")
        hint("CPR needs an EVEN and an ODD frame from the same aircraft.",
             "15 s is often too short. Run longer:")
        hint("  rtl_adsb > capture.txt      # leave it 2-3 minutes")
        hint("  python desktop.py --file capture.txt")
        return False
    with_cs = sum(1 for a in positioned if a.callsign)
    record(OK, "positions resolved",
           f"{len(positioned)} aircraft ({with_cs} with callsigns)")
    return True


def step_range(lat: float, lon: float, frames: list[str]) -> None:
    print("\n[7/7] Range check")
    if not frames:
        record(WARN, "no data for a range report")
        return
    dec = Decoder()
    now = time.time()
    for f in frames:
        dec.feed_hex(f, now=now)
    from decoder import haversine_km
    best = 0.0
    best_alt = 0.0
    for a in dec.aircraft.values():
        if a.lat is None or a.altitude_ft is None:
            continue
        d = haversine_km(lat, lon, a.lat, a.lon)
        if d > best:
            best, best_alt = d, a.altitude_ft
    if best == 0:
        record(WARN, "no ranged aircraft")
        return
    horizon = radio_horizon_km(best_alt)
    pct = 100.0 * best / horizon if horizon else 0
    record(OK, "furthest aircraft",
           f"{best:.0f} km at {best_alt:,.0f} ft  "
           f"(horizon at that altitude: {horizon:.0f} km, {pct:.0f}%)")
    if pct > 85:
        hint("You are near the radio horizon limit - excellent antenna/height.")
    elif pct > 50:
        hint("Solid. To gain more: raise the antenna (every doubling = +41% range).")
    else:
        hint("Well short of the horizon - the limit is your antenna, not geometry.")
        hint("  raise it, or add a 1090 MHz filter/LNA")


# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--lat", type=float, default=0.0, help="your latitude")
    ap.add_argument("--lon", type=float, default=0.0, help="your longitude")
    ap.add_argument("--seconds", type=float, default=15.0,
                    help="capture window for the receive test")
    ap.add_argument("--skip-dongle", action="store_true",
                    help="software checks only")
    args = ap.parse_args()

    print("=" * 74)
    print("V-RADAR  PHASE 1 BRING-UP")
    print("  passive 1090 MHz receive - no transmitter")
    print("=" * 74)

    if args.lat == 0.0 and args.lon == 0.0:
        print("\n  note: using lat/lon 0,0 - pass --lat/--lon for a range report")

    frames: list[str] = []
    if not step_python():
        return 2

    if args.skip_dongle:
        print("\n[2-7/7] skipped (--skip-dongle)")
        print("\n" + "=" * 74)
        print("  software checks only - nothing about your hardware was tested")
        print("=" * 74)
        return 0

    if not step_dongle():
        return 2
    step_tune()
    ok, frames = step_frames(args.seconds)
    step_crc(frames)
    step_positions(frames, args.lat, args.lon)
    step_range(args.lat, args.lon, frames)

    print("\n" + "=" * 74)
    n_fail = sum(1 for s, _, _ in results if s == FAIL)
    n_warn = sum(1 for s, _, _ in results if s == WARN)
    n_ok = sum(1 for s, _, _ in results if s == OK)
    print(f"  {n_ok} passed, {n_warn} warnings, {n_fail} failed")
    if n_fail:
        print("  Fix the failures above, then re-run.")
    elif n_warn:
        print("  Working. The warnings are range/quality tuning, not blockers.")
    else:
        print("  Hardware is working. Start the monitor:")
        print("    python desktop.py --rtl --lat <your-lat> --lon <your-lon>")
    print("=" * 74)
    return 1 if n_fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
