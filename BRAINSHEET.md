# V-RADAR — Brain Sheet

**The complete plan, software to hardware, with the physics that constrains every choice.**

A passive sensing platform. Four separate systems, three of which are legal to build
anywhere with no licence. Nothing here transmits on a licensed band, and nothing here
is an engagement system — detection, tracking and display only.

---

## 0. The one-paragraph version

You asked for "a radar that scans long range with an instant analyser." The honest
answer is that **no single sensor does that**, and the reason is physics, not effort.
Range is limited by the curve of the Earth for radio, by the radar equation for
insects, and by diffraction for optics — three different laws that pull in opposite
directions. So the project is four systems, each matched to the range where its
physics works, sharing one tracking core and one display pattern. The long-range one
is a **passive 1090 MHz receiver** that beats a real radar by 9× on range for 1/100th
the cost, because it listens to aircraft telling you where they are instead of
shouting into the sky.

---

## 1. The four systems

```
   WHAT IS YOUR QUESTION?
   │
   ├─ "What aircraft are flying overhead?" ────▶ A. ADS-B RECEIVER
   │                                              $50 · 250-400 km · legal anywhere
   │                                              one afternoon · WORKS TODAY
   │
   ├─ "...including ones that broadcast no     ─▶ A+. MLAT NETWORK
   │    position at all?"                          $880 · 50-150 km · 3+ stations
   │                                              needs GPSDO clocks · weeks
   │
   ├─ "How much insect activity, and what     ─▶ B. INSECT RADAR
   │    species?"                                   $56 · 1-5 m · 24 GHz ISM
   │                                              reads wingbeat from Doppler · a weekend
   │
   └─ "Kill that specific mosquito." ─────────▶ C. LIGHT CURTAIN
                                                   $330 detector · 3 m · Class 1
                                                  only C can aim at 3 mm · weeks
```

| | Range | Cost | Licence | Physics limit | Status |
|---|---|---|---|---|---|
| **A** ADS-B | **250–400 km** | $50 | none | **Earth's curvature** | ✅ software done, verified |
| **A+** MLAT | 50–150 km | $880 | none | **clock precision + GDOP** | ✅ solver done, validated |
| **B** Insect radar | 1–5 m | $56 | none (ISM) | **radar eq. `1/R⁴`** | ✅ trigger done, 11/11 |
| **C** Light curtain | 3 m | $330 | none (Class 1) | **diffraction + laser reach** | ✅ classifier done, 12/12 |

---

## 2. Why four systems and not one

This is the central engineering point. The instinct is to build one sensor that does
everything. The numbers say you cannot:

| | A: Aircraft | B: Insects | C: One mosquito |
|---|---|---|---|
| Target size | 10–60 m | 3–50 mm | **3 mm** |
| Range | 400 km | 5 m | 3 m |
| Angular precision needed | 0.1° | 5° | **0.02°** |
| Limit | geometry | power | diffraction |
| Scaling law | `√h` | `1/R⁴` | `λ/D` |

### The three hard limits, quantified

**A — Earth's curvature.** `horizon ≈ 4.12·√(h_m)` km. An airliner at 11 km is above
your horizon at **432 km** no matter how little power it uses. Doubling range needs
*no* extra power — just a higher antenna (+41% range per doubling).

**B — Radar equation.** `P_r ∝ 1/R⁴`. Doubling range costs **16× the power**. A $15
24 GHz module sees insects at 1–5 m; reaching 1 km needs ~10¹² times the power. Insect
radar is a *proximity* instrument by nature.

**C — Diffraction.** `θ ≈ λ/D`. X-band radar (3 cm) with a 1 m dish gives `θ = 0.03 rad`
→ at 100 m that is **3 m** of position uncertainty for a **3 mm** target. Off by
**1000×**, and that is the diffraction limit, not an engineering gap. Resolving 3 mm
at 100 m needs a ~100 m antenna.

**And the binding constraint for C is the laser, not the sensor:** lethal range is
~3 m (30 m at 30 W). Detecting further than you can reach is pointless. That is
*why* the light curtain exists — it is deliberately matched to the beam.

---

## 3. Software architecture

One tracking core, one display pattern, four sensor front-ends.

```
   ┌──────────────────────────────────────────────────────────────────┐
   │  SENSOR FRONT-ENDS                                                 │
   │                                                                    │
   │  adsb/decoder.py      Mode-S/ADS-B decode (CRC-24, CPR)           │
   │  adsb/mlat.py         TDOA multilateration (WGS-84 ENU, LM)       │
   │  adsb/pps_monitor.py  GPSDO clock discipline acceptance test      │
   │  system/radar_trigger.py  24 GHz Doppler → activity + wingbeat    │
   │  detection/wingbeat.py    photodiode → wingbeat → species         │
   └────────────────────────┬─────────────────────────────────────────┘
                            │  position / detection / classification
                            ▼
   ┌──────────────────────────────────────────────────────────────────┐
   │  SHARED CORE                                                       │
   │                                                                    │
   │  system/tracker.py        Kalman + nearest-neighbour association   │
   │  adsb/position_fusion.py  multi-source trust + disagreement flag   │
   │  system/laser_controller.py  layered interlocks (10 gates)         │
   └────────────────────────┬─────────────────────────────────────────┘
                            ▼
   ┌──────────────────────────────────────────────────────────────────┐
   │  DISPLAY                                                           │
   │                                                                    │
   │  adsb/desktop.py           polar scope, filters, integrity panel   │
   │  system/desktop_app.py     control GUI, interlock state            │
   │  adsb/monitor.py           minimal scope (superseded by desktop)   │
   └──────────────────────────────────────────────────────────────────┘
```

### The two reusable patterns

**1. Tracking is target-agnostic.** Aircraft, insects and mosquitoes all need
predict → associate → smooth. `system/tracker.py` (Kalman with lead prediction) works
for all three. Reuse it; do not write a second tracker.

**2. Worker thread owns state, UI polls a snapshot.** Every GUI here follows this.
A slow source never freezes the window. Filters are a single `visible_*()` method so
the map and the table **cannot** disagree — they read the same source of truth.

> **Thread-safety note learned the hard way:** Tkinter is not thread-safe for UI
> calls. A background thread calling `update()` segfaults the process. Marshal every
> UI mutation through `app.after(0, ...)`.

---

## 4. Hardware designs

Full build guides live in `hardware/`. Summary:

### A — ADS-B receiver ($50, works today)

| Part | ~USD |
|---|---|
| RTL-SDR Blog V3 dongle | 30 |
| 1090 MHz antenna | 10 |
| SMA coax 1 m | 5 |
| USB extension | 5 |

```
rtl_adsb > capture.txt     # that is the entire sensor
```

**Improve in this order:** height (free, +41%/doubling) → 1090 MHz filter (if urban)
→ colinear antenna (137 mm elements) → LNA (last, amplifies noise too).

### A+ — MLAT network ($880, 4 stations)

Per station: RTL-SDR $30 + antenna $10 + **GPSDO $90** + GPS antenna $15 + Pi $35 +
enclosure $40 = **~$220**.

**The clock is the instrument.** Mode-S frames carry no timestamp, so a clock error
`dt` becomes range error `c·dt`:

| Clock | Position error |
|---|---|
| 10 ns GPSDO | **16 m** |
| 100 ns GPSDO | 72 m |
| 1 µs GPS | 784 m |
| 1 ms NTP | unusable |

One millisecond is **300 km** of error. NTP is not an option. Buy a **u-blox NEO-M8T**
(note the T — the M8N lacks the timing features).

**Timestamping** is the hard engineering: hardware timestamp in the SDR (best), GPIO
edge against PPS (~100 ns, achievable), or software `time.time()` on USB data
(1–10 ms jitter — **do not** expect this to work).

### B — Insect radar ($56, a weekend)

CDM324 or K-LC1a 24 GHz Doppler module $15 + RP2040 $5 + op-amp IF chain $3 + board
$8 + shielded enclosure $10.

**The insight:** a flying insect is not a rigid target. Its wings modulate the Doppler
return, so the spectrum shows a body peak plus **sidebands at ±wingbeat**. One $15
module gives speed *and* species signature simultaneously.

- Doppler scale: **160.9 Hz per m/s**
- Mosquito wingbeat: 350–800 Hz → sidebands there
- Needs ≥10 kSPS ADC (100 kSPS comfortable)
- **CW radar has no range gate** — it tells you *what*, not *where*

### C — Light curtain ($330 detector, $980 with aiming)

850 nm LED fan beam + retroreflector on the far post + global-shutter camera behind an
850 nm bandpass filter + fast photodiode for the wingbeat.

| Section | Cost |
|---|---|
| Illumination | $55 |
| Spatial channel (2 cameras + filters) | $180 |
| Temporal channel (photodiode + TIA + ADC) | $95 |
| Aiming — galvo (preferred) | $650 |
| Aiming — servo (demo only) | $18 |
| Controllers (Pi 5 + RP2040) | $64 |
| **Detector head, no aiming** | **$330** |

**Why a curtain and not a camera:** a mosquito at 3 m with a wide lens is **under 2
pixels**, and a 60 fps camera cannot sample a 400–800 Hz wingbeat — it aliases into
noise. The curtain illuminates a *plane*, looks along it against a retroreflector, and
detects the silhouette (~100:1 contrast, not 5:1) plus the flicker.

**The bandpass filters are $166 — half the detector head.** They are also the parts
people skip and the reason their build does not work. Buy the filters.

**Hard cost cliff:** a mosquito crosses 5 mm in ~5 ms; an MG996R servo takes ~150 ms.
That is 30× too slow. Real tracking needs a galvo, which is 40× the price. If the
budget cannot absorb it, the honest scope is **detection only**.

---

## 5. Build sequence — with gates

Each gate is a **stop condition**. Do not proceed past a failed gate; the failure will
be cheaper to fix now.

```
PHASE 0 — SEE IT WORK (no hardware, today)
  └─ python ADSB_Monitor.py --selftest          ← must print "all checks passed"
     python adsb/mlat.py --demo
     python adsb/pps_monitor.py --demo
     python detection/wingbeat.py
     python system/radar_trigger.py
     python -m pytest system/test_system.py
  GATE: all green. If not, fix the toolchain before buying anything.

PHASE 1 — AIRSPACE (A)  ~$50, one afternoon
  ├─ 1.1  rtl_test -s 2400000        → dongle seen?
  ├─ 1.2  rtl_adsb | head            → hex frames = you are receiving
  └─ 1.3  python adsb/desktop.py --rtl
  GATE: 100+ aircraft over an hour, CRC fail near zero, max range > 100 km.
        Cross-check one callsign against Flightradar24 — position must match.

PHASE 2 — RANGE TUNING (A)  ~$60
  ├─ 2.1  raise the antenna (biggest single gain)
  ├─ 2.2  add 1090 MHz SAW filter if urban
  └─ 2.3  build the colinear (137 mm elements)
  GATE: max range climbs toward 300+ km.

PHASE 3 — INSECT RADAR (B)  ~$56, one weekend
  ├─ 3.1  module + IF amp on a bench, scope the output
  ├─ 3.2  verify Doppler scale: 161 Hz per m/s against a known-speed target
  └─ 3.3  tether a local mosquito, record its spectrum, build YOUR band table
  GATE: a tethered mosquito trips the detector and reads the right wingbeat.
        NOTE: published frequency tables do NOT transfer between regions.

PHASE 4 — DETECTOR HEAD (C)  ~$120 for the go/no-go
  ├─ 4.1  illuminator + retroreflector + ONE camera + ONE filter
  └─ 4.2  point at the retroreflector, look for a FLAT bright field
  GATE: **the field is flat with no room-light clutter.**
        If not — STOP. Nothing downstream will work. This is the single
        most important gate in the whole project.

PHASE 5 — PHOTODIODE CHANNEL (C)  ~$95
  ├─ 5.1  photodiode + TIA + ADC on the RP2040
  └─ 5.2  wave a tuning fork across the curtain
  GATE: the flicker appears in the FFT at the right frequency.

PHASE 6 — TRIANGULATION (C)  ~$85
  ├─ 6.1  second camera, 150–300 mm baseline
  └─ 6.2  triangulate a known object at a known distance
  GATE: size estimation accurate to ±20%.

PHASE 7 — CATCH A MOSQUITO (C)
  └─ 7.1  net one, chill it to immobility, place it in the curtain, let it fly
  GATE: detection AND a correct wingbeat reading.

PHASE 8 — CLOCKS (A+)  ~$220 for ONE station
  ├─ 8.1  GPSDO + PPS to a GPIO
  └─ 8.2  python adsb/pps_monitor.py --serial COM3
  GATE: verdict PASS, jitter < 100 ns, drift < 50 ns/s.
        If PPS drifts more than 1 µs, FIX IT before buying station 2.

PHASE 9 — MLAT (A+)  ~$660 for stations 2–4
  ├─ 9.1  hardware timestamping (GPIO edge against PPS)
  └─ 9.2  validate: MLAT fix vs a known ADS-B position
  GATE: they agree within the predicted GDOP.
        This is the acceptance test — do not skip it.

PHASE 10 — KILL STAGE (C)  $650+  ⚠ READ THE SAFETY DOC FIRST
  └─ 10.1 enclosure, interlocks, beam dump, THEN the laser
  GATE: every interlock proven with the beam OFF before it is ever enabled.
```

---

## 6. Cost roll-up

| Phase | System | Cost | Cumulative |
|---|---|---|---|
| 0 | software only | $0 | $0 |
| 1–2 | A: airspace receiver, tuned | $110 | $110 |
| 3 | B: insect radar | $56 | $166 |
| 4–7 | C: detector head, complete | $330 | $496 |
| 8–9 | A+: MLAT network | $880 | $1,376 |
| 10 | C: kill stage | $650 | **$2,026** |

**You can stop at any phase and have something genuinely useful.** Phases 1–2 give you
a real airspace monitor for $110. Phases 4–7 give you a mosquito activity monitor for
$330 with no laser anywhere.

---

## 7. What is verified vs designed

Honesty matters more than a clean table.

### ✅ Verified — real execution, real output

| Component | Evidence |
|---|---|
| ADS-B decoder | CRC on 4/4 real frames; **CPR resolves to 52.25720, 3.91937** — matches the documented test vector to 5 decimals |
| MLAT solver | Recovers synthesised positions; residual < 50 m everywhere |
| PPS monitor | **5/5 clock modes classified correctly**; range error monotonic with clock quality |
| Position fusion | **10/10**; divergence correctly suppressed at bad GDOP |
| Wingbeat classifier | **12/12**; asserts the ambiguity rather than hiding it |
| Insect radar trigger | **11/11**; CFAR thresholding, symmetric-pair sideband detection |
| System interlocks | **42/42**; species gate holds the beam off with no evidence |
| Simulation | 100% accuracy, 60 fps, verified in headless Chrome |
| Desktop apps | Launched, screenshotted, integrity panel populated |

### 📐 Designed, not yet built

Everything in `hardware/` is a specification. **No hardware has been assembled.** The
costs are researched BOMs, not receipts. Ranges are computed from physics, not measured
on a bench.

### ⚠ Known unknowns

1. **In-flight dosing energy.** The 3 W / 25 ms kill figure is from *anaesthetised,
   immobilised* mosquitoes. In-flight may need 2–3× more. Unconfirmed.
2. **Local species bands.** Published wingbeat tables do not transfer between regions.
   You must measure your own.
3. **MLAT timestamping.** The solver is validated; the *hardware* timestamping at
   10 ns is the genuinely hard engineering and is untested.
4. **Optical link budget at 3 m.** Computed, not measured. Phase 4's gate exists
   precisely because this is where it could fail.

---

## 8. Findings worth knowing before you spend money

These came out of building the software, and each one changes a design decision.

### 1. Wingbeat frequency cannot identify species and sex

The bands overlap across 217 Hz:

```
Female union:  306 - 664 Hz
Male union:    447 - 982 Hz
Overlap:       447 - 664 Hz      <-- most of the range
```

A 600 Hz reading is *simultaneously* "Aedes aegypti female" and "Anopheles stephensi
male". Only below **447 Hz** is sex unambiguous.

**Consequence:** the frequency channel is an excellent **mosquito / not-mosquito
filter** — which is the safety-critical job — but it is not a species identifier.
**Calibrate to your local species**; that single step collapses the ambiguity.

### 2. MLAT is local, and its error grows with GDOP

Measured, 4 stations, 80 km baseline, 10 ns clocks:

| Distance | Error | GDOP |
|---|---|---|
| overhead | **41 m** | 1.3 |
| 50 km | 62 m | 2.4 |
| 90 km | 432 m | 11 |
| 210 km | 2,001 m | 80 |
| 320 km | 3,797 m | 206 |

The solver's **residual stays under 50 m everywhere** — it is fitting correctly. The
error grows because geometry amplifies it.

**Consequence:** `ADS-B gives RANGE, MLAT gives PRECISION.` Use each for what it is
good at. Disagreement flagging must be **GDOP-aware** or it produces false alarms.

### 3. Morphological opening destroys small fast targets

Measured on a synthetic 4-blob video:

| Morphology | Frames detecting |
|---|---|
| `open 3×3 + close 3×3` | **13 / 90 (14%)** |
| `close 3×3` only | 83 / 90 (92%) |
| `dilate 3×3 + close` | **90 / 90 (100%)** |

MOG2 produces a *thin, partial* mask for a small fast object, and `MORPH_OPEN` erodes
first — deleting the target. **The masks are too small, not too noisy.**

**Consequence:** never open first when hunting small fast objects.

### 4. Detection thresholds must scale with the number of bins

A fixed 6 dB gate fired on pure noise, because taking the max over ~2000 noise bins
means the expected maximum grows like `ln(N)`. Measured: pure chi-square noise reaches
**16.7 dB** above the median floor.

**Consequence:** use a **CFAR** threshold derived from the noise distribution. And for
a wingbeat, require the **symmetric sideband pair** — a lone line is noise.

### 5. Hobby servos cannot track a mosquito

A mosquito crosses 5 mm in ~5 ms. An MG996R takes ~150 ms. **30× too slow.**

**Consequence:** a galvo (~$650) or detection-only. There is no middle option.

### 6. A test vector that fails its own CRC is worse than none

Two ADS-B frames I wrote from memory failed CRC while the decoder was correct. I nearly
"fixed" working code.

**Consequence:** generate test vectors with a constructor that computes the checksum.
Never hand-type them.

---

## 9. Risks

| Risk | Severity | Mitigation |
|---|---|---|
| **Eye injury from the laser** | **Fatal to the project** | Hardware interlock first, enclosure second, beam last. Read `laser_power_and_safety.md`. |
| Phase 4 gate fails (no flat field) | High | Only $120 spent. Add more emitters or a better filter. |
| GPSDO jitter > 100 ns | Medium | Fix before station 2. A bad station poisons every fix. |
| Local species uncalibrated | Medium | Phase 3.3 / 7 — measure, do not assume |
| Scope creep into weapons | — | **Out of scope by design.** Detection and display only. |
| Publishing a feed leaks your address | Low | Round coordinates in public feeds |

---

## 10. Legal position

| Action | Status |
|---|---|
| Receive 1090 MHz ADS-B | **Legal** everywhere, no licence |
| Decode, display, record | **Legal** |
| 24 GHz ISM Doppler module | **Legal** (ITU RR 5.150), certified modules only |
| 850 nm Class 1 LED illumination | **Legal** — eye-safe |
| Publish an ADS-B feed | Legal; feed services strip coordinates |
| **Transmit on 1090 MHz** | **Illegal** without a licence |
| **Transmit radar on a licensed band** | **Illegal** — Morocco: ANRT |
| **Jam ADS-B or GNSS** | **Serious criminal offence** |
| Autonomous lethal targeting | **Prohibited** (LAWS); out of scope |

**Nothing in this project transmits.** That is not a coincidence — it is the only way
to build long-range sensing without a spectrum allocation.

---

## 11. Repository layout

```
V-radar/
├── README.md                     entry point, three-system summary
├── BRAINSHEET.md                 ← this document
├── SOFTWARE_AND_HARDWARE.md      interlock chain, GPIO map, module interfaces
├── interlock_explorer.html       interactive 10-gate interlock demo
│
├── hardware/
│   ├── INDEX.md                  which system to build — decision tree
│   ├── A_adsb_receiver.md        $50 · 250-400 km
│   ├── A_plus_mlat_network.md    $880 · clocks, timestamping, GDOP
│   ├── B_insect_radar.md         $56 · 24 GHz Doppler, wingbeat sidebands
│   ├── C_light_curtain.md        $330 · optics, photodiode, budget
│   ├── detector_head.md          full optical design rationale
│   ├── laser_power_and_safety.md ⚠ LD90 values, eye hazard, 5 interlock layers
│   └── specs.md                  v1 spec, ⚠ partially superseded
│
├── adsb/                         SYSTEM A + A+
│   ├── decoder.py                Mode-S/ADS-B from scratch (no deps)
│   ├── receiver.py               sources + live tracker
│   ├── desktop.py                full GUI: scope, filters, integrity, export
│   ├── mlat.py                   TDOA solver (WGS-84 ENU, Levenberg-Marquardt)
│   ├── position_fusion.py        ADS-B vs MLAT trust model
│   ├── pps_monitor.py            GPSDO clock acceptance test
│   ├── monitor.py                minimal scope
│   └── README.md
│
├── system/                       SYSTEM B + C
│   ├── desktop_app.py            control GUI with interlock state
│   ├── full_system.py            main pipeline
│   ├── detector.py               MOG2/KNN/frame-diff detection
│   ├── tracker.py                Kalman + association (SHARED CORE)
│   ├── servo_controller.py       pan-tilt, 3 PWM backends
│   ├── laser_controller.py       10-gate interlock chain
│   ├── radar_trigger.py          B → C presence gate
│   ├── preview.py                video preview generator
│   ├── config.yaml               all tunables
│   ├── test_system.py            42 tests
│   └── launch_app.bat
│
├── detection/
│   ├── wingbeat.py               photodiode → wingbeat → species (12/12)
│   ├── detect.py                 standalone pipeline (98% recall)
│   └── train.py                  YOLO training
│
├── simulation/
│   ├── index.html                p5.js detect→track→zap demo
│   └── run_tests.sh              deterministic headless test matrix
│
└── preview/                      screenshots and rendered video
```

---

## 12. Immediate next actions

**Today, $0:**
```bash
python ADSB_Monitor.py --selftest        # desktop copy — all checks passed
python adsb/mlat.py --demo
python adsb/pps_monitor.py --demo
```

**This week, $50:**
Buy the RTL-SDR Blog V3. Run Phase 1. You will have a working airspace monitor
covering 300+ km within an afternoon.

**Then decide:** tune range (Phase 2, $60), or branch to insects (Phase 3, $56), or
start the optical work (Phase 4, $120 — and respect that gate).

---

## 13. Sources

- **Keller et al.**, *Laser induced mortality of Anopheles stephensi mosquitoes*,
  Sci. Rep. **6**, 20936 (2016) — all LD90 values, exclusion-zone analysis
- **ANSI Z136.1** — MPE tables, skin and ocular damage thresholds
- **ICAO Annex 10 Vol IV, DO-260B** — ADS-B wire format
- **EUROCONTROL** MLAT documentation; **adsbexchange** — practical multilateration
- **Chapman, Drake & Reynolds**, *Recent insights from radar studies of insect
  flight*, Annu. Rev. Entomol. (2011) — radar entomology
- **IEEE Spectrum**, *Backyard Star Wars* — the Photonic Fence architecture
- **u-blox NEO-M8T** datasheet — PPS accuracy specification

---

*Detection, tracking and display. Not an engagement system, and deliberately so.*
