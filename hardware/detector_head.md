# Sensor / Detector Head — Hardware Design

**Version:** 2.0
**Supersedes:** camera-only design in `specs.md`
**Role:** The detection front-end. This is the piece you build first. The laser is
downstream of it and is *not* required for this unit to be useful or testable.

---

## 0. Why this document exists

The v1 spec in `specs.md` assumed a single USB/webcam looking at a room and an ML
model finding "mosquitoes" in the pixels. That does not work, and it is worth being
blunt about why, because the reason determines the whole hardware architecture:

- A mosquito is **2–3 mm** at **0.5–3 m**. At 3 m with a 175° lens that is **under
  2 pixels**. No classifier will separate it from a dust mote.
- A camera at 60 fps samples 60 times a second. A mosquito beats its wings at
  **400–800 Hz**. The camera aliases the wingbeat into noise. The single most
  reliable mosquito discriminator in the literature is **unavailable to a camera**.
- Room lighting produces clutter that swamps a 2-pixel target.

The fix is not a better model. It is **different physics**: illuminate a thin
*plane* of IR light, look *along* that plane against a retroreflector, and detect
the **silhouette** and its **wingbeat oscillation**. That is the architecture
Intellectual Ventures used for the Photonic Fence, and it is what makes a
sub-pixel insect detectable.

So the detector head is not "a camera". It is a **light-curtain sensor**.

---

## 1. Operating principle

```
   POST A                                                    POST B
   ┌──────────────────────────┐              ┌──────────────────────────┐
   │  850nm IR ILLUMINATOR    │───light─────▶│   RETROREFLECTOR         │
   │  (fan beam, ~1m tall)    │              │   (3M Scotchlite)        │
   │                          │◀──returns────│                          │
   │  CAMERA (global shutter) │              │   (passive)              │
   │  PHOTODIODE + TIA        │              │                          │
   │  LASER (green, 532nm)    │              │                          │
   └──────────────────────────┘              └──────────────────────────┘
              ▲
              │  insect crossing the curtain casts a shadow on the
              │  retroreflector → dark pixel cluster against bright background
```

1. **Illuminate.** An 850 nm IR fan beam floods the retroreflector on the far post.
   Because it is retroreflective, the light returns *to source* — the camera sees a
   uniformly bright field and **almost no background clutter**. This is the trick
   that beats room lighting.
2. **Detect.** An insect crossing the curtain casts a sharp **shadow**. Silhouette
   contrast is ~100:1, not the ~5:1 you get from a lit insect against a wall.
3. **Measure size.** Shadow diameter in pixels → true size, once range is known.
   Anything >2 cm is rejected immediately (bird, hand, wasp).
4. **Measure wingbeat.** The shadow *flickers* as wings occlude the return beam.
   This flicker is the wingbeat frequency. **400–800 Hz depending on species and sex**
   (see §4). This is the species discriminator.
5. **Localise.** A second camera (stereo pair) or a second curtain at an angle gives
   range and 3D position → pan/tilt aim vector.

---

## 2. Why the photodiode is the critical part

**This is the single most important design decision in the whole head.**

| Sensor | Sample rate | Can see 400–800 Hz wingbeat? |
|---|---|---|
| Rolling-shutter camera (IMX219, typical webcam) | 30–60 fps | **No** — aliases into garbage |
| Global-shutter camera (OV9281) | 120 fps | Marginal — Nyquist needs ≥1600 fps |
| **Fast photodiode + TIA** | **>1 MHz** | **Yes, comfortably** |

To resolve 800 Hz you need ≥1.6 kSPS, and to get a clean FFT you want ≥10 kSPS.
A camera cannot do this. A **silicon PIN photodiode** can, for about $1.

The photodiode is placed **co-axial with the return beam** (behind a beamsplitter or
simply beside the camera looking at the same retroreflector). It integrates *all*
returned light, so it produces a single time-series: `bright → flicker → bright`
as the insect crosses. FFT that burst → dominant frequency → species ID.

**Design consequence:** the detector head has two parallel channels — a *spatial*
channel (camera, for position and size) and a *temporal* channel (photodiode, for
species). Neither alone is sufficient.

---

## 3. Component list

### 3.1 Illumination

| # | Part | MPN | Qty | ~USD | Notes |
|---|---|---|---|---|---|
| 1 | IR emitter, 850 nm, 1 W Oslon | **SFH 4715S** | 8 | 2.50 | 850 nm chosen: cheap, invisible, matches Si QE peak. 940 nm is more invisible but needs ~2× the power. |
| 2 | Fan-beam collimator, 7° × 60° | Carclo 10048 | 8 | 1.20 | Spreads each emitter into a vertical fan |
| 3 | Constant-current LED driver, 1 A | **AL8860** or TPS92515 | 2 | 1.80 | Do **not** drive these from a resistor — thermal drift wrecks the link budget |
| 4 | Retroreflective sheeting, 300 × 1000 mm | **3M Scotchlite 983-10** (diamond grade) | 1 | 18.00 | The far-post target. Diamond grade returns ~1200 cd/lx/m². |

**Why 850 nm and not a laser line:** a laser line is brighter and better collimated,
but it is a **Class 3B eye hazard** pointed across a room and it produces *speckle*
that ruins silhouette sharpness. An LED fan beam is Class 1 (eye-safe), diffuse, and
speckle-free. Use LEDs for illumination. Reserve the laser for the kill pulse only.

### 3.2 Spatial channel (position + size)

| # | Part | MPN | Qty | ~USD | Notes |
|---|---|---|---|---|---|
| 5 | Global-shutter mono cam, 1 MP, 120 fps | **OV9281** (Arducam B0332) | 2 | 32.00 | Global shutter is mandatory — rolling shutter skews a moving shadow. Mono: no Bayer filter, ~2× more IR sensitivity. |
| 6 | 850 nm bandpass filter, 25 mm | Thorlabs **FB850-40** | 2 | 45.00 | **Not optional.** Blocks room light, passes only your illuminator. This alone is the difference between working and not working. |
| 7 | M12 lens, 8 mm f/1.4 | — | 2 | 12.00 | Narrower than the v1 wide-angle. You need pixels-per-millimetre, not field of view. |
| 8 | CSI/ USB3 cable, 300 mm | — | 2 | 4.00 | |

**Why two cameras:** a single camera gives you a bearing but not a range. Without
range you cannot compute true insect size (a 2-pixel blob at 0.5 m and at 3 m look
identical) and you cannot aim a laser. Two cameras 150–300 mm apart, viewing the
same curtain, triangulate. The baseline sets your depth precision.

### 3.3 Temporal channel (species ID)

| # | Part | MPN | Qty | ~USD | Notes |
|---|---|---|---|---|---|
| 9 | Si PIN photodiode, fast | **BPW34** (or SFH 203 P) | 2 | 1.20 | 100 ns rise time → >1 MHz usable bandwidth |
| 10 | Transimpedance amp, 100 MHz | **OPA381** or LTC6268-10 | 2 | 4.50 | Feedback R sets gain; feedback C sets bandwidth. Start Rf = 100 kΩ, Cf = 1 pF. |
| 11 | 12-bit ADC, 1 MSPS | **ADS7042** | 2 | 3.80 | Or use the RP2040's ADC at 500 kSPS (see §3.5) — good enough to start |
| 12 | IR bandpass filter, 850 nm, 10 mm | Thorlabs **FB850-10** | 2 | 38.00 | Same job as #6, for the photodiode |

**Gain-bandwidth reality check.** BPW34 has ~50 pF junction capacitance. With a
100 kΩ transimpedance you get a pole at `1/(2π·100k·50p) ≈ 32 kHz` — plenty for
800 Hz. Push Rf to 1 MΩ for more gain and the pole drops to 3.2 kHz — still fine.
You do not need exotic parts here; you need **short, guarded traces** and a clean
ground plane, because this node is a 100 kΩ+ impedance and will hum if you let it.

### 3.4 Aiming (for when the laser is added)

| # | Part | MPN | Qty | ~USD | Notes |
|---|---|---|---|---|---|
| 13 | Galvanometer scanner, 5 mm mirror | **GVS005** (Thorlabs) | 1 | 250.00 | **Preferred over servos.** A mosquito crosses 5 mm in ~5 ms; a hobby servo takes ~150 ms to move. Servos cannot track this target. A galvo moves in <1 ms. |
| 14 | Galvo driver + controller | Thorlabs **GVS012** kit | 1 | 400.00 | |
| — | *(Budget fallback)* MG996R servos + PCA9685 | — | 2 | 18.00 | Accepts ~10% hit rate. Only for a demo that proves the pipeline, not for real use. |

**This is a hard cost cliff and you should know about it now.** The v1 BOM budgeted
$15.50 for MG996R servos. Those are the wrong actuator by two orders of magnitude in
response time. Realistic tracking needs a galvo, which costs ~40× more. If the budget
cannot absorb that, the project's honest scope is **detection only**.

### 3.5 Real-time + safety controller

| # | Part | MPN | Qty | ~USD | Notes |
|---|---|---|---|---|---|
| 15 | **RP2040** (Raspberry Pi Pico) | SC0915 | 1 | 4.00 | Dedicated hard-real-time duties: photodiode sampling, wingbeat FFT trigger, **and the interlock**. Must not share a CPU with OpenCV. |
| 16 | Raspberry Pi 5 (4 GB) | SC1112 | 1 | 60.00 | Vision, tracking, UI. |

**Why a separate MCU:** the interlock must run even if the vision pipeline hangs,
crashes, or is mid-frame. A watchdog on the Pi cannot protect against the Pi itself.
The RP2040 owns the laser enable line and drops it in hardware if it stops receiving
a heartbeat.

---

## 4. Wingbeat frequency — the species discriminator

Measured fundamentals (Hz). This is what the photodiode channel is looking for.

| Species | Female | Male |
|---|---|---|
| *Aedes aegypti* | 514–664 | 721–982 |
| *Aedes albopictus* | 517 ± 62 | 687 ± 113 |
| *Aedes japonicus* | 338 ± 32 | 503 ± 56 |
| *Anopheles stephensi* | ~400 | ~600 |
| *Toxorhynchites brevipalpis* | ~400 | ~500 |
| *Culex pipiens* | ~380–520 | ~550–750 |

Two things this buys you:

1. **Reject non-targets.** A housefly (~200 Hz), a moth (~50 Hz), a bee (~230 Hz)
   all fall outside the mosquito band. Reject them and you slash false fires.
   **This works, and it is the safety-critical use of the frequency channel.**
2. **Narrow down the sex.** In most species the female beats *lower* than the male.

### ⚠ The bands overlap — frequency alone cannot identify species and sex

This is worth stating loudly because it is easy to assume otherwise, and the
assumption would be wrong. Cross-checking the table:

```
   Female union:  306 - 664 Hz
   Male union:    447 - 982 Hz
   Overlap:       447 - 664 Hz      <-- 217 Hz wide, i.e. most of the range
   Unambiguous female:  only 306 - 447 Hz
```

A reading of **600 Hz** is simultaneously *Aedes aegypti female* (514–664) and
*Anopheles stephensi male* (540–660). A reading of 520 Hz has **six** candidate
species spanning both sexes. Only below **447 Hz** is the sex unambiguous.

**Consequences for the design:**

- The frequency channel is a **mosquito / not-mosquito** filter. It is excellent at
  that and it is what makes the system safe.
- It is **not** a species or sex identifier on its own. Do not build a fire decision
  that depends on it.
- **Calibrate to your local species.** If you know only *Culex pipiens* and *Aedes
  albopictus* are present, pass that list and the ambiguity collapses — a 520 Hz
  reading resolves to a single candidate. This is the single highest-value
  calibration step in the whole build, and it costs nothing but a survey of what
  actually bites you.
- Where frequency is ambiguous, the **spatial channel** has to resolve it: size,
  whether the insect is in flight vs hovering, and dwell time.

The reference implementation in `detection/wingbeat.py` encodes all of this. It
returns a *candidate set* rather than a confident species name, and it refuses to
fire when the reading is ambiguous — including when all candidates are female but one
is a non-biting species. Its self-test asserts the ambiguity rather than papering
over it:

```
12/12 passed
  PASS   420.0 Hz  Anopheles/Culex/Toxo - all female
        -> sex certain, species not, fire=False   (conservative: Toxo doesn't bite)
  PASS   600.0 Hz  aegypti female OR stephensi male
        -> 4 candidates spanning both sexes, fire=False
  PASS   520.0 Hz  local=Aedes albopictus -> single candidate, fire=True
```

**Tuning forks, not sirens.** The band is narrow and species-dependent, so the
classifier is a *matched filter* over frequency, not a generic "is this a bug" model.
A Moroccan *Culex* is not a Washington *Anopheles*, and the frequency table above
will not transfer for free.

---

## 5. Signal chain and budget

```
  850nm LED ──▶ retroreflector ──▶ 850nm BPF ──┬──▶ OV9281 ──▶ shadow blob ──▶ size + position
  (fan, 1W×8)   (returns to src)               │                              │
                                               │                              ▼
                                               └──▶ BPW34 ──▶ TIA ──▶ ADC ──▶ FFT ──▶ wingbeat
                                                    (fast)              (RP2040)   (species)
                                                                                       │
                                        size OK?  position OK?  wingbeat OK?  ◀─────────┘
                                                     │
                                                     ▼
                                        ┌────────────────────────┐
                                        │  AIM + FIRE REQUEST    │  (laser stage)
                                        │  gated by interlock    │
                                        └────────────────────────┘
```

**Optical budget, 3 m post spacing.** The dominant loss is geometric: the return
cone of a retroreflector is not a point, it has ~1–2° of spread, so return power
falls roughly as `1/range²` beyond a few metres. Budget for it:

- 8 × 1 W emitters, fan-spread over 1 m × 3 m → ~2.7 W/m² at the retroreflector
- Diamond-grade return, ~30% effective retroreflection into a 2° cone
- At 3 m, on-axis return irradiance ≈ **0.1–0.3 W/m²** at the detector
- With a 25 mm f/1.4 lens (≈ 2×10⁻⁴ m² aperture) → **~20–60 µW** on the camera sensor

That is comfortable for a mono sensor with the bandpass filter. **At 10 m it drops to
~2–5 µW** and you will need either more emitters or a laser illuminator. Keep the
first build at **≤3 m** post spacing.

---

## 6. Interlock architecture (build this in from day one)

Even though this document covers the detector, the interlock belongs to the head,
because the head is what knows whether a human is present.

| Layer | Trigger | Action | Owned by |
|---|---|---|---|
| **L0 — Hardware** | No heartbeat on the enable line for >50 ms | Laser enable line pulled low by pull-down resistor | RP2040 + resistor |
| **L1 — Human detect** | Blob >2 cm, or skin-tone region, or face in frame | Hard stop, require manual re-arm | Pi 5 vision |
| **L2 — Geometry** | Aim vector outside calibrated curtain volume | Clamp, do not fire | Pi 5 |
| **L3 — Species** | Wingbeat outside 350–700 Hz female band | Do not fire | RP2040 |
| **L4 — Dwell** | Single target held >150 ms | Abort (a mosquito will not hold still; a finger will) | Pi 5 |

**L0 is the one that matters.** Every other layer is software, and software fails.
A pull-down resistor on the laser enable line costs $0.01 and means that when
everything else dies, the laser is off.

**Beam path containment.** Even with all five layers, enclose the beam path wherever
practicable. The IV design fires across open air; that is a research posture, not a
backyard one. A first build should fire *downward into an enclosure* with a beam
dump, or across a wall of the house where the far post is the beam stop.

---

## 7. Bill of materials summary

| Section | Cost (USD) |
|---|---|
| Illumination | ~$55 |
| Spatial channel (2 cameras + filters) | ~$180 |
| Temporal channel (photodiode + TIA + ADC) | ~$95 |
| Aiming (galvo, *preferred*) | ~$650 |
| Aiming (servo, *demo only*) | ~$18 |
| Controllers (Pi 5 + RP2040) | ~$64 |
| **Detector head, no aiming** | **~$330** |
| **Detector head + galvo** | **~$980** |
| **Detector head + servo demo** | **~$348** |

**The bandpass filters (#6, #12) are ~$166 of that — half the detector head.** They
are also the parts most likely to be skipped by a first-time builder and the parts
whose absence will make the system simply not work. Do not skip them. If the budget
is tight, buy the filters and use cheaper cameras.

---

## 8. Build order

1. **Illuminator + retroreflector + one camera + one filter.** Point at the
   retroreflector, confirm a flat bright field with no room-light clutter. This is
   the go/no-go experiment and costs ~$120. If the field is not flat, nothing
   downstream will work.
2. **Add the photodiode channel.** Wave a tuning fork (or a phone playing a 500 Hz
   tone near a piece of paper) across the curtain. Confirm you see the flicker in the
   FFT. This validates the temporal channel before you spend on a second camera.
3. **Add the second camera.** Triangulate a known object at a known distance.
   Verify size estimation is accurate to ±20%.
4. **Catch a real mosquito.** Net one, chill it to immobility, place it in the
   curtain, let it fly. Confirm detection + correct wingbeat reading.
5. **Only now** consider the laser. The detector is useful on its own — as a
   *monitor*, it tells you when and where mosquitoes are active, which is genuinely
   valuable even if you never fire a beam.

---

## 9. What this design does not solve

- **Wind.** The curtain is a 2D plane. A mosquito 5 cm in front of or behind it is
  invisible. The IV design accepts this — it is a *fence*, and most mosquitoes cross
  it. It is not a room scanner.
- **Multiple simultaneous targets.** One curtain, one shadow at a time, mostly.
  Dense swarms will confuse the blob detector.
- **Cost.** A real system is ~$1,000, not $200. The v1 budget was optimistic by ~5×.
- **The laser is still the hard part.** Everything here is Class 1 and safe to build
  on a bench. The kill stage is not, and it is covered separately in
  `laser_power_and_safety.md`.
