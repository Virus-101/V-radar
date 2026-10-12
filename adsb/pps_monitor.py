"""
GPSDO PPS discipline monitor — the acceptance test for an MLAT station.

Before you buy a second receiver, prove the FIRST one has a clock good enough to
multilaterate with. A station whose PPS wanders by a microsecond poisons every fix
in the network, and a bad station is worse than no station.

    python pps_monitor.py --demo           # synthesise clocks, see pass/fail
    python pps_monitor.py --simulate bad   # what a failing station looks like
    python pps_monitor.py --serial COM3    # live from an RP2040 counting PPS

WHAT IT MEASURES
    A GPS-disciplined oscillator emits a 1 Hz pulse (PPS) aligned to UTC. Sampling
    the interval between edges gives the clock's real performance:

        interval = 1.000000000 s  -> perfect
        jitter   -> short-term noise, sets the MLAT floor
        drift    -> long-term error, worse: it biases the fix

    Range error is c * dt, so:
        10 ns  ->    3 m   (good MLAT)
        100 ns ->   30 m   (usable)
        1 us   ->  300 m   (marginal)
        1 ms   ->  300 km  (useless)

WHAT "PASS" MEANS
    For MLAT you need the ALLAN DEVIATION at 1 s to be under ~100 ns, and the
    drift over an hour under ~1 us. The tool reports both, plus a verdict.
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

C_LIGHT = 299792458.0
NOMINAL_INTERVAL_S = 1.0

#: thresholds, in seconds of clock error
PASS_JITTER_NS = 100.0
MARGINAL_JITTER_NS = 1000.0
PASS_DRIFT_NS_PER_S = 50.0


# ---------------------------------------------------------------------------
# Sample sources
# ---------------------------------------------------------------------------

class PPSSource:
    """Yields (timestamp_s, sequence) for each PPS edge."""

    name = "source"

    def edges(self):
        raise NotImplementedError

    def close(self):
        pass


class SerialPPSource(PPSSource):
    """Live PPS edges from a microcontroller over serial.

    Firmware contract (matches the RP2040 sketch in the MLAT build doc): the MCU
    counts microseconds against a free-running timer and prints one line per PPS
    edge:

        PPS,<seq>,<t_us>

    where t_us is the MCU clock reading at the edge. The MCU is clocked from the
    GPSDO's 10 MHz reference, so t_us is in disciplined microseconds. We compare
    consecutive readings against the nominal 1,000,000 us interval.

    Using the MCU's own counter (not the host clock) is the whole point: a host
    timestamp carries USB jitter of 1-10 ms, which is 300-3000 km of range error.
    """

    name = "serial"

    def __init__(self, port: str, baud: int = 115200):
        import serial
        self.ser = serial.Serial(port, baud, timeout=2.0)
        self.name = f"serial:{port}"

    def edges(self):
        while True:
            line = self.ser.readline().decode("ascii", errors="ignore").strip()
            if not line:
                continue
            parts = line.split(",")
            if len(parts) != 3 or parts[0] != "PPS":
                continue
            try:
                yield (int(parts[2]) / 1e6, int(parts[1]))
            except ValueError:
                continue

    def close(self):
        try:
            self.ser.close()
        except Exception:
            pass


class DemoPPSource(PPSSource):
    """Synthesises PPS edges so the analysis can be validated with no hardware.

    Modes model the real failure modes:

        good      ~10 ns jitter, no drift          -> PASS
        marginal  ~500 ns jitter                   -> MARGINAL
        bad       ~5 us jitter + drift             -> FAIL
        drift     clean but losing lock slowly     -> FAIL on drift only
        unlocked  free-running, no PPS discipline  -> FAIL badly
    """

    def __init__(self, mode: str = "good", n: int = 600, seed: int = 3,
                 realtime: bool = False):
        self.mode = mode
        self.n = n
        self.realtime = realtime
        self.name = f"demo:{mode}"
        import random
        self.rng = random.Random(seed)

    def edges(self):
        r = self.rng
        t = 0.0
        for i in range(self.n):
            if self.mode == "good":
                err = r.gauss(0, 10e-9)
            elif self.mode == "marginal":
                err = r.gauss(0, 500e-9)
            elif self.mode == "bad":
                err = r.gauss(0, 5e-6) + i * 2e-8      # jitter + drift
            elif self.mode == "drift":
                err = r.gauss(0, 20e-9) + i * 5e-7     # clean but drifting
            elif self.mode == "unlocked":
                err = r.gauss(0, 2e-3) + i * 1e-5
            else:
                err = 0.0
            yield (t + err, i)
            t += NOMINAL_INTERVAL_S
            if self.realtime:
                time.sleep(0.05)          # speed up for a demo


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------

@dataclass
class PPSReport:
    n_edges: int
    duration_s: float
    #: interval statistics, seconds
    mean_interval: float
    jitter_ns: float                 # std dev of interval error
    p95_ns: float
    max_ns: float
    #: total frequency error, ns per second (mean offset + slope). This is the
    #: number that accumulates: 500 ns/s is 1.8 ms/hour.
    drift_ns_per_s: float
    #: rate of change of the frequency error, ns/s per s (losing lock)
    slope_ns_per_s2: float
    #: total accumulated error at the end, seconds
    accumulated_s: float
    #: Allan deviation at tau = 1 s (the standard clock metric)
    adev_1s: float
    #: implied MLAT range error, metres
    range_error_m: float
    slope_ns_per_s2: float
    verdict: str
    reasons: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "n_edges": self.n_edges,
            "duration_s": round(self.duration_s, 2),
            "mean_interval_s": self.mean_interval,
            "jitter_ns": round(self.jitter_ns, 1),
            "p95_ns": round(self.p95_ns, 1),
            "max_ns": round(self.max_ns, 1),
            "drift_ns_per_s": round(self.drift_ns_per_s, 3),
            "slope_ns_per_s2": round(self.slope_ns_per_s2, 4),
            "accumulated_us": round(self.accumulated_s * 1e6, 3),
            "adev_1s_ns": round(self.adev_1s * 1e9, 1),
            "range_error_m": round(self.range_error_m, 1),
            "verdict": self.verdict,
            "reasons": self.reasons,
        }


def analyse(edges: list[tuple[float, int]]) -> PPSReport:
    """Turn a list of (timestamp, seq) PPS edges into a clock report."""
    if len(edges) < 10:
        raise ValueError("need at least 10 PPS edges to characterise a clock")

    ts = [t for t, _ in edges]
    intervals = [ts[i + 1] - ts[i] for i in range(len(ts) - 1)]
    errs = [iv - NOMINAL_INTERVAL_S for iv in intervals]      # seconds

    n = len(errs)
    mean_err = statistics.fmean(errs)
    jitter = statistics.pstdev(errs) if n > 1 else 0.0

    se = sorted(errs)
    p95 = se[int(0.95 * (n - 1))]
    mx = max(abs(e) for e in errs)

    # Clock error has TWO components and they need separate metrics:
    #
    #   offset  = the MEAN interval error. A constant +500 ns/s is a 0.5 ppm
    #             frequency error: it does not show up as a slope, but it
    #             accumulates linearly, so after an hour the clock is 1.8 ms
    #             out. This is the dangerous one.
    #   drift   = the SLOPE of interval error over time. Non-zero means the
    #             frequency is changing (losing lock).
    #
    # Reporting only the slope (as this originally did) missed a constant
    # frequency offset entirely, and classified a 300 us/600 s error as PASS.
    xs = list(range(n))
    mx_ = statistics.fmean(xs)
    num = sum((x - mx_) * (y - mean_err) for x, y in zip(xs, errs))
    den = sum((x - mx_) ** 2 for x in xs) or 1.0
    slope = num / den                                          # s per s (drift rate)

    # total frequency error = mean offset + slope, i.e. the error now vs at start
    freq_error = mean_err + slope * (n / 2.0)                  # s per s

    # use the frequency error as "drift" for the verdict: it is what accumulates
    drift = freq_error

    # accumulated error: how far off we are after the whole run
    accumulated = sum(errs)

    # Allan deviation at tau = 1 s: half the mean squared difference of
    # consecutive interval errors. This is THE standard clock stability metric -
    # it converges for flicker/random-walk noise where plain std dev does not.
    if n > 1:
        diffs = [errs[i + 1] - errs[i] for i in range(n - 1)]
        adev = math.sqrt(sum(d * d for d in diffs) / (2.0 * (n - 1)))
    else:
        adev = 0.0

    # implied MLAT range error: dominated by the worse of jitter and drift
    range_error = C_LIGHT * max(jitter, abs(drift))

    reasons: list[str] = []
    jitter_ns = jitter * 1e9
    drift_ns = drift * 1e9

    if jitter_ns <= PASS_JITTER_NS:
        verdict = "PASS"
    elif jitter_ns <= MARGINAL_JITTER_NS:
        verdict = "MARGINAL"
        reasons.append(f"jitter {jitter_ns:.0f} ns is above the {PASS_JITTER_NS:.0f} ns "
                       f"target for good MLAT")
    else:
        verdict = "FAIL"
        reasons.append(f"jitter {jitter_ns:.0f} ns is far too high - "
                       f"this station will poison every fix")

    if abs(drift_ns) > PASS_DRIFT_NS_PER_S:
        if verdict == "PASS":
            verdict = "MARGINAL"
        reasons.append(
            f"frequency error {drift_ns:+.1f} ns/s "
            f"({drift_ns * 3600:+,.0f} ns/hour, "
            f"{drift_ns * 3600 / 1000:+,.1f} us/hour) - accumulates linearly")
        if abs(drift_ns) * 3600 > 5000:
            verdict = "FAIL"
            reasons.append("frequency error exceeds 5 us/hour: unusable for MLAT")

    if abs(accumulated) > 1e-5:
        if verdict == "PASS":
            verdict = "MARGINAL"
        reasons.append(f"accumulated error {accumulated * 1e6:+.1f} us over the run")

    return PPSReport(
        n_edges=len(edges),
        duration_s=ts[-1] - ts[0] if ts else 0.0,
        mean_interval=NOMINAL_INTERVAL_S + mean_err,
        jitter_ns=jitter_ns,
        p95_ns=p95 * 1e9,
        max_ns=mx * 1e9,
        drift_ns_per_s=drift_ns,
        slope_ns_per_s2=slope * 1e9,
        accumulated_s=accumulated,
        adev_1s=adev,
        range_error_m=range_error,
        verdict=verdict,
        reasons=reasons,
    )


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

VERDICT_COLOUR = {"PASS": "\033[92m", "MARGINAL": "\033[93m", "FAIL": "\033[91m"}
RESET = "\033[0m"


def print_report(rep: PPSReport, colour: bool = True) -> None:
    def c(v: str, key: str) -> str:
        if not colour:
            return v
        return f"{VERDICT_COLOUR.get(key, '')}{v}{RESET}"

    print("=" * 74)
    print("GPSDO PPS DISCIPLINE REPORT")
    print("=" * 74)
    print(f"  edges sampled          {rep.n_edges}")
    print(f"  duration               {rep.duration_s:.1f} s")
    print(f"  mean interval          {rep.mean_interval:.9f} s  "
          f"({(rep.mean_interval - 1.0) * 1e9:+.1f} ns from nominal)")
    print()
    print(f"  jitter (1 sigma)       {rep.jitter_ns:8.1f} ns")
    print(f"  95th percentile        {rep.p95_ns:8.1f} ns")
    print(f"  worst single interval  {rep.max_ns:8.1f} ns")
    print(f"  Allan deviation @ 1s   {rep.adev_1s * 1e9:8.1f} ns")
    print(f"  frequency error        {rep.drift_ns_per_s:+8.1f} ns/s "
          f"({rep.drift_ns_per_s * 3600:+,.0f} ns/hour)")
    print(f"  frequency slope        {rep.slope_ns_per_s2:+8.4f} ns/s per s"
          f"   {'(losing lock)' if abs(rep.slope_ns_per_s2) > 0.01 else ''}")
    print(f"  accumulated error      {rep.accumulated_s * 1e6:+8.1f} us "
          f"({rep.accumulated_s * 1e6 / max(rep.duration_s, 1e-9) * 3600:+,.1f} us/hour)")
    print()
    print(f"  implied MLAT range error  {rep.range_error_m:.1f} m")
    print()
    print(f"  VERDICT:  {c(rep.verdict, rep.verdict)}")
    for r in rep.reasons:
        print(f"     - {r}")
    if rep.verdict == "PASS":
        print("     this station is good enough to multilaterate with")
    elif rep.verdict == "MARGINAL":
        print("     usable for coarse fixes; expect degraded accuracy")
    else:
        print("     do NOT add this station to a network")
    print("=" * 74)


def _demo() -> int:
    print("PPS discipline monitor - validating the analysis against known clocks")
    print()
    print(f"{'mode':10s} {'jitter':>10s} {'drift':>11s} {'adev@1s':>10s} "
          f"{'range err':>11s}  {'verdict':>9s}  expected")
    print("-" * 90)

    expect = {"good": "PASS", "marginal": "MARGINAL", "bad": "FAIL",
              "drift": "FAIL", "unlocked": "FAIL"}
    fails = 0
    reports = {}
    for mode in ("good", "marginal", "bad", "drift", "unlocked"):
        edges = list(DemoPPSource(mode, n=600).edges())
        rep = analyse(edges)
        reports[mode] = rep
        ok = rep.verdict == expect[mode]
        if not ok:
            fails += 1
        print(f"{mode:10s} {rep.jitter_ns:9.1f}n {rep.drift_ns_per_s:+10.2f}n "
              f"{rep.adev_1s * 1e9:9.1f}n {rep.range_error_m:10.1f}m  "
              f"{rep.verdict:>9s}  {expect[mode]}"
              + ("" if ok else "   <-- MISMATCH"))

    print("-" * 90)
    # the ordering must hold: good < marginal < bad in range error
    order = [reports[m].range_error_m for m in ("good", "marginal", "bad")]
    if not (order[0] < order[1] < order[2]):
        fails += 1
        print("! range error is not monotonic with clock quality - analysis is wrong")
    else:
        print(f"range error scales correctly: "
              f"{order[0]:.0f} m < {order[1]:.0f} m < {order[2]:.0f} m")

    print()
    print_report(reports["good"])
    print()
    print_report(reports["drift"])

    print(f"\n{5 - fails}/5 clock modes classified correctly")
    return 1 if fails else 0


def _live(port: str, seconds: float, out: Optional[str]) -> int:
    src = SerialPPSource(port)
    print(f"reading PPS from {port} for {seconds:.0f} s ...")
    edges: list[tuple[float, int]] = []
    t0 = time.time()
    try:
        for ts, seq in src.edges():
            edges.append((ts, seq))
            if time.time() - t0 >= seconds:
                break
    finally:
        src.close()
    rep = analyse(edges)
    print_report(rep)
    if out:
        Path(out).write_text(json.dumps(rep.as_dict(), indent=2), encoding="utf-8")
        print(f"report written to {out}")
    return 0 if rep.verdict != "FAIL" else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="GPSDO PPS discipline monitor")
    ap.add_argument("--demo", action="store_true",
                    help="validate the analysis against known clock modes")
    ap.add_argument("--simulate", choices=["good", "marginal", "bad", "drift",
                                           "unlocked"],
                    help="print a full report for a synthetic clock")
    ap.add_argument("--serial", help="serial port for live PPS (e.g. COM3)")
    ap.add_argument("--seconds", type=float, default=300.0,
                    help="live capture duration (default 300)")
    ap.add_argument("--out", help="write the report as JSON")
    args = ap.parse_args()

    if args.simulate:
        edges = list(DemoPPSource(args.simulate, n=600).edges())
        rep = analyse(edges)
        print_report(rep)
        if args.out:
            Path(args.out).write_text(json.dumps(rep.as_dict(), indent=2),
                                      encoding="utf-8")
            print(f"report written to {args.out}")
        return 0 if rep.verdict != "FAIL" else 1
    if args.serial:
        return _live(args.serial, args.seconds, args.out)
    return _demo()


if __name__ == "__main__":
    raise SystemExit(main())
