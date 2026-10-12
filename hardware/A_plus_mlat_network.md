# System A+ — MLAT Receiver Network

**Triangulating aircraft from multiple passive receivers.** The closest legal thing to
owning a radar: you locate aircraft that broadcast *no position at all* by comparing
when each receiver heard the same frame.

| | |
|---|---|
| **Range** | **50–150 km** usable accuracy (see §5 — this is a *local* technique) |
| **Cost** | **$400–1,600** for 4 stations |
| **Licence** | **None** — receive only |
| **Needs** | **GPS-disciplined clocks.** Non-negotiable, see §2. |
| **Software** | `adsb/mlat.py` (done, solver validated) |

---

## 1. Why this works, and what it adds

ADS-B aircraft *tell* you where they are. Many aircraft (military, some GA, older
transponders) broadcast **Mode-S only** — a short squawk with no position field. A
radar sees them; a single ADS-B receiver cannot.

But if **three or more receivers** hear the same frame, the *differences in arrival
time* place the aircraft on a hyperboloid. Intersecting those gives a fix. This is
TDOA multilateration, and it is entirely passive.

```
        aircraft (no position broadcast)
                 ○
              ╱  │  ╲
     t=+40us ╱   │   ╲ t=+12us
           ╱     │     ╲
      RX-1      RX-2     RX-3        ← 3+ receivers hear the same frame
        └────────┴────────┘
       differences in arrival time
         → hyperboloids → intersect → position
```

**What it adds over plain ADS-B:** you now see aircraft that broadcast *nothing about
their position*, and you can independently verify ADS-B positions rather than trusting
them.

---

## 2. The clock problem — read this before buying anything

**Mode-S frames carry no timestamp.** Each receiver stamps arrival locally, so the
receivers' clocks *are* the instrument. A clock error `dt` becomes a range error
`c·dt`:

| Clock method | Range error | Position error (good geometry) | Verdict |
|---|---|---|---|
| 1 ns rubidium | 0.3 m | **18 m** | works |
| **10 ns GPSDO** | 3.0 m | **16 m** | **works — this is the standard** |
| 100 ns GPSDO | 30.0 m | 72 m | works |
| 1 µs GPS, undisciplined | 300 m | 784 m | marginal |
| 1 ms NTP | 300 km | unusable | **useless** |

*Measured by the solver at GDOP 1.08, so the clock is the only variable.*

**One millisecond of clock error is three hundred kilometres of position error.** This
is why every real MLAT network uses a GPS-disciplined oscillator, and why NTP is not
an option.

---

## 3. Parts — per station

| # | Part | ~USD | Notes |
|---|---|---|---|
| 1 | **RTL-SDR Blog V3** | 30 | As system A |
| 2 | **1090 MHz antenna** | 10 | As system A; height matters more than gain |
| 3 | **GPSDO module** (10 MHz out) | **90** | **The critical part.** A disciplined 10 MHz reference. |
| 4 | **GPS antenna** (active, with bias tee) | 15 | Roof-mounted, clear sky view |
| 5 | **Raspberry Pi 4 / Zero 2 W** | 35 | Timestamps frames against the GPSDO |
| 6 | **Timestamping approach** | — | See §4 |
| 7 | Enclosure, PoE, cabling | 40 | |
| | **Per station** | **~$220** | |

**4 stations: ~$880.** Add a 5th and 6th for redundancy and better GDOP.

### Cheap GPSDO options

| Option | ~USD | Quality |
|---|---|---|
| u-blox NEO-M8T (timing variant) | 60 | **Best value.** Has a 1 PPS disciplined output, ~10 ns. |
| u-blox LEA-M8F | 110 | Better holdover |
| Used telecom GPSDO (Trimble, Symmetricom) | 80–200 | Excellent if you find one |
| GPS module + OCXO + PLL | 60 | DIY, works, more effort |

The **NEO-M8T** is the one to buy. Note the **T** — the standard NEO-M8N does not have
the timing features.

---

## 4. Timestamping — the hard engineering

This is the part that separates a working MLAT network from a pile of parts. Three
approaches, in order of difficulty:

### A. Hardware timestamp in the SDR (best)

Some SDRs can timestamp samples against an external 10 MHz + 1 PPS reference. The
timestamp accuracy is then a sample period (~0.4 µs at 2.4 MSPS) — good enough.

**Simplest practical route:** use a receiver that accepts an external clock and count
samples from the last 1 PPS edge. Sample count ÷ sample rate = time since the second.

### B. GPIO edge timestamping (good, DIY)

The SDR's demodulated output drives a GPIO. The Pi timestamps the edge against its
PPS-disciplined clock:

```
   GPSDO ──1 PPS──▶ Pi GPIO  (interrupt: record t_pps)
   SDR  ──frame───▶ Pi GPIO  (interrupt: record t_frame)
   timestamp = t_frame - t_pps  (within the second)
```

With the Pi's clock disciplined to the GPSDO, you get ~100 ns — usable. With an
RP2040 counting a 10 MHz reference directly you get ~10 ns — good.

### C. Software timestamping (poor)

Reading a USB SDR and calling `time.time()` gives ~1–10 ms of jitter from USB
buffering. **This is 300–3,000 km of error.** Do not expect it to work.

**Recommendation:** start with B. A Pi plus the GPSDO PPS on a GPIO, timestamping the
frame edges. It is achievable with off-the-shelf parts and gets you to ~100 ns, which
gives tens of metres of accuracy inside the network.

---

## 5. Accuracy — the honest numbers

MLAT accuracy is governed by **GDOP** (geometric dilution of precision), which grows
as the target moves outside the receiver network. Measured by the solver, 4 stations,
80 km baseline, 10 ns clocks:

| Target distance | Position error | GDOP | Verdict |
|---|---|---|---|
| overhead | **41 m** | 1.3 | excellent |
| 25 km | **9 m** | 1.1 | excellent |
| 50 km | 62 m | 2.4 | excellent |
| 80 km | 229 m | 7.6 | good |
| 120 km | 615 m | 21 | usable |
| 160 km | 1,162 m | 42 | poor |
| 210 km | 2,001 m | 80 | poor |
| 320 km | 3,797 m | 206 | poor |

**The solver fits its data to tens of metres everywhere** — the residual column in the
test output stays under 50 m. The position error grows purely because geometry
amplifies the same measurement error as range increases.

**This is the key insight and the reason the design is two systems, not one:**

```
   ADS-B  : gives RANGE     (300+ km, ~5 m from the aircraft's own GPS)
   MLAT   : gives PRECISION (tens of metres, but only inside the network)
```

Use each for what it is good at. A network covering a city gives excellent MLAT inside
that city and relies on ADS-B beyond it.

### Improving accuracy

1. **More receivers.** Every station improves GDOP and adds redundancy.
2. **Wider spacing.** A 370 km baseline gives GDOP 0.86 vs 1.10 at 93 km.
3. **Better clocks.** 10 ns → 1 ns is a 3× accuracy gain, but costs 10×.
4. **Use known altitude.** Mode-S altitude replies reduce the problem from 3-D to 2-D,
   which needs only **3 receivers** instead of 4 and cuts GDOP substantially.

---

## 6. Build order

**Step 1 — one station, receiving (as system A).**
Confirm `rtl_adsb` gives you frames. Do not proceed until it does.

**Step 2 — discipline the clock.**
Wire the GPSDO's 1 PPS to a GPIO. Write a small script that records `t_pps` on each
edge. Verify over an hour that the PPS interval is 1.000000 s ± 100 ns.

> If your PPS drifts more than 1 µs, fix the GPSDO before building more stations.
> Everything downstream depends on this.

**Step 3 — timestamp frames.**
Drive a GPIO from the SDR's demodulator output. Record frame edges. Log
`(hex_frame, timestamp_ns)`.

**Step 4 — validate with the solver.**
Feed a *known* aircraft (one also broadcasting ADS-B position) into `mlat.py` and
compare the MLAT fix against its broadcast position. **They should agree to within
your predicted GDOP.** This is the acceptance test.

```bash
cd adsb && python mlat.py --demo     # solver validation, no hardware
python mlat.py --clock               # clock requirements
python mlat.py --network             # network planning
```

**Step 5 — add stations.**
Two stations give no fix. **Three** give a 2-D fix *if* you know the altitude (Mode-S
provides it). **Four** give a 3-D fix without altitude. Add a 5th for redundancy.

**Step 6 — combine with ADS-B.**
For each target: use the ADS-B position when available, use MLAT for Mode-S-only
aircraft and to cross-check. Flag any ADS-B position that disagrees with MLAT by more
than the GDOP prediction — that is a spoofed or faulty transponder.

---

## 7. Verification

The solver is validated against **known ground truth** — positions are synthesised,
TDOA computed forward, then recovered:

```
case                                 true position                 solved      err
overhead the network           33.5731,  -7.5898    33.5732,  -7.5902      41m
~40 km NE, cruise              33.9000,  -7.5000    33.9013,  -7.5004     146m
~90 km NE, cruise              34.2000,  -7.0000    34.2034,  -6.9978     432m
~320 km NE, near horizon       35.5000,  -6.0000    35.5372,  -5.9723    4841m
overhead, low altitude         33.5731,  -7.5898    33.5732,  -7.5903      45m
```

Plus a 3-D solve (altitude unknown) that recovers altitude to ±354 m at 90 km, and a
geometry sweep showing GDOP vs baseline.

**The important assertion is the residual, not the distance.** The solver must fit its
own data well regardless of geometry; position error is then whatever GDOP dictates.
Asserting "always under 500 m" would be asserting something MLAT cannot do.

---

## 8. What this will and will not do

| Can | Cannot |
|---|---|
| Locate Mode-S-only aircraft | Locate aircraft no receiver hears |
| Cross-check spoofed ADS-B | Beat ADS-B for range (MLAT is local) |
| Work passively, legally | Work without GPS-disciplined clocks |
| Improve with every station | Cover 400 km accurately with 4 stations |

**Known failure modes:**

- **Clock drift** — if a GPSDO loses lock, that station poisons every fix. Monitor PPS
  health and drop a bad station automatically.
- **Multipath** — a frame arriving twice at one receiver gives a false timestamp.
  Sanity-check residuals per station.
- **Low-altitude targets** — a poor vertical geometry means altitude is weakly
  determined. Use the Mode-S altitude reply rather than solving for it.
- **No altitude reply and only 3 stations** — you get a 2-D fix that is wrong wherever
  the terrain is not at your assumed altitude.

---

## 9. Legal

Same as system A, and the same conclusion: **receiving is legal everywhere.**

| Action | Status |
|---|---|
| Receiving 1090 MHz | **Legal** |
| Running a multi-receiver network | **Legal** |
| Publishing MLAT results | Legal (this is what adsbexchange does) |
| **Transmitting anything** | **Illegal** without a licence |
| **Jamming** | **Serious criminal offence** |

**Privacy:** an MLAT network's receiver positions can be reverse-engineered from
published data. Round coordinates in any public feed.

---

## 10. Sources

- **Mode-S multilateration** — standard technique in air traffic surveillance;
  see EUROCONTROL documentation on MLAT for ATC.
- **adsbexchange / PlanePlotter** — real-world hobbyist MLAT networks, and the
  practical reference for the timestamping problem.
- **u-blox NEO-M8T** timing receiver datasheet — PPS accuracy specification.
- The solver in `adsb/mlat.py` implements Levenberg-Marquardt on the TDOA equations
  with WGS-84 ENU conversion, independently.
