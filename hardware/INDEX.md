# Hardware — Index

Three separate systems. They solve different problems at different ranges and are
**not** variations of one design. Build the one that matches your question.

| # | System | Range | Cost | Licence | Question it answers |
|---|---|---|---|---|---|
| **A** | [ADS-B Airspace Receiver](A_adsb_receiver.md) | **250–400 km** | $50–150 | **None** (passive) | "What aircraft are flying, where, how high?" |
| **B** | [Insect Radar](B_insect_radar.md) | **1–5 m** | $40–90 | **None** (ISM band) | "How many insects, and what species?" |
| **C** | [IR Light Curtain](C_light_curtain.md) | **3 m** | $330 | **None** (Class 1) | "Is that a female mosquito, and where exactly?" |
| **A+** | [MLAT Receiver Network](A_plus_mlat_network.md) | **50–150 km** | $880 | **None** (passive) | "Where are the aircraft that broadcast *no* position?" |

---

## Which one do you want

```
   What is your actual question?
   │
   ├─ "What's flying overhead?" ─────────────▶ A. ADS-B receiver      ($50, one afternoon)
   │                                           Range is set by the curve of the Earth.
   │
   ├─ "How many insects are in this field /    ▶ B. Insect radar        ($40, one weekend)
   │   room, and what are they?"                Radar entomology. 1-5 m.
   │
   └─ "Kill that specific mosquito" ─────────▶ C. Light curtain       ($330, weeks)
                                               Only C can aim at 3 mm.
```

---

## Why three systems and not one

This is the central engineering point, and it is worth stating plainly because the
instinct is to build one sensor that does everything:

**No single sensor covers this range span.** The requirements pull in opposite
directions:

| | A: Aircraft | B: Insects (volume) | C: One mosquito (point) |
|---|---|---|---|
| Target size | 10–60 m | 3–50 mm | 3 mm |
| Range | 400 km | 3 m | 3 m |
| Angular precision needed | 0.1° | 5° | **0.02°** |
| Physics | radio horizon | Doppler | optical silhouette |
| Limit | **geometry** | **power** | **diffraction** |

A radar that reaches 400 km is blind to a mosquito; an optical system that resolves
a mosquito is blind past 30 m. **Pick the sensor for the range, not the other way
round.**

### The three hard limits, side by side

**A — ADS-B: limited by the curve of the Earth.** Range `≈ 4.12 × √(h_m) km`. An
airliner at 11 km is above your horizon at **432 km** regardless of power. Receiving
is free and unlicensed. This is the only one of the three that gives you
*hundreds of kilometres*.

**B — Insect radar: limited by the radar equation.** Power received falls as `1/R⁴`,
so doubling range costs **16×** the power. A $15 24 GHz Doppler module detects
insects at 1–5 m. To reach 1 km you would need ~10¹² times the power — which is why
insect radar is a *proximity* instrument.

**C — Light curtain: limited by diffraction and the laser.** `θ ≈ λ/D`. To resolve a
3 mm target at 3 m you need ~0.02° — achievable optically. But the laser's lethal
range is ~3 m (30 m at 30 W), so **there is no point detecting further than you can
reach.** The curtain is deliberately matched to the beam.

---

## The honest summary

| If you want to... | Build | Why not the others |
|---|---|---|
| Track aircraft | **A** | B and C cannot see past a few metres |
| Track aircraft that broadcast no position | **A+** | Needs 3+ stations and GPSDO clocks |
| Survey insect activity | **B** | A only sees transponders; C sees one insect at a time |
| Kill a specific mosquito | **C** | A and B cannot resolve 3 mm at aiming precision |

**All three are passive or licence-free.** Nothing here transmits on a licensed band.
That is not a coincidence — it is the only way to build long-range sensing without a
spectrum allocation, which in Morocco means ANRT.

---

## Shared infrastructure

Two things carry across all three:

**1. The tracking maths.** `system/tracker.py` (Kalman filter with nearest-neighbour
association and lead prediction) is target-agnostic. Aircraft, insects and mosquitoes
all need the same predict-associate-smooth pipeline. Reuse it.

**2. The desktop pattern.** Both `system/desktop_app.py` and `adsb/desktop.py` use the
same architecture: worker thread owns the state, UI polls a snapshot, filters are a
single `visible_*()` method so the map and the table can never disagree. Copy that
structure for any new sensor.

---

## Files

| File | System |
|---|---|
| `A_adsb_receiver.md` | Airspace receiver — parts, antenna, build order, verification |
| `B_insect_radar.md` | 24 GHz Doppler — parts, physics, calibration |
| `C_light_curtain.md` | IR light curtain — optics, photodiode chain, budget |
| `A_plus_mlat_network.md` | MLAT network — clocks, timestamping, accuracy |
| `../adsb/README.md` | Software-side build guide for system A |
| `../adsb/mlat.py` | TDOA solver (Levenberg-Marquardt, WGS-84 ENU) |
| `../system/radar_trigger.py` | System B integration — radar presence gate |
| `../detector_head.md` | Full design rationale for system C |
| `../laser_power_and_safety.md` | Kill-stage power and eye-hazard analysis |
