# V-Radar

**A passive sensing platform.** Four separate systems for four different ranges, three
of which are legal to build anywhere with no licence. Nothing here transmits on a
licensed band.

> **Start with [`BRAINSHEET.md`](BRAINSHEET.md)** — the full plan, software to
> hardware, with the physics that constrains every choice, build phases with gates,
> costs, and what is verified versus merely designed.

Built in parts, all present and verified:

| Part | Path | Status |
|---|---|---|
| **Full plan** | `BRAINSHEET.md` | 📋 the master document |
| ADS-B decoder (from scratch) | `adsb/decoder.py` | ✅ CPR matches the documented test vector |
| ADS-B desktop app | `adsb/desktop.py` | ✅ verified running, integrity panel live |
| MLAT TDOA solver | `adsb/mlat.py` | ✅ validated against ground truth |
| Position fusion | `adsb/position_fusion.py` | ✅ 10/10 |
| GPSDO clock monitor | `adsb/pps_monitor.py` | ✅ 5/5 clock modes classified |
| Insect radar trigger | `system/radar_trigger.py` | ✅ 11/11 |
| Wingbeat classifier | `detection/wingbeat.py` | ✅ 12/12 |
| Integrated system | `system/` | ✅ 42/42 tests pass |
| Simulation (p5.js) | `simulation/index.html` | ✅ 100% accuracy, 60 fps |
| Hardware specs | `hardware/` | 📐 design complete, not built |
| Original v1 spec | `hardware/specs.md` | ⚠️ **partially wrong** — see banner at top |

---

## Four hardware systems

Separate designs for separate problems — see **`hardware/INDEX.md`** for which to build.

| System | Range | Cost | Licence | Question |
|---|---|---|---|---|
| **A** ADS-B airspace receiver | **250–400 km** | $50 | none (passive) | What aircraft are flying? |
| **A+** MLAT receiver network | 50–150 km | $880 | none (passive) | ...including ones broadcasting no position? |
| **B** 24 GHz insect radar | 1–5 m | $56 | none (ISM) | How much insect activity, what species? |
| **C** IR light curtain | 3 m | $330 | none (Class 1) | Is that a female mosquito, exactly where? |

No single sensor covers this span: A is limited by the curve of the Earth, B by the
radar equation (`1/R⁴`), C by diffraction and the laser's 3 m reach. Pick the sensor
for the range.

---

## Start here

1. **Read `hardware/laser_power_and_safety.md`.** It answers the two questions that
   decide everything: what power kills a mosquito (**~3 W green, 25 ms**), and can it
   hurt a human (**yes — it can blind you permanently**). The margin is roughly
   **3.5 million× on the retina**. Nothing else in this repo matters until you have
   read that.
2. **Read `hardware/detector_head.md`.** The v1 design (one webcam + an ML model) does
   not work, and that document explains why: a mosquito is 2 px wide at 3 m and beats
   its wings at 400–800 Hz, which a 60 fps camera cannot see. The fix is a different
   physical architecture — an IR light curtain with a retroreflector.
3. **Build the simulation first** (`simulation/`) to see the whole pipeline work.
4. **Then build the detector head** (step 1 of the build order costs ~$120 and is the
   go/no-go experiment).

---

## Quick start

```bash
# Desktop app — the control GUI
python system/desktop_app.py --mock        # synthesised swarm, no hardware
python system/desktop_app.py               # live camera
python system/desktop_app.py --video f.mp4 # replay footage
#   or on Windows:  system\launch_app.bat

# Simulation — just open it
start simulation/index.html          # Windows
xdg-open simulation/index.html       # Linux

# Simulation test matrix (deterministic, headless)
bash simulation/run_tests.sh

# Wingbeat classifier self-test
python detection/wingbeat.py

# Integrated system tests
cd system && python -m pytest test_system.py -q

# Generate a video preview of the pipeline
python system/preview.py --seconds 15
```

---

## The desktop app

`system/desktop_app.py` — a Tkinter GUI that drives the **real** pipeline
(`MosquitoDetector` → `KalmanTracker` → `PanTiltController` → `LaserController` →
`OverlayRenderer`). Nothing is re-implemented for the UI; what you see is production
code.

```
┌──────────────────────────────────────────┬─────────────────────┐
│                                          │ TELEMETRY           │
│   live annotated video                   │  FPS          24    │
│   (detections, track IDs, trails,        │  Detections    6    │
│    crosshair, firing-zone circle)        │  Tracks        6    │
│                                          │  Confirmed     6    │
│                                          │  Target        5    │
│                                          │  Fires         0    │
│                                          ├─────────────────────┤
│                                          │ INTERLOCKS          │
│                                          │  Armed              ●│
│                                          │  Species gate req.  ●│
│                                          │  Species confirmed  ○│
│                                          │  Emergency stop     ○│
│                                          ├─────────────────────┤
│                                          │ BEAM HELD OFF BY    │
│                                          │  NO_WINGBEAT_CONFIRM│
│                                          │                 143 │
├──────────────────────────────────────────┤─────────────────────┤
│ frame 812 | NO_WINGBEAT_CONFIRM | TARGET │ [▶ Start] [⏸ Pause] │
└──────────────────────────────────────────┴─────────────────────┘
```

**Deliberate design choices:**

- **The vision pipeline runs on a worker thread.** Only the newest frame is kept in
  the queue (stale frames are dropped), so the UI stays responsive and never falls
  behind the pipeline.
- **ARM enters active mode too.** Arming alone leaves gate 3 (`require_active_mode`)
  blocking every shot — you would see "ARMED" and never understand why nothing fired.
  That bug existed in the first build of this app and is fixed.
- **E-STOP is always live** and is the largest control on the panel. A safety control
  that is hard to find is not a safety control.
- **Bench mode is opt-in and labelled.** It disables the species gate so the aim/fire
  path is demonstrable without a sensor head. The label says never to use it with a
  live beam.

**Verified behaviour** (screenshots captured from the running app):

| State | Fires | Beam held off by |
|---|---|---|
| Gate on (default) | **0** | `NO_WINGBEAT_CONFIRM` ×143 |
| Bench mode (gate off) | **9** | `TARGET_COOLDOWN` ×87, `OUTSIDE_FIRING_ZONE` ×48 |

The first row is the system behaving correctly: detection and tracking fully working,
beam correctly refused because no photodiode evidence exists.

---

## The simulation

An interactive p5.js demo of the full detect → track → zap loop. 22 mosquitoes with
erratic Perlin-noise flight, a turret with a persistent target lock and lead
prediction, detection brackets, laser beam, and zap bursts.

**Controls:** LMB attract · RMB repel · SPACE toggle auto-target · +/- mosquito count ·
S save PNG.

**Measured behaviour** (deterministic harness, 30 s of simulated time):

| Mosquitoes | Zapped | Accuracy | Max simultaneous detections |
|---|---|---|---|
| 6 | 51 | 100% | 4 |
| 22 | 110 | 100% | 11 |
| 45 | 105 | 100% | 21 |

### A note on the simulation's numbers

The turret hits 100% of its shots. That is **not** a claim that a real system would.
The simulation models a mosquito as a point with smooth noise-driven motion and a
turret that can turn at 9 rad/s. Reality has aerodynamic unpredictability, latency,
vibration, and a galvo that costs more than the rest of the BOM combined. Treat the
simulation as a check that the *pipeline logic* is sound, not as a performance
prediction.

---

## The physics that constrains the design

**To kill a mosquito** (Keller et al., *Sci. Rep.* 6, 20936):

| Wavelength | Power | Pulse | Notes |
|---|---|---|---|
| 532 nm green | **3.0 W** | 25 ms | most power-efficient |
| 10.6 µm CO₂ | **2.1 W** | 25 ms | comparable, and **far safer** |
| 976 nm diode | 10.9 W | 25 ms | needs 3.6× the power |

**Human hazard, at that same power:**

| Target | Threshold | vs. kill dose |
|---|---|---|
| Mosquito (LD90) | 1.78 J/cm² | 1× |
| Human eye (MPE) | ~5 × 10⁻⁷ J/cm² | **~3.5 million× more sensitive** |
| Human skin | >2 × 10⁻² J/cm² | ~90× more sensitive |

**Recommendation: if you build a kill stage, use 10.6 µm CO₂ or 1.47 µm.** Both are
absorbed before reaching the retina, which collapses the required exclusion zone from
"most of the room" to **under one metre**. Green is more efficient but needs a large
clear volume around the beam.

---

## Two findings worth knowing before you build

### 1. Wingbeat frequency cannot identify species and sex

The frequency bands overlap almost completely:

```
Female union:  306 - 664 Hz
Male union:    447 - 982 Hz
Overlap:       447 - 664 Hz   <-- 217 Hz, i.e. most of the range
```

A 600 Hz reading is *simultaneously* "Aedes aegypti female" and "Anopheles stephensi
male". Only below 447 Hz is sex unambiguous. The frequency channel is an excellent
**mosquito / not-mosquito filter** — which is the safety-critical job — but it is not
a species identifier. **Calibrate to your local species**; that single step collapses
the ambiguity (see `detection/wingbeat.py`, `local_species=`).

### 2. Hobby servos cannot track a mosquito

A mosquito crosses a 5 mm spot in ~5 ms. An MG996R servo takes ~150 ms to move. That
is a 30× miss. Real tracking needs a **galvanometer scanner** — which costs ~$650
versus ~$18 for servos. This is a hard cost cliff: a realistic system is **~$1,000**,
not the $200 the original spec assumed.

### 3. Morphological *opening* destroys small fast targets

Found by running the detection pipeline on a synthetic 4-blob video rather than just
compiling it. The original fallback detector did `MORPH_OPEN` then `MORPH_CLOSE`.

MOG2 produces a **thin, partial** mask for a small fast-moving object — a 3 px blob
moving 2–3 px/frame leaves only a sliver of disagreement with the background model.
`MORPH_OPEN` erodes first, which deletes that sliver outright. Measured, same video:

| Morphology | Frames with a detection |
|---|---|
| `open 3x3 + close 3x3` (original) | **13 / 90 (14%)** |
| `close 3x3` only | 83 / 90 (92%) |
| `dilate 3x3 + close 3x3` (fixed) | **90 / 90 (100%)** |

The masks are too *small*, not too *noisy* — so the fix is to **dilate**, not open.
After the fix the pipeline reports a median of exactly 4 detections/frame, matching
the 4 blobs actually in the video, and 98% of frames detect at least one target.

This is a general lesson for detecting small fast objects: **never open first.**
Opening is for removing speckle from a mask that is already the right size.

---

## Safety

This repository documents a system that can permanently blind someone. If you build
the laser stage:

- **Enclose the beam path.** Do not copy the open-air research configuration.
- **Hardware interlock first.** A pull-down resistor on the laser enable line, owned
  by a separate MCU, so the beam dies when the software does.
- **Never fire at a stationary target.** A mosquito cannot hold still; a finger can.
- **Laser safety eyewear** rated for the wavelength and power, for everyone present.
- **Not near children or pets.** Ever.

The detector head alone is Class 1 and safe to build on a bench. Build it first. It is
genuinely useful on its own as a monitor that tells you when and where mosquitoes are
active.

---

## Sources

- Keller, M. D. et al. *Laser induced mortality of Anopheles stephensi mosquitoes.*
  Sci. Rep. **6**, 20936 (2016). — all LD90 values, exclusion-zone analysis.
- ANSI Z136.1 — MPE tables; skin and ocular damage thresholds.
- Berkeley Lab EHS, *Laser Bio-effects*; Univ. of Illinois DRS laser safety.
- Mukundarajan et al., *Sci. Data* (2020); Feugère et al. — wingbeat frequency tables.
- IEEE Spectrum, *Backyard Star Wars* — the Photonic Fence architecture.
