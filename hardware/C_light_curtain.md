# System C — IR Light Curtain (Detector Head)

**The only one of the three that can resolve a mosquito well enough to aim at it.**
The full design rationale is in `../detector_head.md`; this is the build-focused view.

| | |
|---|---|
| **Range** | **3 m** post spacing (matched to the laser's reach) |
| **Cost** | **$330** detector head, $980 with galvo aiming |
| **Licence** | **None** — 850 nm LED illumination is Class 1 |
| **Build time** | Weeks |
| **Resolves** | position ±1 mm, size ±20%, **wingbeat frequency** |

---

## 1. Why a light curtain and not a camera

A camera looking at a room fails on three counts, and the reasons determine the
whole architecture:

| Problem | Number |
|---|---|
| Mosquito at 3 m with a 175° lens | **under 2 pixels** |
| Wingbeat at 400–800 Hz, camera at 60 fps | **aliased into noise** — unusable |
| Room lighting clutter on a 2-pixel target | swamps it |

The fix is **different physics**: illuminate a thin *plane* of IR, look *along* it
against a **retroreflector**, and detect the **silhouette** plus its **flicker**.

```
   POST A                                                    POST B
   ┌──────────────────────────┐              ┌──────────────────────────┐
   │  850nm IR ILLUMINATOR    │───light─────▶│   RETROREFLECTOR         │
   │  (fan beam, ~1m tall)    │              │   (3M Scotchlite)        │
   │                          │◀──returns────│                          │
   │  CAMERA (global shutter) │              │   (passive, no power)    │
   │  PHOTODIODE + TIA        │              │                          │
   └──────────────────────────┘              └──────────────────────────┘
              ▲
              │  insect crossing casts a shadow on the retroreflector
              │  → dark cluster on a uniformly bright field
```

Two properties make this work: the retroreflector returns light **to source**, so
background clutter nearly vanishes, and the silhouette gives ~100:1 contrast instead
of ~5:1.

---

## 2. Parts

### 2.1 Illumination — $55

| # | Part | MPN | Qty | ~USD | Notes |
|---|---|---|---|---|---|
| 1 | IR emitter, 850 nm, 1 W | **SFH 4715S** | 8 | 2.50 | 850 nm: cheap, invisible, matches silicon QE. 940 nm needs ~2× power. |
| 2 | Fan collimator 7°×60° | Carclo 10048 | 8 | 1.20 | Spreads each emitter into a vertical fan |
| 3 | Constant-current driver, 1 A | **AL8860** / TPS92515 | 2 | 1.80 | **Not** a series resistor — thermal drift wrecks the link budget |
| 4 | Retroreflective sheeting 300×1000 mm | **3M Scotchlite 983-10** | 1 | 18.00 | Diamond grade, ~1200 cd/lx/m² |

**Why LEDs and not a laser line:** a laser is brighter and better collimated, but it
is a **Class 3B eye hazard** pointed across a room, and it produces **speckle** that
ruins silhouette sharpness. LEDs are Class 1, diffuse, speckle-free.

### 2.2 Spatial channel — $180

| # | Part | MPN | Qty | ~USD | Notes |
|---|---|---|---|---|---|
| 5 | Global-shutter mono cam, 1 MP, 120 fps | **OV9281** (Arducam B0332) | 2 | 32.00 | Global shutter mandatory — rolling shutter skews a moving shadow. Mono = ~2× IR sensitivity. |
| 6 | **850 nm bandpass filter, 25 mm** | Thorlabs **FB850-40** | 2 | 45.00 | **Not optional.** Blocks room light. This alone is the difference between working and not. |
| 7 | M12 lens 8 mm f/1.4 | — | 2 | 12.00 | Narrow, not wide — you need pixels per millimetre, not field of view |
| 8 | CSI/USB3 cable 300 mm | — | 2 | 4.00 | |

**Two cameras, not one:** a single camera gives a bearing but not a range. Without
range you cannot compute true size (a 2-pixel blob at 0.5 m and at 3 m look
identical) and you cannot aim. Baseline 150–300 mm sets depth precision.

### 2.3 Temporal channel — $95

| # | Part | MPN | Qty | ~USD | Notes |
|---|---|---|---|---|---|
| 9 | Si PIN photodiode, fast | **BPW34** | 2 | 1.20 | 100 ns rise → >1 MHz bandwidth |
| 10 | Transimpedance amp | **OPA381** / LTC6268-10 | 2 | 4.50 | Start Rf = 100 kΩ, Cf = 1 pF |
| 11 | 12-bit ADC, 1 MSPS | **ADS7042** | 2 | 3.80 | Or RP2040 ADC at 500 kSPS to start |
| 12 | IR bandpass filter, 10 mm | Thorlabs **FB850-10** | 2 | 38.00 | Same job as #6, for the photodiode |

**Why a photodiode is the critical part.** To resolve 800 Hz you need ≥1.6 kSPS; for a
clean FFT, ≥10 kSPS. **No camera can do this** — a 60 fps sensor aliases the signal
into garbage. A $1 photodiode can.

Gain-bandwidth: BPW34 has ~50 pF junction capacitance. With Rf = 100 kΩ the pole is
`1/(2π·100k·50p) ≈ 32 kHz` — ample. You do not need exotic parts; you need **short,
guarded traces** and a clean ground plane, because this node is 100 kΩ+ and will hum.

### 2.4 Aiming — $650 (or $18 for a demo that does not work)

| # | Part | MPN | ~USD | Notes |
|---|---|---|---|---|
| 13 | Galvo scanner, 5 mm mirror | **GVS005** | 250.00 | **Preferred.** A mosquito crosses 5 mm in ~5 ms; a servo takes ~150 ms. |
| 14 | Galvo driver + controller | Thorlabs **GVS012** | 400.00 | |
| — | *(Budget fallback)* MG996R + PCA9685 | | 18.00 | Accepts ~10% hit rate. Demo only. |

**This is a hard cost cliff.** Servos are the wrong actuator by two orders of
magnitude in response time. If the budget cannot absorb a galvo, the honest scope is
**detection only** — which is still a genuinely useful instrument.

### 2.5 Real-time + safety controller — $64

| # | Part | MPN | ~USD | Notes |
|---|---|---|---|---|
| 15 | **RP2040** (Pico) | SC0915 | 4.00 | Photodiode sampling, FFT trigger, **and the interlock** |
| 16 | Raspberry Pi 5 (4 GB) | SC1112 | 60.00 | Vision, tracking, UI |

**Why a separate MCU:** the interlock must run even if the vision pipeline hangs. A
watchdog on the Pi cannot protect against the Pi itself. The RP2040 owns the laser
enable line and drops it in hardware if the heartbeat stops.

---

## 3. Budget summary

| Section | Cost |
|---|---|
| Illumination | $55 |
| Spatial channel | $180 |
| Temporal channel | $95 |
| Aiming (galvo, preferred) | $650 |
| Aiming (servo, demo only) | $18 |
| Controllers | $64 |
| **Detector head, no aiming** | **$330** |
| **Detector head + galvo** | **$980** |

**The bandpass filters are $166 of that — half the detector head.** They are also the
parts most likely to be skipped and the parts whose absence makes the system simply
not work. **Buy the filters.** If the budget is tight, buy the filters and use
cheaper cameras.

---

## 4. Build order

**Step 1 — the go/no-go experiment (~$120).**
Illuminator + retroreflector + **one** camera + **one** filter. Point at the
retroreflector; confirm a flat bright field with no room-light clutter.

> If the field is not flat, **nothing downstream will work.** Stop and fix this.

**Step 2 — add the photodiode channel.**
Wave a tuning fork (or play a 500 Hz tone near a piece of paper) across the curtain.
Confirm you see the flicker in the FFT. This validates the temporal channel before
you spend on a second camera.

**Step 3 — add the second camera.**
Triangulate a known object at a known distance. Verify size estimation to ±20%.

**Step 4 — catch a real mosquito.**
Net one, chill it to immobility, place it in the curtain, let it fly. Confirm
detection **and a correct wingbeat reading**.

**Step 5 — only now consider the laser.** The detector is useful on its own — as a
*monitor* it tells you when and where mosquitoes are active, which is valuable even
if you never fire a beam.

---

## 5. Verification

The wingbeat classifier ships with a self-test:

```bash
cd detection && python wingbeat.py
```

```
12/12 passed
  PASS   420.0 Hz  Anopheles/Culex/Toxo - all female
  PASS   600.0 Hz  aegypti female OR stephensi male
  PASS   520.0 Hz  local=Aedes albopictus -> single candidate, fire=True
```

**Two results you must understand before trusting the species output:**

**1. Wingbeat frequency cannot identify species and sex.** The bands overlap:

```
Female union:  306 - 664 Hz
Male union:    447 - 982 Hz
Overlap:       447 - 664 Hz   <-- 217 Hz, i.e. most of the range
```

A 600 Hz reading is *simultaneously* "Aedes aegypti female" and "Anopheles stephensi
male". Only below **447 Hz** is sex unambiguous. The frequency channel is an excellent
**mosquito / not-mosquito filter** — which is the safety-critical job — but it is not
a species identifier. **Calibrate to your local species**; that single step collapses
the ambiguity.

**2. The species gate defaults to ON.** `require_wingbeat: true` in
`system/config.yaml`. The laser refuses to fire without positive photodiode evidence.
A gate that defaults to off is not a safety gate.

---

## 6. Safety

The detector head alone is **Class 1 and safe to build on a bench.** The kill stage is
not — a 3 W beam can blind you permanently, and the margin is roughly **3.5 million×**
on the retina. Full analysis in `../laser_power_and_safety.md`.

If you build the laser stage:

- **Enclose the beam path.** Do not copy the open-air research configuration.
- **Hardware interlock first** — pull-down resistor on the enable line, owned by the
  RP2040, so the beam dies when the software does.
- **Never fire at a stationary target.** A mosquito cannot hold still; a finger can.
- **Prefer 10.6 µm CO₂ or 1.47 µm** if you build a beam. Comparable power to green,
  and absorbed before the retina, which shrinks the exclusion zone from "most of the
  room" to **under a metre**.
- **Not near children or pets. Ever.**

---

## 7. What it will not do

- **Wind.** The curtain is a 2D plane. An insect 5 cm in front of or behind it is
  invisible. This is a *fence*; most mosquitoes cross it. It is not a room scanner.
- **Dense swarms.** One curtain, one shadow at a time. Multiple simultaneous targets
  confuse the blob detector.
- **Long range.** 3 m is the design point, matched to the laser. Extending the
  curtain does not extend the beam.
- **Cost.** A real system is ~$1,000, not $200.
