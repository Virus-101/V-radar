"""
Wingbeat detection and species classification from photodiode signal.

This is the temporal channel of the detector head. A mosquito crossing the IR
curtain modulates the return beam at its wingbeat frequency (350-700 Hz for
females of most vector species). We sample the photodiode, find the burst, and
classify it by dominant frequency.

Design notes
------------
- Sample rate must be >= 10x the highest frequency of interest. At 700 Hz that
  means >= 7 kSPS; we use 20 kSPS for clean peak resolution and headroom for
  harmonics (mosquito flight tones have strong harmonics above the fundamental).
- The burst is short. A mosquito crosses a 30 cm curtain at ~0.5 m/s in ~600 ms,
  so expect 100-200 ms of usable signal. That is 2000-4000 samples at 20 kSPS -
  enough for a 2048-point FFT with a Hanning window.
- We do NOT need a neural network. The discriminator is a band-pass + peak-pick.
  Cheap, explainable, and runs on an RP2040.

Reference data (fundamental wingbeat, Hz):
    Aedes aegypti        female 514-664   male 721-982
    Aedes albopictus     female 517+-62   male 687+-113
    Aedes japonicus      female 338+-32   male 503+-56
    Anopheles stephensi  female ~400      male ~600
    Toxorhynchites       female ~400      male ~500

Non-targets to reject:
    housefly ~200 Hz, honeybee ~230 Hz, moth ~50 Hz, bumblebee ~130 Hz
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Sequence

import numpy as np

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

#: Photodiode sample rate. Must exceed 2x the highest harmonic we care about.
SAMPLE_RATE_HZ = 20_000

#: FFT window size. 2048 @ 20 kSPS = 9.77 Hz/bin, ~100 ms of signal.
FFT_SIZE = 2048

#: Frequency bin resolution implied by the two above.
BIN_WIDTH_HZ = SAMPLE_RATE_HZ / FFT_SIZE  # 9.77 Hz


# ---------------------------------------------------------------------------
# Species table
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SpeciesProfile:
    """A known wingbeat band. Bands are inclusive."""

    name: str
    female_low: float
    female_high: float
    male_low: float
    male_high: float
    bites: bool = True

    def classify(self, freq_hz: float, tolerance: float = 0.0) -> str | None:
        """Return 'female', 'male', or None for a measured frequency."""
        if self.female_low - tolerance <= freq_hz <= self.female_high + tolerance:
            return "female"
        if self.male_low - tolerance <= freq_hz <= self.male_high + tolerance:
            return "male"
        return None


#: Profiles ordered roughly by how likely each is to be the local pest.
#: Calibrate against YOUR species - these do not transfer between regions.
SPECIES_TABLE: tuple[SpeciesProfile, ...] = (
    SpeciesProfile("Aedes aegypti",       514, 664, 721, 982),
    SpeciesProfile("Aedes albopictus",    455, 579, 574, 800),
    SpeciesProfile("Aedes japonicus",     306, 370, 447, 559),
    SpeciesProfile("Anopheles stephensi", 360, 440, 540, 660),
    SpeciesProfile("Toxorhynchites brew.", 350, 450, 450, 550, bites=False),
    SpeciesProfile("Culex pipiens",       380, 520, 550, 750),
)

#: The union of all female biting bands. Used for the L3 interlock: if the
#: measured frequency is not in here, we do not fire.
FEMALE_BITE_BAND_HZ = (350.0, 700.0)

#: The union of all mosquito bands, male and female. Anything outside this is
#: not a mosquito and must never be fired at. This is the safety-critical band,
#: NOT the female band - see the ambiguity note below.
MOSQUITO_BAND_HZ = (300.0, 1000.0)

#: Frequencies we actively know to be non-targets.
NON_TARGET_BANDS_HZ = (
    (30.0, 70.0),      # moth
    (110.0, 150.0),    # bumblebee
    (180.0, 260.0),    # housefly / honeybee
)

# ---------------------------------------------------------------------------
# IMPORTANT: the species/sex ambiguity
# ---------------------------------------------------------------------------
# Cross-checking the table above shows the bands are NOT separable in general:
#
#   Female union:  306 - 664 Hz
#   Male union:    447 - 982 Hz
#   Overlap:       447 - 664 Hz   <-- 217 Hz wide, i.e. most of the range
#
# A reading of 600 Hz is simultaneously "Aedes aegypti female" (514-664) and
# "Anopheles stephensi male" (540-660). Only 306-447 Hz is unambiguously
# female across all six species.
#
# The conclusion is that wingbeat frequency alone CANNOT identify species and
# sex. It can only:
#   (a) confirm "this is a mosquito, not a fly/bee/moth"  <- safety critical
#   (b) narrow sex to a set of possibilities               <- efficiency only
#
# This is why classify_frequency() returns a candidate set and a certainty
# level rather than a confident species name. Passing `local_species` when you
# know which species you actually have collapses the ambiguity - that is the
# right fix, and it is why the hardware doc says to calibrate locally.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------

@dataclass
class WingbeatResult:
    """Outcome of analysing one burst of photodiode samples."""

    detected: bool
    frequency_hz: float | None = None
    confidence: float = 0.0
    #: All (species, sex) pairs whose band contains the measured frequency.
    #: Usually more than one - see the ambiguity note above.
    candidates: list[tuple[str, str]] = field(default_factory=list)
    #: The species name when unambiguous, else None.
    species: str | None = None
    #: 'female', 'male', or None when the reading is ambiguous between them.
    sex: str | None = None
    #: True when every candidate has the same sex. Note this is about SEX
    #: only - it says nothing about whether the species bites.
    sex_certain: bool = False
    #: True when sex_certain AND the certain sex is female.
    certainly_female: bool = False
    #: True when every candidate is a species that takes blood meals.
    all_candidates_bite: bool = False
    should_fire: bool = False
    reason: str = ""
    snr_db: float = 0.0
    duration_ms: float = 0.0
    harmonic_hz: float | None = None

    def __str__(self) -> str:
        if not self.detected:
            return f"[no wingbeat] {self.reason}"
        if self.species:
            ident = f"{self.species} ({self.sex})"
        elif self.candidates:
            ident = "ambiguous: " + "/".join(f"{s.split()[0]} {x[0][0]}"
                                             for s, x in self.candidates)
        else:
            ident = "unknown"
        return (
            f"[{self.frequency_hz:7.1f} Hz] {ident} "
            f"conf={self.confidence:.2f} snr={self.snr_db:4.1f}dB "
            f"fire={self.should_fire} - {self.reason}"
        )


# ---------------------------------------------------------------------------
# Core DSP
# ---------------------------------------------------------------------------

def _hann(n: int) -> np.ndarray:
    return np.hanning(n)


def find_burst(
    samples: np.ndarray,
    threshold_sigma: float = 3.0,
    min_duration_ms: float = 20.0,
) -> tuple[int, int] | None:
    """Locate the insect crossing in a photodiode trace.

    The trace is mostly flat (steady return from the retroreflector) with a
    burst of oscillation when an insect crosses. We detect the burst by
    comparing a short-window RMS against the quiet baseline.

    Returns (start, end) sample indices, or None if nothing found.
    """
    samples = np.asarray(samples, dtype=float)
    n = len(samples)
    if n < 64:
        return None

    # Detrend: remove slow drift from ambient light changes / LED warmup.
    # A mosquito crossing is fast (100-200 ms); ambient drift is slow (>1 s).
    win = max(64, n // 16)
    if win % 2 == 0:
        win += 1
    kernel = np.ones(win) / win
    baseline = np.convolve(samples, kernel, mode="same")
    ac = samples - baseline

    # Estimate the noise floor from the quietest 20% of the trace.
    abs_ac = np.abs(ac)
    floor = np.percentile(abs_ac, 20)
    if floor <= 0:
        floor = abs_ac.std() or 1e-9

    thresh = threshold_sigma * floor

    # Find contiguous runs above threshold.
    active = abs_ac > thresh
    idx = np.flatnonzero(active)
    if idx.size == 0:
        return None

    # Group indices into runs, allowing small gaps (wingbeats have gaps
    # between the up and down strokes).
    max_gap = int(0.005 * SAMPLE_RATE_HZ)  # 5 ms
    groups: list[list[int]] = [[idx[0]]]
    for i in idx[1:]:
        if i - groups[-1][-1] <= max_gap:
            groups[-1].append(i)
        else:
            groups.append([i])

    # Pick the longest run that meets the minimum duration.
    best = None
    best_len = 0
    for g in groups:
        length = g[-1] - g[0]
        if length > best_len:
            best_len = length
            best = g

    if best is None:
        return None

    duration_ms = best_len / SAMPLE_RATE_HZ * 1000.0
    if duration_ms < min_duration_ms:
        return None

    return best[0], best[-1]


def dominant_frequency(
    samples: np.ndarray,
    sample_rate: int = SAMPLE_RATE_HZ,
    fft_size: int = FFT_SIZE,
    band_hz: tuple[float, float] = (100.0, 1500.0),
) -> tuple[float, float, float | None]:
    """FFT a burst and return (fundamental_hz, snr_db, second_harmonic_hz).

    The search band defaults to 100-1500 Hz: below 100 Hz is wind and handling
    noise, above 1500 Hz is electrical pickup.
    """
    samples = np.asarray(samples, dtype=float)
    n = len(samples)

    # Pad or truncate to the FFT size.
    if n < fft_size:
        buf = np.zeros(fft_size)
        buf[:n] = samples
    else:
        # Take the centre of the burst - the insect is most central there.
        start = (n - fft_size) // 2
        buf = samples[start:start + fft_size]

    buf = buf - buf.mean()
    buf = buf * _hann(fft_size)

    spectrum = np.abs(np.fft.rfft(buf))
    freqs = np.fft.rfftfreq(fft_size, d=1.0 / sample_rate)

    # Restrict to the search band.
    lo, hi = band_hz
    mask = (freqs >= lo) & (freqs <= hi)
    if not mask.any():
        return 0.0, 0.0, None

    band_spec = spectrum[mask]
    band_freqs = freqs[mask]

    peak_i = int(np.argmax(band_spec))
    peak_freq = float(band_freqs[peak_i])
    peak_mag = float(band_spec[peak_i])

    # SNR: peak against the median of the in-band spectrum.
    med = float(np.median(band_spec))
    snr_db = 20.0 * math.log10(peak_mag / med) if med > 0 and peak_mag > 0 else 0.0

    # Parabolic interpolation around the peak for sub-bin resolution.
    # Without this you are quantised to BIN_WIDTH_HZ (~9.8 Hz), which is not
    # fine enough to separate a 400 Hz female from a 430 Hz one.
    if 0 < peak_i < len(band_spec) - 1:
        y0, y1, y2 = band_spec[peak_i - 1], band_spec[peak_i], band_spec[peak_i + 1]
        denom = (y0 - 2 * y1 + y2)
        if denom != 0:
            delta = 0.5 * (y0 - y2) / denom
            delta = max(-1.0, min(1.0, delta))
            peak_freq += delta * BIN_WIDTH_HZ

    # Look for the second harmonic - mosquito flight tones have one, and its
    # presence raises confidence. It should be near 2x the fundamental.
    second = None
    target = peak_freq * 2.0
    hmask = (freqs >= target - 100) & (freqs <= target + 100)
    if hmask.any():
        h_spec = spectrum[hmask]
        h_freqs = freqs[hmask]
        h_i = int(np.argmax(h_spec))
        if h_spec[h_i] > med * 3.0:
            second = float(h_freqs[h_i])

    return peak_freq, snr_db, second


def classify_frequency(
    freq_hz: float,
    tolerance: float = 25.0,
    min_snr_db: float = 6.0,
    snr_db: float = 99.0,
    local_species: Sequence[str] | None = None,
) -> tuple[list[tuple[str, str]], bool, bool, str]:
    """Map a measured frequency to (candidates, sex_certain, all_bite, reason).

    Returns every (species, sex) pair whose band contains freq_hz. Usually more
    than one - see the ambiguity note at the top of this module.

    local_species: if you know which species are actually present locally,
        pass their names and the ambiguity collapses dramatically. This is the
        single most valuable calibration you can do.
    """
    if snr_db < min_snr_db:
        return [], False, False, f"signal too weak ({snr_db:.1f} dB)"

    # Explicitly reject known non-targets first.
    for lo, hi in NON_TARGET_BANDS_HZ:
        if lo <= freq_hz <= hi:
            return [], False, False, f"non-target band ({lo:.0f}-{hi:.0f} Hz)"

    # Anything outside the mosquito range is not a mosquito.
    mlo, mhi = MOSQUITO_BAND_HZ
    if not (mlo <= freq_hz <= mhi):
        return [], False, False, f"outside mosquito band ({freq_hz:.0f} Hz)"

    table = SPECIES_TABLE
    if local_species:
        wanted = {s.lower() for s in local_species}
        filtered = tuple(p for p in table if p.name.lower() in wanted)
        if filtered:
            table = filtered

    candidates: list[tuple[str, str]] = []
    for profile in table:
        sex = profile.classify(freq_hz, tolerance=tolerance)
        if sex is not None:
            candidates.append((profile.name, sex))

    if not candidates:
        return [], False, False, f"in mosquito band but no species match ({freq_hz:.0f} Hz)"

    # Sex is certain only if EVERY candidate has the same sex.
    sexes = {sex for _, sex in candidates}
    sex_certain = len(sexes) == 1
    certainly_female = sex_certain and sexes == {"female"}

    # Whether every candidate bites is a SEPARATE question from sex. At 420 Hz
    # all candidates are female but one (Toxorhynchites) does not take blood,
    # so "female" is certain while "biting female" is not.
    all_bite = all(
        next(p.bites for p in table if p.name == name)
        for name, _ in candidates
    )

    if len(candidates) == 1:
        name, sex = candidates[0]
        return candidates, sex_certain, all_bite, f"single match: {name} {sex}"

    if sex_certain:
        only = next(iter(sexes))
        bite_note = "all bite" if all_bite else "some do not take blood"
        return candidates, sex_certain, all_bite, (
            f"{len(candidates)} species, all {only} ({bite_note}) "
            f"- sex certain, species not"
        )

    return candidates, False, False, (
        f"{len(candidates)} candidates spanning both sexes - "
        f"frequency alone cannot resolve this"
    )


def analyse_burst(
    samples: np.ndarray,
    sample_rate: int = SAMPLE_RATE_HZ,
    tolerance: float = 25.0,
    min_snr_db: float = 6.0,
    local_species: Sequence[str] | None = None,
) -> WingbeatResult:
    """Full pipeline: raw photodiode trace -> firing decision.

    This is the function the RP2040 firmware mirrors and the function the
    interlock consults.

    Firing requires that the frequency evidence ALONE is sufficient, i.e. every
    candidate is a biting female. When the reading is ambiguous between a female
    and a male, we do NOT fire on frequency grounds. In the real system the
    spatial channel (size, in-flight vs hovering, dwell time) resolves the
    remainder; this module deliberately does not pretend to.
    """
    samples = np.asarray(samples, dtype=float)
    n = len(samples)

    burst = find_burst(samples)
    if burst is None:
        return WingbeatResult(
            detected=False,
            reason="no burst above threshold",
            duration_ms=n / sample_rate * 1000.0,
        )

    start, end = burst
    seg = samples[start:end + 1]
    duration_ms = len(seg) / sample_rate * 1000.0

    freq, snr, harmonic = dominant_frequency(seg, sample_rate=sample_rate)

    # Confidence blends SNR and harmonic agreement.
    confidence = min(1.0, max(0.0, (snr - min_snr_db) / 20.0))
    if harmonic is not None and freq > 0:
        ratio = harmonic / freq
        if 1.8 <= ratio <= 2.2:
            confidence = min(1.0, confidence + 0.25)

    candidates, sex_certain, all_bite, reason = classify_frequency(
        freq,
        tolerance=tolerance,
        min_snr_db=min_snr_db,
        snr_db=snr,
        local_species=local_species,
    )

    species = candidates[0][0] if len(candidates) == 1 else None
    sexes = {sex for _, sex in candidates}
    sex = sexes.pop() if len(sexes) == 1 else None
    certainly_female = sex_certain and sex == "female"

    # Fire only when the frequency evidence alone is sufficient: the sex is
    # certain, it is female, and every candidate species takes blood meals.
    should_fire = certainly_female and all_bite and confidence >= 0.35

    return WingbeatResult(
        detected=True,
        frequency_hz=freq,
        confidence=confidence,
        candidates=candidates,
        species=species,
        sex=sex,
        sex_certain=sex_certain,
        certainly_female=certainly_female,
        all_candidates_bite=all_bite,
        should_fire=should_fire,
        reason=reason,
        snr_db=snr,
        duration_ms=duration_ms,
        harmonic_hz=harmonic,
    )


# ---------------------------------------------------------------------------
# Signal synthesis - for testing without a live insect
# ---------------------------------------------------------------------------

def synthesise_crossing(
    wingbeat_hz: float,
    duration_ms: float = 150.0,
    sample_rate: int = SAMPLE_RATE_HZ,
    noise_sigma: float = 0.05,
    amplitude: float = 1.0,
    seed: int | None = 42,
) -> np.ndarray:
    """Generate a fake photodiode trace for one insect crossing.

    Models what the detector actually sees: a flat baseline, then a windowed
    oscillation at the wingbeat frequency, then flat again. The envelope is
    smooth (the insect enters and leaves the beam gradually).

    Used by the test suite and by the hardware bring-up procedure.
    """
    rng = np.random.default_rng(seed)
    total_ms = duration_ms * 3.0  # burst occupies the middle third
    n = int(total_ms / 1000.0 * sample_rate)
    t = np.arange(n) / sample_rate

    burst_n = int(duration_ms / 1000.0 * sample_rate)
    burst_start = (n - burst_n) // 2

    envelope = np.zeros(n)
    # Raised-cosine envelope across the crossing.
    w = np.hanning(burst_n)
    envelope[burst_start:burst_start + burst_n] = w

    # Fundamental + a second harmonic (mosquitoes have strong harmonics).
    carrier = np.sin(2 * math.pi * wingbeat_hz * t)
    carrier += 0.35 * np.sin(2 * math.pi * 2 * wingbeat_hz * t + 0.7)

    signal = 1.0 + amplitude * envelope * carrier
    signal += rng.normal(0, noise_sigma, n)

    return signal


def _self_test() -> int:
    """Run the classifier against synthesised crossings. Returns exit code.

    Note the assertions are about what the physics supports, not what would be
    nice. In particular a 600 Hz reading is EXPECTED to be ambiguous between
    Aedes female and Anopheles male, and the test asserts that we correctly
    refuse to fire on it rather than pretending to identify it.
    """
    # (wingbeat_hz, expectation)
    #   'fire'         -> must be classified certainly_female and fire
    #   'no_fire'      -> must NOT fire
    #   'ambiguous'    -> must detect a mosquito but refuse to fire
    cases = [
        # --- unambiguously female AND all candidates bite ---
        # (none in the default table: every female region contains at least one
        #  non-biting or cross-sex candidate. Local calibration fixes this.)

        # --- unambiguous non-targets: must never fire ---
        (200.0, "no_fire", "housefly"),
        (230.0, "no_fire", "honeybee"),
        (50.0,  "no_fire", "moth"),
        (130.0, "no_fire", "bumblebee"),

        # --- sex certain (all female) but not all bite: conservative no-fire ---
        (420.0, "sex_certain_no_fire", "Anopheles/Culex/Toxo - all female"),
        (400.0, "sex_certain_no_fire", "Anopheles/Culex/Toxo - all female"),

        # --- ambiguous: mosquito, but sex cannot be resolved from frequency ---
        (520.0, "ambiguous", "aegypti female OR japonicus male"),
        (600.0, "ambiguous", "aegypti female OR stephensi male"),

        # --- clearly male: must not fire ---
        (900.0, "no_fire", "aegypti male"),
    ]

    print(f"Wingbeat classifier self-test  (fs={SAMPLE_RATE_HZ} Hz, "
          f"bin={BIN_WIDTH_HZ:.2f} Hz)")
    print(f"mosquito band {MOSQUITO_BAND_HZ}  female-only region <447 Hz")
    print("-" * 96)

    failures = 0
    for freq, expect, label in cases:
        trace = synthesise_crossing(freq)
        r = analyse_burst(trace)

        ok = True
        notes = []

        if expect == "fire":
            if not r.should_fire:
                ok = False
                notes.append("expected FIRE, did not fire")
            if not r.certainly_female:
                ok = False
                notes.append("expected certainly_female")
        elif expect == "no_fire":
            if r.should_fire:
                ok = False
                notes.append("FALSE POSITIVE - would fire at a non-target")
        elif expect == "ambiguous":
            if not r.detected:
                ok = False
                notes.append("failed to detect a mosquito at all")
            if r.should_fire:
                ok = False
                notes.append("fired on an ambiguous reading - unsafe default")
            if not r.candidates:
                ok = False
                notes.append("no candidates reported")
            if r.sex_certain:
                ok = False
                notes.append("claimed sex certainty on a mixed-sex reading")
        elif expect == "sex_certain_no_fire":
            if not r.detected:
                ok = False
                notes.append("failed to detect a mosquito at all")
            if not r.sex_certain:
                ok = False
                notes.append("expected sex to be certain")
            if r.sex != "female":
                ok = False
                notes.append(f"expected female, got {r.sex}")
            if r.should_fire:
                ok = False
                notes.append(
                    "fired despite a non-biting candidate - should be "
                    "conservative"
                )

        status = "PASS" if ok else "FAIL"
        if not ok:
            failures += 1
        print(f"{status}  {freq:6.1f} Hz  {label}")
        print(f"        -> {r}")
        if r.candidates:
            cand = ", ".join(f"{s}({x})" for s, x in r.candidates)
            print(f"        candidates: {cand}")
        for note in notes:
            print(f"        ! {note}")

    # --- local species calibration collapses the ambiguity ---
    print("-" * 96)
    print("Local calibration: restricting to species actually present.")
    calib_cases = [
        (520.0, ["Aedes albopictus"], "single candidate, should fire"),
        (420.0, ["Anopheles stephensi"], "single candidate, should fire"),
        (600.0, ["Anopheles stephensi"], "resolves to MALE - must not fire"),
    ]
    for freq, species, note in calib_cases:
        trace = synthesise_crossing(freq)
        r = analyse_burst(trace, local_species=species)
        cand = ", ".join(f"{s}({x})" for s, x in r.candidates) or "none"
        ok = len(r.candidates) == 1
        want_fire = "should fire" in note
        if r.should_fire != want_fire:
            ok = False
        if not ok:
            failures += 1
        print(f"  {'PASS' if ok else 'FAIL'}  {freq:6.1f} Hz "
              f"local={species[0]:22s} -> {cand:34s} fire={r.should_fire}")
        print(f"        {note}")

    total = len(cases) + len(calib_cases)
    print("-" * 96)
    print(f"{total - failures}/{total} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(_self_test())
