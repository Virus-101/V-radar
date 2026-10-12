"""
System B integration — 24 GHz insect radar as a presence trigger.

The radar and the optical curtain solve different halves of the same problem:

    radar  : cheap, always-on, 1-5 m, tells you SOMETHING IS FLYING + wingbeat
    optics : expensive, precise, 3 m, tells you WHERE and WHICH SPECIES

Neither alone is sufficient. Radar cannot aim (CW has no range gate, and the beam
is a cone, not a pixel). Optics cannot afford to stare (a curtain only covers a
plane, and a camera pipeline costs power).

So: run the radar continuously as a cheap gate. When it reports insect-class
activity in the wingbeat band, wake the optical pipeline. This cuts the optical
system's duty cycle by an order of magnitude and gives you a log of activity even
when nothing is in the curtain.

This module provides the radar side and the handshake. It is deliberately
mockable: ``MockRadarBackend`` generates synthetic spectra so the integration can
be tested and demonstrated with no hardware, exactly like the rest of this repo.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Sequence

import numpy as np

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: 24.125 GHz ISM centre frequency
F_CARRIER_HZ = 24.125e9
C_LIGHT = 299792458.0

#: Doppler shift per unit target velocity: f_d = 2*v*f_c/c
HZ_PER_MS = 2.0 * F_CARRIER_HZ / C_LIGHT      # ~160.9 Hz per m/s

#: Mosquito wingbeat band (females of the common vector species)
MOSQUITO_BAND_HZ = (350.0, 800.0)

#: Known non-targets, by wingbeat / rotor signature
NON_TARGET_BANDS_HZ = (
    (10.0, 75.0),        # moth (and below - slow flappers)
    (100.0, 165.0),      # bumblebee
    (180.0, 290.0),      # housefly, honeybee
    (2500.0, 9000.0),    # drone rotor harmonics
)


class Activity(str, Enum):
    QUIET = "quiet"              # nothing above threshold
    INSECT = "insect"            # insect-class wingbeat detected
    MOSQUITO = "mosquito"        # in the mosquito band specifically
    NON_TARGET = "non_target"    # flying, but the wrong signature
    CLUTTER = "clutter"          # broadband, probably rain/foliage/fan


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------

@dataclass
class RadarReading:
    """One processed radar dwell."""

    activity: Activity
    doppler_hz: float = 0.0            # body Doppler (speed)
    wingbeat_hz: Optional[float] = None  # modulation frequency
    speed_ms: float = 0.0
    snr_db: float = 0.0
    amplitude: float = 0.0
    reason: str = ""
    timestamp: float = 0.0
    #: candidates the classifier could not resolve between, if any
    ambiguous: bool = False

    @property
    def should_wake_optics(self) -> bool:
        """Should this reading wake the optical pipeline?

        Only MOSQUITO does. INSECT is interesting for the log but not worth
        spinning up the camera; NON_TARGET and CLUTTER are explicitly not.
        """
        return self.activity is Activity.MOSQUITO

    def __str__(self) -> str:
        wb = f"{self.wingbeat_hz:6.1f}Hz" if self.wingbeat_hz else "   --   "
        return (f"[{self.activity.value:10s}] "
                f"doppler={self.doppler_hz:7.1f}Hz v={self.speed_ms:4.2f}m/s "
                f"wb={wb} snr={self.snr_db:5.1f}dB  {self.reason}")


# ---------------------------------------------------------------------------
# Spectrum analysis
# ---------------------------------------------------------------------------

def doppler_to_speed(hz: float) -> float:
    return hz / HZ_PER_MS


def speed_to_doppler(ms: float) -> float:
    return ms * HZ_PER_MS


#: A wingbeat slower than this is not an insect wing. Moths sit near 50 Hz and
#: below, and letting the search start at 100 Hz mis-assigned them.
SIDEBAND_MIN_HZ = 30.0
SIDEBAND_MAX_HZ = 1200.0

#: Sideband must exceed the local floor by this factor to count.
SIDEBAND_PROMINENCE = 3.0


#: Detection threshold in units of floor * ln(N_search). A FIXED dB gate does
#: not work: taking the max over N noise bins means the expected maximum grows
#: like ln(N). Measured on chi-square noise with N~2000, pure noise reaches
#: 16.7 dB above the median floor - so a 6 dB gate fires on nothing.
NOISE_MULT = 2.5

#: A wingbeat slower than this is not an insect wing. Moths sit near 50 Hz and
#: below; the search must reach that low or moths get mis-assigned.
SIDEBAND_MIN_HZ = 30.0
SIDEBAND_MAX_HZ = 1200.0

#: Sideband must exceed the local floor by this factor to count.
SIDEBAND_PROMINENCE = 3.0


def detection_threshold(floor: float, n_search: int) -> float:
    """Noise-adaptive detection threshold (absolute spectrum units)."""
    return floor * NOISE_MULT * max(math.log(max(n_search, 2)), 1.0)


def _cfar_threshold(mean_noise: float, n_trials: int,
                    p_fa: float = 0.01) -> float:
    """Constant-false-alarm threshold for a periodogram.

    FFT power bins are exponentially distributed. The probability that the max
    of N bins exceeds T times the mean is  P = 1 - (1 - exp(-T))^N, so inverting
    gives the threshold for a chosen false-alarm rate. A fixed dB gate cannot do
    this: the max of N noise bins grows with N.
    """
    if mean_noise <= 0:
        return float("inf")
    n = max(n_trials, 2)
    return mean_noise * -math.log(1.0 - (1.0 - p_fa) ** (1.0 / n))


def find_sidebands(spectrum: np.ndarray, freqs: np.ndarray,
                   carrier_bin: int,
                   search_hz: tuple[float, float] = (SIDEBAND_MIN_HZ, SIDEBAND_MAX_HZ),
                   min_prominence: float = SIDEBAND_PROMINENCE,
                   p_fa: float = 0.01
                   ) -> tuple[Optional[float], float]:
    """Locate the wingbeat sidebands around the body-Doppler peak.

    A flying insect is not a rigid target: its wings oscillate, which modulates
    the Doppler return, so the spectrum shows a central peak with sidebands at
    +/- the wingbeat frequency.

    Two detection modes, both physically grounded:

    * **Pair** - both sidebands present at the same offset. This is the strong
      case; a genuine modulation is symmetric about the carrier.
    * **Single** - only the upper sideband exists because the lower one folded
      past DC. This happens when the wingbeat exceeds the body Doppler, which is
      the common mosquito case (slow flight, fast wings). Detected with a higher
      threshold because a single line is weaker evidence than a pair.

    Returns (wingbeat_hz, score) where score is the line height relative to the
    noise mean.
    """
    if carrier_bin <= 0 or carrier_bin >= len(spectrum) - 1:
        return None, 0.0

    lo, hi = search_hz
    n = len(spectrum)
    bin_hz = float(freqs[1] - freqs[0]) if n > 1 else 1.0
    mean_noise = float(np.mean(spectrum)) or 1e-9

    off_lo = max(int(lo / bin_hz), 1)
    off_hi = max(int(hi / bin_hz), off_lo + 1)
    n_trials = max(off_hi - off_lo, 1)

    # pair: min of the two sides must clear a threshold for N trials
    # single: one side must clear a stricter threshold, since a lone line is
    #         weaker evidence (there are 2*N chances for a single line)
    t_pair = _cfar_threshold(mean_noise, n_trials, p_fa)
    t_single = _cfar_threshold(mean_noise, 2 * n_trials, p_fa)

    best_f, best_score = None, 0.0
    for off in range(off_lo, off_hi + 1):
        lo_bin = carrier_bin - off
        hi_bin = carrier_bin + off
        if hi_bin >= n:
            break
        hi_v = spectrum[hi_bin]
        lo_v = spectrum[lo_bin] if lo_bin >= 1 else 0.0

        if lo_bin >= 1 and lo_v >= t_pair and hi_v >= t_pair:
            score = min(lo_v, hi_v) / mean_noise
        elif hi_v >= t_single:
            # upper only: the mirror folded below DC (or is absent)
            score = (hi_v / mean_noise) * 0.5     # lone line scores lower
        else:
            continue
        if score > best_score:
            best_score = score
            best_f = off * bin_hz

    if best_f is None:
        return None, 0.0
    return best_f, best_score


def classify_reading(doppler_hz: float, wingbeat_hz: Optional[float],
                     snr_db: float, floor_db: float = 6.0,
                     band: tuple[float, float] = MOSQUITO_BAND_HZ,
                     tolerance: float = 25.0
                     ) -> tuple[Activity, str, bool]:
    """Map a spectrum measurement to (activity, reason, ambiguous).

    Mirrors the logic in detection/wingbeat.py: an unresolved reading is NOT a
    mosquito. Ambiguity fails safe, because the downstream consumer may be a
    laser.
    """
    if snr_db < floor_db:
        return Activity.QUIET, f"below threshold ({snr_db:.1f} dB)", False

    if wingbeat_hz is None:
        # Something moved above threshold, but with no modulation. A rigid
        # target: rain, foliage, a fan, a vehicle. NOT an insect - insects
        # always modulate. (Real insect returns are dominated by the wingbeat.)
        v = doppler_to_speed(doppler_hz)
        return (Activity.CLUTTER,
                f"no wingbeat modulation, rigid target at {v:.2f} m/s", False)

    # explicit non-targets first
    for lo, hi in NON_TARGET_BANDS_HZ:
        if lo <= wingbeat_hz <= hi:
            name = ("moth" if hi <= 70 else
                    "bumblebee" if hi <= 160 else
                    "housefly/bee" if hi <= 280 else "drone rotor")
            return Activity.NON_TARGET, f"{name} band ({lo:.0f}-{hi:.0f} Hz)", False

    lo, hi = band
    if lo - tolerance <= wingbeat_hz <= hi + tolerance:
        # in-band. We cannot resolve species from wingbeat alone (the bands
        # overlap across species and sex - see detection/wingbeat.py), so we
        # report the frequency and flag that it needs the optical channel to
        # confirm species.
        return (Activity.MOSQUITO,
                f"wingbeat {wingbeat_hz:.0f} Hz in mosquito band "
                f"({lo:.0f}-{hi:.0f} Hz); species needs optical confirm",
                True)

    return Activity.INSECT, f"wingbeat {wingbeat_hz:.0f} Hz, insect but not mosquito", False


# ---------------------------------------------------------------------------
# Backends
# ---------------------------------------------------------------------------

class RadarBackend:
    """Base class: returns a raw spectrum for one dwell."""

    def read_spectrum(self) -> tuple[np.ndarray, np.ndarray]:
        """Return (spectrum, freqs_hz)."""
        raise NotImplementedError

    def close(self) -> None:
        pass


class MockRadarBackend(RadarBackend):
    """Synthesises spectra so the integration runs with no hardware.

    Produces a body-Doppler peak plus wingbeat sidebands, with noise. Used by
    the self-test and by anyone wanting to exercise the trigger logic.
    """

    #: sideband height as a fraction of the carrier. Real insect returns are
    #: wing-dominated, so this is high - the body return is often NOT the
    #: strongest line. 0.5 is realistic and still conservative.
    MODULATION_DEPTH = 0.5

    def __init__(self, n_bins: int = 2048, fs: float = 20000.0, seed: int = 7):
        self.n_bins = n_bins
        self.fs = fs
        self.freqs = np.fft.rfftfreq(n_bins, d=1.0 / fs)
        self.rng = np.random.default_rng(seed)
        self._script: list[tuple[Optional[float], Optional[float], float]] = []
        self._i = 0

    def script(self, items: Sequence[tuple[Optional[float], Optional[float], float]]):
        """Queue (speed_ms, wingbeat_hz, snr_db) triples. None = nothing there."""
        self._script = list(items)
        self._i = 0

    def read_spectrum(self) -> tuple[np.ndarray, np.ndarray]:
        if self._script:
            speed, wb, snr = self._script[self._i % len(self._script)]
            self._i += 1
        else:
            speed, wb, snr = (1.2, 520.0, 18.0)

        spec = self.rng.normal(0, 1.0, len(self.freqs)) ** 2

        if speed is not None:
            fd = speed_to_doppler(speed)
            peak_bin = int(round(fd / (self.fs / self.n_bins)))
            amp = 10 ** (snr / 10.0)
            for off in (-1, 0, 1):
                b = peak_bin + off
                if 0 <= b < len(spec):
                    spec[b] += amp
            if wb is not None:
                off = int(round(wb / (self.fs / self.n_bins)))
                # Both sidebands when they fit. When the wingbeat exceeds the
                # body Doppler the lower sideband folds past DC and only the
                # upper one exists - a real case for slow insects with fast
                # wings, which is exactly the mosquito regime.
                for sign in (+1, -1):
                    side = peak_bin + sign * off
                    if 1 <= side < len(spec):
                        spec[side] += amp * self.MODULATION_DEPTH
        return spec, self.freqs


class SerialRadarBackend(RadarBackend):
    """Reads IF samples from a microcontroller over serial and FFTs them.

    Protocol: the MCU sends a line of comma-separated ADC samples terminated by
    a newline, e.g. "2048,2051,2047,...". We FFT each line. Keep the line under
    ~4 KB; at 12-bit and 2048 samples that is about 8 KB of text, so either
    reduce to 1024 samples or send binary.

    Not exercised here - it needs hardware - but the framing is what the
    firmware in B_insect_radar.md emits.
    """

    def __init__(self, port: str, baud: int = 921600, n_samples: int = 1024,
                 sample_rate: float = 20000.0):
        import serial  # pyserial; imported lazily so the module works without it
        self.ser = serial.Serial(port, baud, timeout=1.0)
        self.n_samples = n_samples
        self.sample_rate = sample_rate
        self.freqs = np.fft.rfftfreq(n_samples, d=1.0 / sample_rate)
        self._window = np.hanning(n_samples)

    def read_spectrum(self) -> tuple[np.ndarray, np.ndarray]:
        while True:
            line = self.ser.readline().decode("ascii", errors="ignore").strip()
            if not line:
                continue
            parts = line.split(",")
            if len(parts) != self.n_samples:
                continue
            try:
                x = np.array([float(p) for p in parts])
            except ValueError:
                continue
            x = x - x.mean()
            spec = np.abs(np.fft.rfft(x * self._window)) ** 2
            return spec, self.freqs

    def close(self):
        try:
            self.ser.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Processor
# ---------------------------------------------------------------------------

@dataclass
class RadarStats:
    dwells: int = 0
    mosquito: int = 0
    insect: int = 0
    non_target: int = 0
    clutter: int = 0
    quiet: int = 0
    wake_requests: int = 0
    first_seen: float = 0.0
    last_mosquito: float = 0.0

    def as_dict(self) -> dict:
        elapsed = max(time.time() - (self.first_seen or time.time()), 1e-6)
        return {
            "dwells": self.dwells,
            "dwells_per_s": round(self.dwells / elapsed, 1),
            "mosquito": self.mosquito,
            "insect": self.insect,
            "non_target": self.non_target,
            "clutter": self.clutter,
            "quiet": self.quiet,
            "wake_requests": self.wake_requests,
            "mosquito_rate_pct": round(100.0 * self.mosquito / max(self.dwells, 1), 1),
        }


class InsectRadar:
    """Processes radar dwells into activity readings and wake decisions."""

    def __init__(self, backend: RadarBackend,
                 on_wake=None,
                 min_dwells_to_confirm: int = 2,
                 dwell_interval_s: float = 0.1):
        self.backend = backend
        self.on_wake = on_wake
        self.min_dwells = min_dwells_to_confirm
        self.dwell_interval_s = dwell_interval_s
        self.stats = RadarStats(first_seen=time.time())
        self._consecutive_mosquito = 0
        self.last_reading: Optional[RadarReading] = None
        self.log: list[RadarReading] = []

    def process_dwell(self) -> RadarReading:
        spec, freqs = self.backend.read_spectrum()
        self.stats.dwells += 1

        # body Doppler: the strongest peak away from DC
        n = len(spec)
        # Exclude only DC and the 1/f rise. This must be SMALL: the guard is a
        # frequency region, and insect body Doppler is only ~161 Hz per m/s, so
        # a guard of 2% of a 20 kHz spectrum (~195 Hz) would swallow the entire
        # low-speed insect regime. Exclude below ~20 Hz only.
        bin_hz = float(freqs[1] - freqs[0]) if n > 1 else 1.0
        guard = max(int(math.ceil(20.0 / bin_hz)), 2)
        search = spec[guard:]
        if search.size == 0:
            r = RadarReading(activity=Activity.QUIET, reason="empty spectrum",
                             timestamp=time.time())
            return r
        peak = int(np.argmax(search)) + guard
        doppler_hz = float(freqs[peak])

        # Noise floor from the whole spectrum. Estimating it from a handful of
        # guard bins is too noisy to threshold against.
        floor = float(np.median(spec)) or 1e-9
        snr_db = 10.0 * math.log10(max(spec[peak], 1e-12) / floor)

        # A peak below the CFAR threshold is NOT a detection. Without this, the
        # max over ~2000 noise bins passes any fixed gate.
        mean_noise = float(np.mean(spec)) or 1e-9
        thresh = _cfar_threshold(mean_noise, search.size, p_fa=0.001)
        if spec[peak] < thresh:
            r = RadarReading(activity=Activity.QUIET,
                             doppler_hz=doppler_hz,
                             speed_ms=doppler_to_speed(doppler_hz),
                             snr_db=snr_db, amplitude=float(spec[peak]),
                             reason=f"peak below noise threshold "
                                    f"({spec[peak]:.1f} < {thresh:.1f}, {snr_db:.1f} dB)",
                             timestamp=time.time())
            self.last_reading = r
            self.log.append(r)
            self.stats.quiet += 1
            self._consecutive_mosquito = 0
            return r

        wb, prominence = find_sidebands(spec, freqs, peak)
        activity, reason, ambiguous = classify_reading(
            doppler_hz, wb, snr_db)

        r = RadarReading(
            activity=activity,
            doppler_hz=doppler_hz,
            wingbeat_hz=wb,
            speed_ms=doppler_to_speed(doppler_hz),
            snr_db=snr_db,
            amplitude=float(spec[peak]),
            reason=reason,
            timestamp=time.time(),
            ambiguous=ambiguous,
        )
        self.last_reading = r
        self.log.append(r)
        if len(self.log) > 2000:
            self.log.pop(0)

        # --- stats ---
        setattr(self.stats, activity.value.replace("non_target", "non_target"),
                getattr(self.stats, activity.value) + 1)

        # --- wake logic: require N consecutive mosquito dwells ---
        # A single dwell is not enough. A stray reflection or a single wingbeat
        # transient should not spin up the camera.
        if activity is Activity.MOSQUITO:
            self._consecutive_mosquito += 1
            self.stats.last_mosquito = r.timestamp
            if self._consecutive_mosquito >= self.min_dwells:
                self.stats.wake_requests += 1
                self._consecutive_mosquito = 0
                if self.on_wake is not None:
                    self.on_wake(r)
        else:
            self._consecutive_mosquito = 0

        return r

    def run(self, duration_s: float = 0.0):
        """Poll the backend until duration elapses (0 = forever)."""
        t0 = time.time()
        while duration_s <= 0 or (time.time() - t0) < duration_s:
            self.process_dwell()
            time.sleep(self.dwell_interval_s)

    def close(self):
        self.backend.close()


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------

def _self_test() -> int:
    print("Insect radar integration self-test")
    print("=" * 90)
    print(f"carrier {F_CARRIER_HZ/1e9:.3f} GHz   "
          f"doppler scale {HZ_PER_MS:.1f} Hz per m/s   "
          f"mosquito band {MOSQUITO_BAND_HZ[0]:.0f}-{MOSQUITO_BAND_HZ[1]:.0f} Hz")
    print("-" * 90)

    # (speed_ms, wingbeat_hz, snr_db, expected_activity, label)
    cases = [
        (1.20, 520.0, 18.0, Activity.MOSQUITO,  "Aedes female, 520 Hz"),
        (0.80, 420.0, 16.0, Activity.MOSQUITO,  "Anopheles female, 420 Hz"),
        (1.50, 680.0, 15.0, Activity.MOSQUITO,  "Aedes male, 680 Hz (in band)"),
        (0.90, 200.0, 16.0, Activity.NON_TARGET, "housefly, 200 Hz"),
        (1.10,  50.0, 15.0, Activity.NON_TARGET, "moth, 50 Hz"),
        (1.30, 130.0, 14.0, Activity.NON_TARGET, "bumblebee, 130 Hz"),
        (2.00, 300.0, 15.0, Activity.INSECT,    "insect, below mosquito band"),
        (1.00,  None, 20.0, Activity.CLUTTER,   "rigid target, no modulation"),
        (None,  None,  0.0, Activity.QUIET,     "nothing there"),
    ]

    backend = MockRadarBackend()
    wakes: list[RadarReading] = []
    radar = InsectRadar(backend, on_wake=wakes.append, min_dwells_to_confirm=2)

    fails = 0
    for speed, wb, snr, expect, label in cases:
        backend.script([(speed, wb, snr)] * 4)      # 4 identical dwells
        readings = [radar.process_dwell() for _ in range(4)]
        got = readings[-1].activity
        ok = (got is expect)
        if not ok:
            fails += 1
        print(f"{'PASS' if ok else 'FAIL'}  {label:32s}")
        print(f"        -> {readings[-1]}")
        if not ok:
            print(f"        ! expected {expect.value}, got {got.value}")

    # wake counting: 2 mosquito cases x 4 dwells with min_dwells=2 -> 2 wakes each
    print("-" * 90)
    # Expected wakes, computed from the observed sequence of activities using
    # the same rule the implementation uses. This tests the WAKE LOGIC rather
    # than re-deriving the mock's arithmetic in the assertion.
    expected = 0
    run = 0
    for r in radar.log:
        if r.activity is Activity.MOSQUITO:
            run += 1
            if run >= radar.min_dwells:
                expected += 1
                run = 0
        else:
            run = 0
    print(f"wake requests: {radar.stats.wake_requests}  "
          f"(expected {expected}, derived from the observed activity sequence)")
    if radar.stats.wake_requests != expected:
        fails += 1
        print("        ! wake count does not match the derived expectation")
    if expected == 0:
        fails += 1
        print("        ! no wakes at all - the trigger never fired")

    # a single stray dwell must NOT wake the optics
    backend.script([(1.2, 520.0, 18.0)] + [(None, None, 0.0)] * 3)
    before = radar.stats.wake_requests
    for _ in range(4):
        radar.process_dwell()
    if radar.stats.wake_requests != before:
        fails += 1
        print("        ! a single stray dwell triggered a wake - must not")
    else:
        print("PASS  single stray mosquito dwell did NOT wake the optics")

    print("-" * 90)
    print("stats:", radar.stats.as_dict())
    print(f"\n{9 + 2 - fails}/{9 + 2} checks passed")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(_self_test())
