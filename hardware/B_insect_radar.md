# System B — Insect Radar (24 GHz Doppler)

**Detects flying insects at 1–5 m and reads their wingbeat frequency directly.**
This is radar entomology: a real, published field, and the hardware is cheap.

| | |
|---|---|
| **Range** | **1–5 m** (power-limited) |
| **Cost** | **$40–90** |
| **Licence** | **None** — 24.125 GHz is ISM / licence-exempt |
| **Build time** | One weekend |
| **Gives you** | Presence, range, speed, **wingbeat frequency** |

---

## 1. Why this design, and why the range is short

The radar equation for a small target:

```
   P_received = (P_t · G² · λ² · σ) / ((4π)³ · R⁴)
```

Everything interesting is in `R⁴`. Doubling range needs **16×** the power.

| Target | RCS σ | Range at 10 dBm, 20 dBi (24 GHz) |
|---|---|---|
| Mosquito (3 mm) | ~10⁻⁷ m² | **~1 m** |
| Housefly (6 mm) | ~10⁻⁶ m² | ~2 m |
| Large moth (30 mm) | ~10⁻⁴ m² | **~5 m** |
| Small drone | ~10⁻² m² | ~15 m |

So a $15 Doppler module genuinely sees insects — **at arm's length to a few metres.**
This is not a limitation to work around; it is the instrument. Radar entomology
instruments are all short-range, because the physics says so.

**Why 24 GHz and not X-band?** Shorter wavelength means (a) more Doppler shift per
unit speed, so wingbeat modulation is easier to extract, and (b) a small antenna has
useful gain. A 24 GHz module with a 4×4 patch array gives ~20 dBi in a 30 mm square.

---

## 2. Parts

| # | Part | ~USD | Notes |
|---|---|---|---|
| 1 | **CDM324** or **K-LC1a** 24 GHz Doppler module | 15 | The core sensor. CDM324 is the cheap one; K-LC1a (RFbeam) has better IF output. |
| 2 | **STM32F103 "Blue Pill"** or **RP2040** | 5 | 12-bit ADC at ≥100 kSPS. RP2040 is easier to source. |
| 3 | **Op-amp, low-noise** (OPA2333 / MCP6002) | 3 | Two-stage IF amplifier. The module's raw IF is millivolts. |
| 4 | **PCB or perfboard, 24 GHz-safe** | 8 | **Keep the IF trace short and guarded.** This node is high-impedance. |
| 5 | **Shielded enclosure** | 10 | 24 GHz leaks; a metal box prevents pickup and keeps it clean. |
| 6 | **Horn or lens** (optional) | 15 | Narrows the beam from ~80° to ~20°, raising gain and rejecting clutter. |
| | **Total** | **~$56** | ~$90 with horn and nice enclosure |

### Optional

| Part | ~USD | Gain |
|---|---|---|
| 24 GHz dielectric lens | 20 | Beam narrowing without a horn |
| Second module (I/Q pair) | 15 | Direction discrimination (approaching vs receding) |
| Precision mount + gimbal | 30 | Scan a volume instead of staring |

---

## 3. How it works

```
   24 GHz module
   ┌──────────────────────────┐
   │  TX ──── 24.125 GHz ────▶ │──── beam ────▶  insect (moving)
   │                          │◀─── echo ─────
   │  RX ──▶ mixer ──▶ IF out │
   └──────────────────────────┘
                    │
                    │  IF = Doppler shift
                    │  f_d = 2·v·f_c / c
                    │      = 2·v·24.125e9 / 3e8
                    │      = 161 Hz per (m/s)
                    ▼
        ┌───────────────────────┐
        │  amp (×1000, 2 stage) │
        └───────────┬───────────┘
                    ▼
        ┌───────────────────────┐
        │  ADC @ 100 kSPS       │
        └───────────┬───────────┘
                    ▼
        ┌───────────────────────┐
        │  FFT                  │
        │   • body Doppler      │ → speed (161 Hz per m/s)
        │   • wingbeat sidebands│ → species
        └───────────────────────┘
```

**The key insight:** a flying insect is not a rigid target. Its wings oscillate, and
that oscillation **modulates the Doppler return**. So the spectrum shows:

- a **central peak** = the insect's body velocity
- **sidebands** at ±wingbeat frequency = the wings

That means a single $15 module gives you **speed and species signature
simultaneously** — the same information the photodiode channel in system C gets
optically, but from radio.

### Numbers

| Quantity | Value |
|---|---|
| Doppler per m/s | **161 Hz** |
| Mosquito wingbeat | 350–800 Hz → sidebands at ±350–800 Hz |
| Insect flight speed | 0.5–2 m/s → 80–320 Hz body Doppler |
| Required ADC rate | ≥10 kSPS (100 kSPS comfortable) |
| Range resolution | none (CW radar) — range from amplitude only |

**Note the frequency overlap:** a mosquito at 2 m/s gives 322 Hz body Doppler, and its
wingbeat sidebands sit at 350–800 Hz *away* from that. Resolving them needs a clean
FFT, which is why the IF amplifier quality matters more than the module.

---

## 4. Calibration

This is the part people skip, and it is the part that makes the data real.

**Step 1 — verify the Doppler scale.** Hold a target moving at a known speed. A
metronome-driven pendulum, or a small fan with a known blade-tip speed. Confirm
161 Hz per m/s. If it is off, your module's frequency is not exactly 24.125 GHz
(they vary ±30 MHz) — measure the actual centre frequency and correct.

**Step 2 — establish the wingbeat bands for YOUR insects.** This is the same lesson as
the optical channel: **the published frequency tables do not transfer between
regions.** Catch a local mosquito, hold it in front of the module, record its
spectrum, and build your own table. Ten specimens is enough to define a band.

**Step 3 — set the detection threshold.** Point the module at an empty room. Record
the noise floor. Set the threshold at 6–10 dB above it. Then confirm a single
tethered mosquito trips it.

**Step 4 — characterise clutter.** Rain, moving foliage, and fans all produce
Doppler. A 20° horn helps enormously here by narrowing the beam.

---

## 5. What it will and will not do

| Can | Cannot |
|---|---|
| Detect insects at 1–5 m | Detect them at 100 m |
| Measure body speed | Measure range (CW radar has no range gate) |
| Read wingbeat frequency | Identify species without local calibration |
| Count insects crossing a beam | Resolve *position* — the beam is a cone, not a pixel |
| Run continuously, unattended | Distinguish two insects in the beam at once |

**The honest framing:** this is a **volume activity monitor**. It tells you *how much*
insect flight is happening and *what kind*, in a small region. It is not an aiming
sensor — it cannot tell you where within the beam the insect is.

That is exactly why system C exists: when you need a *position* good enough to aim
at, you need optics.

---

## 6. Where it fits

**Best uses:**

- **Field monitoring** — how does insect activity vary by hour, weather, location?
- **Species survey** — build a local wingbeat library, compare against traps
- **Trigger for system C** — a cheap always-on radar tells you an insect is present;
  then the optical curtain takes over for identification and aiming
- **Teaching** — it makes the radar equation and Doppler concrete for $50

**The radar-plus-optics combination is genuinely good design:** radar for continuous
cheap presence detection over a small volume, optics for the precision work when it
matters. Neither alone is sufficient; together they cover the problem.

---

## 7. Legal

24.125 GHz is an **ISM band**, licence-exempt for low-power devices under
ITU Radio Regulation 5.150. The modules sold for this purpose are certified for it.

| Action | Status |
|---|---|
| Operating a certified 24 GHz Doppler module | **Legal** — no licence |
| Building your own from a certified module | **Legal** |
| Modifying the module to raise power | **No** — voids certification, may be illegal |
| Building a 24 GHz transmitter from scratch | **Requires checking local rules** — don't assume |

Keep the module within its certified power (typically ≤10 dBm EIRP) and you are fine.
Do not amplify the transmit side.

---

## 8. Sources

- **Chapman, Drake & Reynolds** — *Recent insights from radar studies of insect
  flight*, Annu. Rev. Entomol. (2011). The standard review of radar entomology.
- **Riley** — *Radar cross section of insects*, Proc. IEEE (1985).
- **RFbeam** application notes on 24 GHz Doppler for movement sensing.
- The wingbeat frequency tables in `../detection/wingbeat.py` (Aedes, Anopheles,
  Culex) apply here directly — same biology, different sensor.
