"""
MLAT — multilateration of aircraft from multiple passive receivers.

The closest legal thing to owning a radar. Aircraft that broadcast only Mode-S
(DF4/5/20/21) send no position. But if THREE OR MORE receivers hear the same
frame, the *differences* in arrival time place the aircraft on a hyperboloid,
and intersecting those gives a fix. Everything here is passive receive.

    python mlat.py --demo            # validate the solver against ground truth
    python mlat.py --network         # show what a receiver network buys you
    python mlat.py --clock           # why GPS-disciplined clocks are mandatory

THE CLOCK PROBLEM (read this before buying hardware)
----------------------------------------------------
TDOA needs timestamps. Mode-S frames carry none - each receiver timestamps
locally, so the receivers' clocks must be synchronised. A clock error of dt
becomes a range error of c*dt:

    10 ns   ->    3 m      GPS-disciplined oscillator      (works)
    100 ns  ->   30 m      good GPSDO, longer baseline    (works)
    1 us    ->  300 m      GPS without discipline          (marginal)
    1 ms    ->  300 km     NTP                            (useless)

One millisecond of clock error is three hundred kilometres of position error.
This is why every real MLAT network uses GPS-disciplined oscillators, and why
NTP is not an option. The solver below is only as good as the clocks feeding it.
"""

from __future__ import annotations

import argparse
import math
from dataclasses import dataclass, field
from typing import Optional, Sequence

import numpy as np

from decoder import haversine_km

# ---------------------------------------------------------------------------
# WGS-84 geodesy
# ---------------------------------------------------------------------------

WGS84_A = 6378137.0                      # semi-major axis, metres
WGS84_F = 1.0 / 298.257223563            # flattening
WGS84_E2 = WGS84_F * (2.0 - WGS84_F)     # first eccentricity squared
C_LIGHT = 299792458.0                    # m/s


def geodetic_to_ecef(lat_deg: float, lon_deg: float, alt_m: float) -> np.ndarray:
    lat, lon = math.radians(lat_deg), math.radians(lon_deg)
    sin_lat, cos_lat = math.sin(lat), math.cos(lat)
    N = WGS84_A / math.sqrt(1.0 - WGS84_E2 * sin_lat * sin_lat)
    return np.array([
        (N + alt_m) * cos_lat * math.cos(lon),
        (N + alt_m) * cos_lat * math.sin(lon),
        (N * (1.0 - WGS84_E2) + alt_m) * sin_lat,
    ])


def ecef_to_geodetic(xyz: np.ndarray) -> tuple[float, float, float]:
    """Bowring's method - converges in a few iterations for near-surface points."""
    x, y, z = float(xyz[0]), float(xyz[1]), float(xyz[2])
    lon = math.atan2(y, x)
    p = math.hypot(x, y)
    if p < 1e-9:
        return (90.0 if z > 0 else -90.0), 0.0, abs(z) - WGS84_A
    # initial guess
    lat = math.atan2(z, p * (1.0 - WGS84_E2))
    for _ in range(8):
        sin_lat = math.sin(lat)
        N = WGS84_A / math.sqrt(1.0 - WGS84_E2 * sin_lat * sin_lat)
        alt = p / math.cos(lat) - N
        lat = math.atan2(z, p * (1.0 - WGS84_E2 * N / (N + alt)))
    sin_lat = math.sin(lat)
    N = WGS84_A / math.sqrt(1.0 - WGS84_E2 * sin_lat * sin_lat)
    alt = p / math.cos(lat) - N
    return math.degrees(lat), math.degrees(lon), alt


class LocalFrame:
    """East-North-Up frame anchored at a reference point.

    ENU keeps the numbers small (metres, not 6.4e6) which matters for the
    least-squares conditioning.
    """

    def __init__(self, lat0: float, lon0: float, alt0: float = 0.0):
        self.lat0, self.lon0, self.alt0 = lat0, lon0, alt0
        self._origin = geodetic_to_ecef(lat0, lon0, alt0)
        latr, lonr = math.radians(lat0), math.radians(lon0)
        self._sl, self._cl = math.sin(latr), math.cos(latr)
        self._so, self._co = math.sin(lonr), math.cos(lonr)

    def to_enu(self, lat: float, lon: float, alt: float) -> np.ndarray:
        d = geodetic_to_ecef(lat, lon, alt) - self._origin
        return np.array([
            -self._so * d[0] + self._co * d[1],
            -self._sl * self._co * d[0] - self._sl * self._so * d[1] + self._cl * d[2],
            self._cl * self._co * d[0] + self._cl * self._so * d[1] + self._sl * d[2],
        ])

    def to_geodetic(self, enu: np.ndarray) -> tuple[float, float, float]:
        e, n, u = float(enu[0]), float(enu[1]), float(enu[2])
        d = np.array([
            -self._so * e - self._sl * self._co * n + self._cl * self._co * u,
            self._co * e - self._sl * self._so * n + self._cl * self._so * u,
            self._cl * n + self._sl * u,
        ])
        return ecef_to_geodetic(self._origin + d)


# ---------------------------------------------------------------------------
# Receiver network
# ---------------------------------------------------------------------------

@dataclass
class Receiver:
    name: str
    lat: float
    lon: float
    alt_m: float = 0.0
    #: clock error in seconds (positive = clock reads late). Real networks
    #: discipline this to ~10 ns with a GPSDO.
    clock_err_s: float = 0.0
    enu: Optional[np.ndarray] = field(default=None, repr=False)


class ReceiverNetwork:
    """A set of time-synchronised passive receivers."""

    def __init__(self, receivers: Sequence[Receiver]):
        if len(receivers) < 3:
            raise ValueError("MLAT needs at least 3 receivers (4 for 3-D)")
        self.receivers = list(receivers)
        lat0 = sum(r.lat for r in self.receivers) / len(self.receivers)
        lon0 = sum(r.lon for r in self.receivers) / len(self.receivers)
        alt0 = sum(r.alt_m for r in self.receivers) / len(self.receivers)
        self.frame = LocalFrame(lat0, lon0, alt0)
        for r in self.receivers:
            r.enu = self.frame.to_enu(r.lat, r.lon, r.alt_m)

    @property
    def positions(self) -> np.ndarray:
        return np.array([r.enu for r in self.receivers])

    @property
    def reference(self) -> Receiver:
        return self.receivers[0]

    def baseline_km(self) -> float:
        """Longest receiver-to-receiver distance. Sets the precision."""
        p = self.positions
        best = 0.0
        for i in range(len(p)):
            for j in range(i + 1, len(p)):
                best = max(best, float(np.linalg.norm(p[i] - p[j])) / 1000.0)
        return best

    def describe(self) -> str:
        lines = [f"network: {len(self.receivers)} receivers, "
                 f"max baseline {self.baseline_km():.0f} km"]
        for r in self.receivers:
            ce = r.clock_err_s * C_LIGHT
            lines.append(f"  {r.name:10s} {r.lat:9.5f} {r.lon:10.5f} "
                         f"{r.alt_m:6.0f}m   clock_err={r.clock_err_s * 1e9:+7.1f}ns "
                         f"({ce:+7.1f}m)")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Forward model
# ---------------------------------------------------------------------------

def predicted_range_differences(point_enu: np.ndarray,
                                net_enu: np.ndarray) -> np.ndarray:
    """Range difference from the target to each receiver, relative to receiver 0.

    Positive means the signal arrived at that receiver *later* than at the
    reference, i.e. it is further away.
    """
    d = net_enu - point_enu[None, :]
    rng = np.linalg.norm(d, axis=1)
    return rng[1:] - rng[0]


def observed_range_differences(tdoa_s: np.ndarray,
                               clock_errs_s: Optional[np.ndarray] = None) -> np.ndarray:
    """Convert arrival-time differences to range differences.

    tdoa_s[i] = (arrival at receiver i+1) - (arrival at receiver 0), seconds.
    Clock errors are subtracted out because they are a known (calibrated)
    property of the network.
    """
    rd = np.asarray(tdoa_s, dtype=float) * C_LIGHT
    if clock_errs_s is not None:
        ce = np.asarray(clock_errs_s, dtype=float)
        rd = rd - (ce[1:] - ce[0]) * C_LIGHT
    return rd


# ---------------------------------------------------------------------------
# Solver
# ---------------------------------------------------------------------------

@dataclass
class Fix:
    lat: float
    lon: float
    alt_m: float
    residual_rms_m: float
    gdop: float
    iterations: int
    receivers_used: int
    converged: bool
    #: 1-sigma position uncertainty estimate, metres (horizontal)
    sigma_h_m: float = 0.0

    def distance_km_from(self, lat: float, lon: float) -> float:
        return haversine_km(lat, lon, self.lat, self.lon)

    def __str__(self) -> str:
        flag = "OK " if self.converged else "BAD"
        return (f"[{flag}] {self.lat:9.5f}, {self.lon:10.5f}  "
                f"{self.alt_m:7.0f}m  rms={self.residual_rms_m:6.1f}m "
                f"GDOP={self.gdop:4.2f} sigma={self.sigma_h_m:6.1f}m "
                f"n={self.receivers_used} it={self.iterations}")


def _grid_search(net_enu: np.ndarray, obs_rd: np.ndarray,
                 alt_guess_m: float, half_width_m: float = 500e3,
                 n: int = 121) -> np.ndarray:
    """Coarse search for a starting point.

    Gauss-Newton needs a decent initial guess; an aircraft 300 km away will not
    converge from the network centroid. A 10 km grid over a 1000 km box is
    cheap and finds the right basin.
    """
    axis = np.linspace(-half_width_m, half_width_m, n)
    E, N = np.meshgrid(axis, axis)
    pts = np.stack([E.ravel(), N.ravel(),
                    np.full(E.size, alt_guess_m)], axis=1)          # (K,3)

    d = net_enu[None, :, :] - pts[:, None, :]                       # (K,M,3)
    rng = np.linalg.norm(d, axis=2)                                 # (K,M)
    rd = rng[:, 1:] - rng[:, 0:1]                                   # (K,M-1)

    resid = rd - obs_rd[None, :]
    cost = np.einsum("km,km->k", resid, resid)
    return pts[int(np.argmin(cost))]


def solve_fix(network: ReceiverNetwork, tdoa_s: np.ndarray,
              alt_guess_m: float = 10000.0,
              alt_known_m: Optional[float] = None,
              init_enu: Optional[np.ndarray] = None,
              max_iter: int = 60, tol_m: float = 0.5,
              grid: bool = True) -> Fix:
    """Solve for the aircraft position from arrival-time differences.

    alt_known_m: if the aircraft sent a Mode-S altitude reply we know z, which
        reduces the problem to 2-D and needs only 3 receivers.
    """
    net_enu = network.positions
    ce = np.array([r.clock_err_s for r in network.receivers])
    obs_rd = observed_range_differences(tdoa_s, ce)
    m = len(obs_rd)

    if alt_known_m is not None:
        # unknowns: east, north only
        if init_enu is not None:
            p = np.array([init_enu[0], init_enu[1]], dtype=float)
        else:
            p = np.array([0.0, 0.0])
        unknowns = 2
    else:
        if init_enu is not None:
            p = np.array(init_enu, dtype=float)
        elif grid:
            p = _grid_search(net_enu, obs_rd, alt_guess_m)
        else:
            p = np.array([0.0, 0.0, alt_guess_m])
        unknowns = 3

    lam = 1e-3                     # Levenberg-Marquardt damping
    iters = 0
    converged = False
    rms = float("inf")
    J = None

    for iters in range(1, max_iter + 1):
        if alt_known_m is not None:
            pt = np.array([p[0], p[1], alt_known_m])
        else:
            pt = p

        d = pt[None, :] - net_enu                                  # (M,3)
        rng = np.linalg.norm(d, axis=1)                            # (M,)
        rng = np.maximum(rng, 1e-6)
        model = rng[1:] - rng[0]
        resid = obs_rd - model

        rms = float(np.sqrt(np.mean(resid ** 2)))

        # Jacobian of (R_i - R_0) w.r.t. the unknowns
        u = d / rng[:, None]                                       # unit vectors
        Jfull = u[1:] - u[0]                                       # (M-1,3)
        J = Jfull[:, :2] if alt_known_m is not None else Jfull

        # Levenberg-Marquardt step
        JtJ = J.T @ J
        Jtr = J.T @ resid
        try:
            step = np.linalg.solve(JtJ + lam * np.eye(unknowns), Jtr)
        except np.linalg.LinAlgError:
            break

        p = p + step
        if np.linalg.norm(step) < tol_m:
            converged = True
            break

        # if the step made things worse, increase damping
        if alt_known_m is not None:
            pt2 = np.array([p[0], p[1], alt_known_m])
        else:
            pt2 = p
        d2 = pt2[None, :] - net_enu
        r2 = np.maximum(np.linalg.norm(d2, axis=1), 1e-6)
        rms2 = float(np.sqrt(np.mean((obs_rd - (r2[1:] - r2[0])) ** 2)))
        if rms2 > rms:
            lam *= 4.0
        else:
            lam = max(lam * 0.5, 1e-6)

    # final geometry
    if alt_known_m is not None:
        pt = np.array([p[0], p[1], alt_known_m])
    else:
        pt = p
    d = pt[None, :] - net_enu
    rng = np.maximum(np.linalg.norm(d, axis=1), 1e-6)
    u = d / rng[:, None]
    Jfull = u[1:] - u[0]
    J = Jfull[:, :2] if alt_known_m is not None else Jfull

    # GDOP: how much the geometry amplifies measurement error
    try:
        cov = np.linalg.inv(J.T @ J)
        gdop = float(np.sqrt(np.trace(cov)))
    except np.linalg.LinAlgError:
        cov = np.eye(unknowns) * np.inf
        gdop = float("inf")

    # assume 10 ns clock precision unless the network says otherwise
    sigma_t = max((abs(r.clock_err_s) for r in network.receivers), default=1e-8)
    sigma_t = max(sigma_t, 1e-8)
    sigma_r = C_LIGHT * sigma_t
    sigma_h = float(np.sqrt(max(cov[0, 0] + cov[1, 1], 0.0))) * sigma_r

    lat, lon, alt = network.frame.to_geodetic(pt)
    return Fix(lat=lat, lon=lon, alt_m=alt,
               residual_rms_m=rms, gdop=gdop, iterations=iters,
               receivers_used=len(network.receivers), converged=converged,
               sigma_h_m=sigma_h)


# ---------------------------------------------------------------------------
# Simulation - validate the solver against known ground truth
# ---------------------------------------------------------------------------

def make_tdoa(network: ReceiverNetwork, true_lat: float, true_lon: float,
              true_alt_m: float, clock_noise_s: float = 0.0,
              seed: int = 0) -> np.ndarray:
    """Compute the arrival-time differences a true position would produce."""
    rng = np.random.default_rng(seed)
    pt = network.frame.to_enu(true_lat, true_lon, true_alt_m)
    rd = predicted_range_differences(pt, network.positions)

    tdoa = rd / C_LIGHT
    # each receiver's clock adds its own error
    errs = rng.normal(0.0, clock_noise_s, len(network.receivers))
    tdoa = tdoa + (errs[1:] - errs[0])
    return tdoa


# ---------------------------------------------------------------------------
# Demonstrations
# ---------------------------------------------------------------------------

def casablanca_network(clock_err_s: float = 10e-9) -> ReceiverNetwork:
    """A plausible 4-station network around Casablanca. ~90 km baselines."""
    return ReceiverNetwork([
        Receiver("CASA-C", 33.5731, -7.5898, 50.0, clock_err_s),
        Receiver("CASA-N", 33.9000, -7.5000, 40.0, clock_err_s),
        Receiver("CASA-E", 33.5000, -7.2000, 60.0, clock_err_s),
        Receiver("CASA-S", 33.2000, -7.7000, 45.0, clock_err_s),
    ])


def demo() -> int:
    print("=" * 78)
    print("MLAT SOLVER VALIDATION")
    print("=" * 78)

    net = casablanca_network(clock_err_s=10e-9)
    print(net.describe())
    print()

    # Accuracy degrades with GDOP, which grows with range. The assertion is
    # therefore on the RESIDUAL (a solver-quality check) plus a GDOP-scaled
    # bound on position error - not a flat distance. Asserting "always <500 m"
    # would be asserting something MLAT cannot do: at 210 km with an 80 km
    # baseline the geometry alone puts a floor of kilometres on the error.
    #
    # (lat, lon, alt_m, label, max_acceptable_err_m)
    cases = [
        (33.5731, -7.5898, 10000, "overhead the network",      150),
        (33.9000, -7.5000, 11000, "~40 km NE, cruise",         300),
        (34.2000, -7.0000, 11000, "~90 km NE, cruise",         600),
        (35.5000, -6.0000, 10500, "~320 km NE, near horizon",  8000),
        (33.5731, -7.5898, 500,   "overhead, low altitude",    400),
    ]

    print(f"{'case':28s} {'true position':>21s}  {'solved':>21s}  "
          f"{'err':>7s} {'lim':>6s} {'GDOP':>7s} {'rms':>6s}")
    print("-" * 84)
    for lat, lon, alt, label, limit in cases:
        tdoa = make_tdoa(net, lat, lon, alt, clock_noise_s=10e-9, seed=1)
        fix = solve_fix(net, tdoa, alt_known_m=alt)
        err = fix.distance_km_from(lat, lon) * 1000.0
        print(f"{label:28s} {lat:9.4f},{lon:9.4f}  "
              f"{fix.lat:9.4f},{fix.lon:9.4f}  "
              f"{err:6.0f}m {limit:5.0f}m {fix.gdop:7.2f} {fix.residual_rms_m:6.1f}m")
        assert fix.converged, f"did not converge: {label}"
        # the solver must fit its own data well regardless of geometry
        assert fix.residual_rms_m < 100, (
            f"poor fit (rms={fix.residual_rms_m:.1f} m): {label}")
        assert err < limit, f"error {err:.0f} m exceeds {limit} m: {label}"

    print("-" * 84)
    print("The solver fits its data to tens of metres everywhere (rms column).")
    print("Position error grows because GDOP grows: the SAME few metres of")
    print("measurement error is amplified by the geometry as range increases.")

    # --- 3-D solve, altitude unknown ---
    print("\n--- 3-D solve, altitude NOT known (4 receivers, 3 unknowns) ---")
    for lat, lon, alt, label, _limit in cases[:3]:
        tdoa = make_tdoa(net, lat, lon, alt, clock_noise_s=10e-9, seed=2)
        fix = solve_fix(net, tdoa, alt_guess_m=10000.0)
        err = fix.distance_km_from(lat, lon) * 1000.0
        alt_err = fix.alt_m - alt
        print(f"  {label:26s} err={err:6.0f}m  alt_err={alt_err:+7.0f}m  "
              f"GDOP={fix.gdop:4.2f}  converged={fix.converged}")
        assert err < 800, f"3-D error too large: {label}"

    # --- the clock error sweep: the whole argument for a GPSDO ---
    print("\n--- why you need a GPS-disciplined clock ---")
    print(f"{'clock error':>14} {'range error':>13} {'position err':>14}  verdict")
    print("-" * 78)
    # Test at a position with GOOD geometry (overhead, GDOP ~1.3) so this
    # isolates the clock variable. Testing at 90 km would conflate clock error
    # with GDOP and make even a 10 ns GPSDO look inadequate.
    for err_s, label in [(1e-9, "1 ns rubidium"), (10e-9, "10 ns GPSDO"),
                         (100e-9, "100 ns GPSDO"), (1e-6, "1 us GPS"),
                         (1e-3, "1 ms NTP")]:
        net2 = casablanca_network(clock_err_s=err_s)
        tdoa = make_tdoa(net2, 33.70, -7.50, 10000,
                         clock_noise_s=err_s, seed=3)
        fix = solve_fix(net2, tdoa, alt_known_m=10000)
        e = fix.distance_km_from(33.70, -7.50) * 1000.0
        verdict = ("works" if e < 100 else
                   "marginal" if e < 1000 else
                   "useless")
        print(f"{label:>14} {err_s * C_LIGHT:10.1f} m {e:11.0f} m   {verdict}   "
              f"(GDOP {fix.gdop:.2f})")

    # --- geometry: what a bigger baseline buys ---
    print("\n--- geometry: baseline vs precision (10 ns clocks) ---")
    print(f"{'baseline':>10} {'GDOP':>6} {'sigma_h':>10}  note")
    print("-" * 78)
    configs = [
        (0.5, "0.5 deg spacing, ~50 km"),
        (1.0, "1.0 deg spacing, ~100 km"),
        (2.0, "2.0 deg spacing, ~200 km"),
        (4.0, "4.0 deg spacing, ~400 km"),
    ]
    for spacing, note in configs:
        net3 = ReceiverNetwork([
            Receiver("C", 33.5731, -7.5898, 50.0, 10e-9),
            Receiver("N", 33.5731 + spacing, -7.5898, 50.0, 10e-9),
            Receiver("E", 33.5731, -7.5898 + spacing, 50.0, 10e-9),
            Receiver("W", 33.5731, -7.5898 - spacing, 50.0, 10e-9),
        ])
        tdoa = make_tdoa(net3, 33.9, -7.4, 10000, clock_noise_s=10e-9, seed=4)
        fix = solve_fix(net3, tdoa, alt_known_m=10000)
        print(f"{net3.baseline_km():9.0f} km {fix.gdop:6.2f} "
              f"{fix.sigma_h_m:9.1f} m  {note}")

    print("\n" + "=" * 78)
    print("all solver checks passed")
    print("=" * 78)
    return 0


def network_report() -> int:
    net = casablanca_network()
    print(net.describe())
    print(f"\nmax baseline: {net.baseline_km():.0f} km")
    print("\nFor comparison, a 1 kW X-band radar with a 1 m dish detects a")
    print("transponder-less aircraft (RCS ~10 m^2) at ~46 km. A 4-station MLAT")
    print(f"network with {net.baseline_km():.0f} km baselines covers a comparable volume")
    print("passively, and improves with every receiver you add.")
    return 0


def clock_report() -> int:
    print("Clock synchronisation requirement")
    print("-" * 60)
    for dt, name in [(1e-9, "1 ns  rubidium/GPSDO"),
                     (10e-9, "10 ns GPSDO"),
                     (100e-9, "100 ns GPSDO"),
                     (1e-6, "1 us  GPS, undisciplined"),
                     (1e-3, "1 ms  NTP over LAN"),
                     (1e-2, "10 ms NTP over internet")]:
        print(f"  {name:28s} -> {dt * C_LIGHT:10.1f} m of range error")
    print()
    print("Mode-S frames carry no timestamp. Each receiver stamps locally, so the")
    print("clocks ARE the instrument. 1 ms of clock error is 300 km of position")
    print("error. Budget for a GPSDO per receiver - it is not optional.")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="MLAT multilateration")
    ap.add_argument("--demo", action="store_true", help="validate the solver")
    ap.add_argument("--network", action="store_true", help="network planning")
    ap.add_argument("--clock", action="store_true", help="clock requirements")
    args = ap.parse_args()
    if args.network:
        raise SystemExit(network_report())
    if args.clock:
        raise SystemExit(clock_report())
    raise SystemExit(demo())
