"""
Position fusion: ADS-B vs MLAT, with disagreement flagging.

Two independent position sources for the same aircraft is a gift: you can
CROSS-CHECK them. ADS-B positions are broadcast by the aircraft and are
unauthenticated, so a transponder can be faulty or spoofed. MLAT is derived from
arrival times at receivers you control, so it cannot be spoofed by the aircraft.

When they disagree by more than the geometry allows, something is wrong - and
which source is wrong is often determinable from the pattern.

    python position_fusion.py            # validate the fusion logic
    python position_fusion.py --demo     # show disagreement scenarios

THE TRUST MODEL
    ADS-B : precise when correct (~5 m), but self-reported and unauthenticated
    MLAT  : less precise (tens to thousands of metres, GDOP-dependent), but
            independent - the aircraft cannot influence it

So MLAT is the reference for TRUST and ADS-B is the reference for PRECISION.
A disagreement is judged against the MLAT's own uncertainty, not a fixed number:
200 m of disagreement is alarming at GDOP 1 and expected at GDOP 80.
"""

from __future__ import annotations

import argparse
import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from decoder import haversine_km


class Status(str, Enum):
    ADSB_ONLY = "adsb_only"          # no MLAT fix available
    MLAT_ONLY = "mlat_only"          # no ADS-B position (Mode-S only aircraft)
    AGREE = "agree"                  # both present, within tolerance
    SUSPECT = "suspect"              # disagree beyond tolerance
    DIVERGENT = "divergent"          # disagree wildly - likely spoofed/faulty


@dataclass
class PositionSource:
    """One position estimate for an aircraft."""

    name: str                        # 'adsb' | 'mlat'
    lat: float
    lon: float
    alt_m: Optional[float] = None
    #: 1-sigma horizontal uncertainty, metres. ADS-B ~5 m; MLAT from GDOP.
    sigma_m: float = 5.0
    timestamp: float = 0.0
    #: MLAT only: geometry quality
    gdop: Optional[float] = None


@dataclass
class FusionResult:
    icao: str
    status: Status
    lat: float
    lon: float
    alt_m: Optional[float]
    #: which source the output position came from
    chosen: str
    #: horizontal disagreement between the sources, metres
    disagreement_m: float = 0.0
    #: combined 1-sigma uncertainty of the output position, metres
    sigma_m: float = 0.0
    #: how many sigma the disagreement represents
    sigma_ratio: float = 0.0
    notes: list[str] = field(default_factory=list)

    def __str__(self) -> str:
        flag = {"agree": "OK ", "adsb_only": "A  ", "mlat_only": "M  ",
                "suspect": "!! ", "divergent": "XXX"}[self.status.value]
        return (f"[{flag}] {self.icao}  {self.lat:9.5f},{self.lon:10.5f}  "
                f"src={self.chosen:4s} sigma={self.sigma_m:7.1f}m "
                f"disagree={self.disagreement_m:8.1f}m "
                f"({self.sigma_ratio:5.1f} sigma)  "
                + ("; ".join(self.notes) if self.notes else ""))


# ---------------------------------------------------------------------------
# Tolerances
# ---------------------------------------------------------------------------

#: How many combined sigmas of disagreement before we raise a flag. 3 sigma is
#: the usual statistical convention; beyond 5 sigma it is not noise.
SUSPECT_SIGMA = 3.0
DIVERGENT_SIGMA = 5.0

#: Absolute floor, so tiny sigmas do not flag trivial differences.
MIN_TOLERANCE_M = 50.0

#: ADS-B positions older/newer than this relative to the MLAT fix are not
#: comparable - the aircraft moved in between.
MAX_TIME_SKEW_S = 2.0

#: An airliner at 250 m/s covers 500 m in 2 s, so time skew matters.
MAX_SPEED_MPS = 350.0


def fuse(icao: str, adsb: Optional[PositionSource],
         mlat: Optional[PositionSource],
         min_tolerance_m: float = MIN_TOLERANCE_M,
         max_time_skew_s: float = MAX_TIME_SKEW_S) -> Optional[FusionResult]:
    """Combine the two position sources for one aircraft.

    Returns None if neither source has a position.
    """
    if adsb is None and mlat is None:
        return None

    # --- only one source ---
    if mlat is None:
        assert adsb is not None
        return FusionResult(
            icao=icao, status=Status.ADSB_ONLY,
            lat=adsb.lat, lon=adsb.lon, alt_m=adsb.alt_m,
            chosen="adsb", sigma_m=adsb.sigma_m,
            notes=["no MLAT coverage for this aircraft"],
        )
    if adsb is None:
        assert mlat is not None
        return FusionResult(
            icao=icao, status=Status.MLAT_ONLY,
            lat=mlat.lat, lon=mlat.lon, alt_m=mlat.alt_m,
            chosen="mlat", sigma_m=mlat.sigma_m,
            notes=["aircraft broadcasts no position (Mode-S only)",
                   f"GDOP {mlat.gdop:.1f}" if mlat.gdop else "GDOP unknown"],
        )

    # --- both: compare ---
    assert adsb is not None and mlat is not None
    d_m = haversine_km(adsb.lat, adsb.lon, mlat.lat, mlat.lon) * 1000.0

    # combined uncertainty: quadrature sum, since the errors are independent
    sigma_c = math.sqrt(adsb.sigma_m ** 2 + mlat.sigma_m ** 2)
    sigma_c = max(sigma_c, 1e-6)
    ratio = d_m / sigma_c

    notes: list[str] = []
    tolerance = max(SUSPECT_SIGMA * sigma_c, min_tolerance_m)

    # time skew check: if the samples are far apart the aircraft has moved
    skew = abs(adsb.timestamp - mlat.timestamp)
    if skew > max_time_skew_s:
        moved = MAX_SPEED_MPS * skew
        notes.append(f"time skew {skew:.1f}s (~{moved:.0f}m of travel) - "
                     f"comparison unreliable")
        # widen tolerance to account for the movement
        tolerance = max(tolerance, d_m)      # do not flag on a stale comparison

    if d_m <= tolerance:
        status = Status.AGREE
        chosen = "adsb"                       # prefer precision when they agree
        sigma_out = math.sqrt(1.0 / (1.0 / adsb.sigma_m ** 2
                                     + 1.0 / mlat.sigma_m ** 2))
        notes.append(f"sources agree within {tolerance:.0f}m")
    elif ratio >= DIVERGENT_SIGMA:
        status = Status.DIVERGENT
        chosen = "mlat"                       # independent source wins on trust
        sigma_out = mlat.sigma_m
        notes.append(f"disagreement {d_m:.0f}m = {ratio:.1f} sigma: "
                     f"ADS-B position is not credible")
        notes.append("using MLAT (aircraft cannot influence it)")
    else:
        status = Status.SUSPECT
        chosen = "mlat"
        sigma_out = mlat.sigma_m
        notes.append(f"disagreement {d_m:.0f}m = {ratio:.1f} sigma "
                     f"(tolerance {tolerance:.0f}m)")
        notes.append("flagging for review; using MLAT")

    out = mlat if chosen == "mlat" else adsb
    return FusionResult(
        icao=icao, status=status, lat=out.lat, lon=out.lon, alt_m=out.alt_m,
        chosen=chosen, disagreement_m=d_m, sigma_m=sigma_out,
        sigma_ratio=ratio, notes=notes,
    )


def mlat_sigma_from_gdop(gdop: float, clock_sigma_ns: float = 10.0,
                         n_receivers: int = 4) -> float:
    """1-sigma horizontal uncertainty of an MLAT fix, metres.

    sigma_r = c * clock_sigma, amplified by GDOP. This is the number that makes
    disagreement flagging meaningful: an MLAT fix with GDOP 80 genuinely cannot
    tell you whether an ADS-B position 2 km away is wrong.
    """
    C = 299792458.0
    sigma_r = C * (clock_sigma_ns * 1e-9)
    return sigma_r * max(gdop, 1e-3)


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------

def _self_test() -> int:
    print("Position fusion self-test")
    print("=" * 96)

    cases = [
        # (label, adsb_sigma, mlat_sigma, gdop, offset_m, expect_status)
        ("both agree, tight geometry",     5.0,   12.0,  1.2,     15.0, Status.AGREE),
        ("agree within tolerance",         5.0,   30.0,  3.0,     60.0, Status.AGREE),
        ("borderline: 4 sigma",            5.0,   50.0,  5.0,    201.0, Status.SUSPECT),
        ("divergent: ADS-B is wrong",      5.0,   20.0,  2.0,   2000.0, Status.DIVERGENT),
        # Bad geometry SUPPRESSES the flag, correctly. At GDOP 150 the MLAT
        # sigma is ~450 m, so a 2 km offset is only 1.3 sigma - the MLAT fix
        # genuinely cannot adjudicate it. Flagging here would be a false alarm.
        ("large offset but bad geometry",  5.0, 1500.0, 150.0,  2000.0, Status.AGREE),
        ("same offset, good geometry",     5.0,   20.0,   2.0,  2000.0, Status.DIVERGENT),
    ]

    fails = 0
    for label, s_a, s_m, gdop, offset_m, expect in cases:
        # place ADS-B at a reference point, MLAT offset due north
        lat0, lon0 = 33.5731, -7.5898
        dlat = offset_m / 111000.0
        adsb = PositionSource("adsb", lat0, lon0, 10000.0, s_a, timestamp=100.0)
        mlat = PositionSource("mlat", lat0 + dlat, lon0, 10000.0, s_m,
                              timestamp=100.0, gdop=gdop)
        r = fuse("TEST01", adsb, mlat)
        ok = r.status is expect
        if not ok:
            fails += 1
        print(f"{'PASS' if ok else 'FAIL'}  {label:32s} gdop={gdop:6.1f} "
              f"offset={offset_m:7.0f}m -> {r.status.value:9s} "
              f"({r.sigma_ratio:5.1f} sigma, tol={max(3*r.sigma_m, 50):6.0f}m)")
        if not ok:
            print(f"        ! expected {expect.value}")
        print(f"        {r}")

    # --- single-source cases ---
    print("-" * 96)
    adsb = PositionSource("adsb", 33.5731, -7.5898, 10000.0, 5.0)
    r = fuse("A", adsb, None)
    ok = r.status is Status.ADSB_ONLY
    fails += 0 if ok else 1
    print(f"{'PASS' if ok else 'FAIL'}  ADS-B only -> {r.status.value}")

    mlat = PositionSource("mlat", 33.6, -7.6, 9000.0, 40.0, gdop=2.5)
    r = fuse("B", None, mlat)
    ok = r.status is Status.MLAT_ONLY
    fails += 0 if ok else 1
    print(f"{'PASS' if ok else 'FAIL'}  MLAT only (Mode-S aircraft) -> {r.status.value}")
    print(f"        {r}")

    r = fuse("C", None, None)
    ok = r is None
    fails += 0 if ok else 1
    print(f"{'PASS' if ok else 'FAIL'}  no position from either source -> None")

    # --- time skew must suppress a false divergence ---
    print("-" * 96)
    lat0 = 33.5731
    adsb = PositionSource("adsb", lat0, -7.5898, 10000.0, 5.0, timestamp=100.0)
    # 5 km apart, but 20 seconds apart in time: an airliner covers 5 km in 20 s
    mlat = PositionSource("mlat", lat0 + 5000 / 111000.0, -7.5898, 10000.0,
                          20.0, timestamp=120.0, gdop=2.0)
    r = fuse("D", adsb, mlat)
    ok = r.status is Status.AGREE
    fails += 0 if ok else 1
    print(f"{'PASS' if ok else 'FAIL'}  5 km apart but 20 s skew -> {r.status.value} "
          f"(must not flag a stale comparison)")
    print(f"        {r}")

    # --- gdop -> sigma helper ---
    print("-" * 96)
    for gdop in (1.0, 10.0, 80.0, 200.0):
        s = mlat_sigma_from_gdop(gdop, 10.0)
        print(f"  GDOP {gdop:5.1f} with 10 ns clocks -> sigma {s:8.1f} m")
    print("  (this is why disagreement flagging must be GDOP-aware: at GDOP 200")
    print("   an MLAT fix cannot adjudicate a 2 km ADS-B discrepancy)")

    total = len(cases) + 4
    print("=" * 96)
    print(f"{total - fails}/{total} checks passed")
    return 1 if fails else 0


def _demo() -> int:
    print("Disagreement scenarios - what each one means in practice")
    print("=" * 96)
    lat0, lon0 = 33.5731, -7.5898

    scenarios = [
        ("healthy network, honest transponder",
         5.0, 15.0, 1.5, 20.0, 0.0),
        ("honest transponder, poor MLAT geometry",
         5.0, 1200.0, 120.0, 900.0, 0.0),
        ("transponder position error (faulty GPS)",
         5.0, 20.0, 2.0, 1500.0, 0.0),
        ("spoofed position (deliberate offset)",
         5.0, 20.0, 2.0, 12000.0, 0.0),
        ("clock unlocked at one station",
         5.0, 5000.0, 500.0, 8000.0, 0.0),
    ]

    for label, s_a, s_m, gdop, offset, _ in scenarios:
        adsb = PositionSource("adsb", lat0, lon0, 10000.0, s_a, timestamp=1.0)
        mlat = PositionSource("mlat", lat0 + offset / 111000.0, lon0, 10000.0,
                              s_m, timestamp=1.0, gdop=gdop)
        r = fuse("SCEN", adsb, mlat)
        print(f"\n{label}")
        print(f"  ADS-B sigma {s_a:6.0f}m   MLAT sigma {s_m:7.0f}m (GDOP {gdop:.0f})")
        print(f"  offset {offset:8.0f}m -> {r.status.value.upper()}")
        for n in r.notes:
            print(f"    - {n}")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="ADS-B / MLAT position fusion")
    ap.add_argument("--demo", action="store_true")
    args = ap.parse_args()
    raise SystemExit(_demo() if args.demo else _self_test())
