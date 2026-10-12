# Laser Power Requirements & Human Safety

**Question answered:** what power kills a mosquito, and can it hurt a human?

**Short answer:** you need **~2–3 W optical for 25 ms** (green) to kill a mosquito
reliably. That same beam **can permanently blind a human**, and the margin between
"kills the bug" and "damages the eye" is not large — it is roughly **1000× in your
favour on the retina, and only ~10–100× on skin**. The system is safe only because of
*geometry and timing*, never because the laser is weak.

All kill numbers below are from the primary literature, not estimates:
Keller et al., *Laser induced mortality of Anopheles stephensi mosquitoes*,
Sci. Rep. **6**, 20936 (2016) — the Intellectual Ventures Lab study.

---

## 1. What it takes to kill a mosquito

The governing quantity is **fluence** — energy per unit area, J/cm² — not raw power.
The paper's LD90 values (dose killing 90% of subjects):

| Wavelength | Beam Ø | Pulse | **LD90 fluence** | **LD90 power** | Pulse energy |
|---|---|---|---|---|---|
| **532 nm** (green) | 2.3 mm | 25 ms | 1.78 J/cm² | **3.0 W** | 74 mJ |
| 532 nm | 2.0 mm | 5.7 ms | 1.7 J/cm² | 8.5 W | 48 mJ |
| 532 nm | 5.8 mm | 20 ms | 0.64 J/cm² | 8.5 W | 170 mJ |
| **532 nm** (Q-switched) | 2.5 mm | 10 ns | 0.25 J/cm² | **1 MW peak** | 10 mJ |
| 1064 nm (NIR) | 2.5 mm | 10 ns | 0.53 J/cm² | 2.6 MW peak | 26 mJ |
| 1064 nm (long pulse) | 2.5 mm | 90 µs | 1.84 J/cm² | 1.0 kW | 90 mJ |
| 976 nm (diode) | 2.5 mm | 25 ms | 5.57 J/cm² | **10.9 W** | 273 mJ |
| 1470 nm | 2.5 mm | 25 ms | 4.02 J/cm² | 7.9 W | 197 mJ |
| **10,600 nm** (CO₂) | 2.5 mm | 25 ms | 1.06 J/cm² | **2.1 W** | 52 mJ |
| 10,600 nm | 2.5 mm | 4.3 ms | 0.89 J/cm² | 10.2 W | 44 mJ |

### The practical takeaways

1. **Green (532 nm) is the most efficient visible option** — 3 W for 25 ms. Mosquito
   tissue is melanin-pigmented, and melanin absorbs green strongly. That is why green
   wins over near-IR: at 1064 nm the absorption is several times lower, so the required
   fluence roughly doubles.

2. **The blue-green band is where the cheap lasers are.** The paper explicitly notes
   that just under 3 W at 25 ms "is easily achieved by several laser technologies in
   the blue to green wavelength spectrum." You do not need an exotic source.

3. **A shorter pulse does NOT save much power.** Going 25 ms → 5.7 ms at the same
   wavelength kept the required fluence flat (1.78 → 1.7 J/cm²). It took a **Q-switched
   10 ns pulse** to cut the fluence meaningfully (down to 0.25 J/cm²) — and that trades
   *average* power for **1 MW of peak power**, which is a far worse eye hazard. Short
   pulses are the wrong direction for safety.

4. **A wider spot is *less* efficient overall.** Expanding 2.3 mm → 5.8 mm cut the
   fluence 3× but over 6× the area, so *net pulse energy went up* (74 mJ → 170 mJ).
   Tight focus is better.

5. **You cannot boil the mosquito.** The paper notes it takes under ~2 J to boil the
   water in a 1 mg insect, but the *lethal* dose is 74 mJ. The kill is **localised
   thermal damage** — the beam burns a small hole in the thorax. At the 8.5 W / 5.7 ms
   condition the researchers recorded **a visible plume of smoke**; at 3 W / 25 ms the
   mosquito showed no outward damage but went moribund and died within 24 h.

### The number to design around

> **3 W of green (532 nm), 25 ms pulse, ~2.3 mm spot at the target.**
> 74 mJ per shot. That is the baseline kill condition.

Note this was measured on **anaesthetised, immobilised** mosquitoes. In-flight dosing
is expected to need similar energy (the paper's own follow-up), but it has not been
confirmed — assume you may need 2–3× more until proven.

---

## 2. Can it harm a human? Yes. Here is exactly how much.

Two separate questions, because the eye and the skin behave completely differently.

### 2.1 The eye — the real hazard

For **400–1400 nm** (which includes both 532 nm green and 1064 nm NIR), the eye's
cornea and lens focus the beam onto the retina, concentrating it by a factor of
roughly **100,000×**. The ANSI Z136.1 maximum permissible exposure (MPE) for the
retinal hazard region is on the order of:

> **MPE ≈ 5 × 10⁻⁷ J/cm²** for 400–700 nm, exposure 10⁻⁹ – 1.8 × 10⁻⁵ s

Compare that to the kill requirement: **1.78 J/cm²**. The lethal-for-mosquito fluence
is about **3.5 million times** the human ocular MPE.

That factor is not the safety margin, because the geometries differ — the MPE is
specified at the *cornea* over a 7 mm pupil, while the kill fluence is at the
*target* spot. But the order of magnitude is the point: **this is not a beam you can
be casual around.**

The concrete failure mode, straight from laser-safety literature:

- A beam with an irradiance of **1 mW/cm² entering the eye exposes the retina to
  100 W/cm².**
- **A 40 mW laser, focused, will instantly burn through paper.**
- **A low-power laser in the milliwatt range can burn the retina if focused onto it.**
- Damage can occur **in less time than a quarter-second blink reflex** with high-power
  lasers.
- If the burn lands on the **fovea**, most fine (reading and working) vision is lost.

Your kill laser is **3000 mW**, not 40 mW. A single stray reflection off a window, a
glasses lens, a shiny phone screen, or a spoon into someone's eye is a blinding event.
**There is no "safe" exposure of a 3 W green beam to an unprotected eye.**

### 2.2 The skin — less catastrophic, still real

Skin is much more forgiving than the retina, because it does not focus. The threshold
for *any* skin damage is roughly:

> **>2 × 10⁻² J/cm²** to cause skin damage (ANSI 1993), at these wavelengths

The mosquito LD90 is **1.78 J/cm²** — about **90× above the human skin damage
threshold**. So a 3 W / 25 ms pulse delivered to skin **will** cause a burn, not a
tickle. With a tightly focused beam it will evaporate tissue and produce a third-degree
burn.

The saving grace on skin is **spot size and dwell**. Your beam is focused to ~2.3 mm
at 3 m. A human is not 2.3 mm wide — most of the beam misses, and only a small patch
of skin receives the full fluence. And the pulse is 25 ms, not continuous. That is why
the system can be operated at all: **the beam is aimed at a 3 mm target, and the
exposure is 25 thousandths of a second.** It is not that the beam is safe; it is that
the *engagement* is brief and small.

### 2.3 The summary table

| | Mosquito kill | Human eye | Human skin |
|---|---|---|---|
| **Threshold** | 1.78 J/cm² (LD90) | ~5 × 10⁻⁷ J/cm² (MPE, retina) | >2 × 10⁻² J/cm² |
| **Ratio vs kill** | 1× | **~3,500,000× more sensitive** | **~90× more sensitive** |
| **Outcome if hit** | dies in 24 h | permanent retinal burn, possible blindness | burn; third-degree if focused |
| **Reversibility** | n/a | **none** | partial, scarring |

**Direct answer to your question:** at the power needed to kill a mosquito, the laser
**can absolutely harm a human** — permanently, in the case of the eye. The design is
safe only if it *never fires when a person could be in the beam path*. That is an
engineering problem, and it is the next section.

---

## 3. How the system is made safe (the part that actually matters)

You do not make this safe by using a weaker laser — a weaker laser does not kill the
mosquito. You make it safe by **guaranteeing the beam is only ever emitted into an
empty curtain.**

### 3.1 The five interlock layers

| Layer | What it checks | Response | Latency |
|---|---|---|---|
| **L0 — Hardware watchdog** | Heartbeat on the enable line | Pull-down resistor kills the beam | **<1 ms, in hardware** |
| **L1 — Human presence** | Blob >2 cm, skin-tone region, face detected | Hard stop + manual re-arm | 1 frame |
| **L2 — Geometry** | Aim vector inside the calibrated curtain | Clamp / refuse to fire | immediate |
| **L3 — Species** | Wingbeat in 350–700 Hz female band | Do not fire | <5 ms |
| **L4 — Dwell** | Same target held >150 ms | Abort (a mosquito moves; a finger does not) | 150 ms |

**L1 and L4 are the ones that save eyes.** L1 because a human is 100× larger than a
mosquito and trivially detectable. L4 because a mosquito *cannot hold still* — if the
target is stationary, it is not a mosquito, and you must not fire.

### 3.2 The exclusion zone is a real, computable number

The IV paper gives this directly:

> "For 25 ms pulses, the infrared wavelengths (which do not reach the retina) would
> require exclusion zones as small as **less than a meter**. Shorter wavelengths and
> pulse durations both lead to **larger exclusion zones**."

Read that carefully, because it is bad news for the green laser:

- **IR (10.6 µm CO₂, or 1.47 µm):** the beam does not reach the retina, so the
  exclusion zone is **<1 m**. This is the *safe* wavelength choice.
- **Green (532 nm):** it *does* reach the retina, so the exclusion zone is **much
  larger**. A 3 W green beam needs a substantial clear volume around it.

**This is a genuine design tension.** Green is the most *power-efficient* wavelength
(3 W vs 2.1 W for CO₂ — actually comparable) but the *least* eye-safe. The paper notes
that "laser cost and safety concerns scale nearly linearly with power, so there are
multiple factors pushing against the use of higher power lasers."

**Recommendation:** if you build a kill stage at all, prefer **10.6 µm CO₂** or
**1.47 µm**. CO₂ needs only 2.1 W (comparable to green), and because 10.6 µm is
absorbed by water in the *cornea* and never reaches the retina, the exclusion zone
collapses to under a metre. It is the wavelength that is both effective *and*
forgiving. It is also far more expensive and bulky than a diode — that is the trade.

### 3.3 Engineering controls (do all of these)

1. **Enclose the beam path.** The single most effective control. Fire downward into a
   box, or along a wall to a beam stop. An open-air room-crossing beam is a research
   posture you should not copy at home.
2. **Beam dump / backstop.** Class 3 and 4 beams must terminate in a fire-resistant
   absorber. Many surfaces that look matte are IR reflectors.
3. **No reflective surfaces in the curtain.** Windows, mirrors, screens, glasses,
   cutlery, glossy paint. Audit the room.
4. **Key switch + emission indicator + audible warning** before the beam can be armed.
5. **Interlocked enclosure** that cannot be opened without shutting the laser down
   (this is what makes it Class 1 as a system).
6. **Laser safety eyewear** rated for the wavelength and power, for anyone in the room
   during testing. For 532 nm at 3 W you need OD 5+ at 532 nm.
7. **Never test near pets or children.** Ever.
8. **One person, one hand on the emergency stop.** Not a hands-off automated test
   until the interlocks are proven.

---

## 4. Realistic scope, honestly

| Build | Wavelength | Power | Eye risk | Cost | Verdict |
|---|---|---|---|---|---|
| **Detection head only** | 850 nm LED | Class 1 | none | ~$330 | **Build this. Do this first.** Useful on its own as a monitor. |
| Aiming + Class 1 pointer | 532 nm | 1–5 mW | low | +$20 | Proves the tracking pipeline. Cannot kill anything. |
| **Full kill system, IR** | 10.6 µm CO₂ | 2.1 W | contained | ~$1,500+ | Realistic if enclosed. The *safe* kill option. |
| **Full kill system, green** | 532 nm | 3 W | **serious** | ~$800+ | Works, but needs a large exclusion zone. Hardest to make safe. |
| Q-switched anything | 532/1064 nm | 1 MW peak | **extreme** | $$$$ | Do not. Peak power makes the eye hazard far worse for little gain. |

**The blunt version:** the detection half of this project is a genuinely good,
buildable, useful engineering project that is safe to work on at a bench. The kill
half is a **laser weapon that fires autonomously at 3 W**, and the failure mode of a
bug in the targeting code is a person losing an eye. That is not a reason it cannot be
done — it is a reason it must be done with the interlocks first, the enclosure second,
and the beam last.

---

## 5. Sources

- **Keller, M. D. et al.** *Laser induced mortality of Anopheles stephensi mosquitoes.*
  Sci. Rep. **6**, 20936 (2016). doi:10.1038/srep20936 — all LD90 values, the
  exclusion-zone discussion, and the "plume of smoke" observation.
- **ANSI Z136.1** — maximum permissible exposure (MPE) tables; skin-damage threshold
  (>2 × 10⁻² J/cm²) and ocular MPE (5 × 10⁻⁷ J/cm²) as quoted in NOAA LJ-98-10C.
- **Lawrence Berkeley Lab, EHS** — *Laser Bio-effects*: 100,000× retinal concentration,
  the 40 mW / paper and 1 mW/cm² / 100 W/cm² figures.
- **University of Illinois DRS** — *Laser Hazards and Control Measures*.
- **Intellectual Ventures / IEEE Spectrum** — *Backyard Star Wars*: the <2 J boil
  figure and the "few tens of millijoules within a few milliseconds" conclusion.
- **Rockwell Laser Industries** — *Laser Standards and Classifications*: Class 1–4 AEL
  definitions.
