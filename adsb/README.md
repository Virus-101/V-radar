# ADS-B Airspace Monitor — Build Guide

A **long-range air surveillance system that is legal to build anywhere**, because it
only listens. No transmitter, no licence, no spectrum allocation.

| | |
|---|---|
| **Detection range** | **300–400 km** (altitude-limited, not power-limited) |
| **Cost** | **$30–80** |
| **Legal** | Passive receive at 1090 MHz — unlicensed worldwide |
| **Coverage** | Every aircraft broadcasting position, altitude, speed, identity |
| **Update rate** | 2 messages/second per aircraft (ADS-B requirement) |

---

## 1. Why this beats building a radar

You asked for "radar with max range, from any source." Here is the comparison, and
it is not close:

| | Build a radar | ADS-B receiver |
|---|---|---|
| Cost | $5,000 (used marine) – $100,000+ | **$30–80** |
| Licence | **Required** (ANRT in Morocco). Criminal offence without. | **None** |
| Range vs airliner at 11 km | ~46 km (1 kW X-band, 1 m dish) | **~360 km** |
| Range vs mosquito | ~145 km — but useless (see below) | n/a |
| Altitude data | Inferred | **Broadcast, exact** |
| Identity | Inferred | **Broadcast, exact** |
| Build effort | Months | An afternoon |
| Legality | Transmitting unlicensed radar is illegal | Legal everywhere |

**The range difference is the whole point.** Radar is limited by *power*: signal falls
as `1/R⁴`, so doubling range needs **16×** the power. ADS-B is limited by the *radio
horizon*, which is geometry: `4.12 × √(height in m)` km. An airliner at 11 km altitude
is above the horizon at ~430 km no matter how little power it transmits. You are
limited by the curve of the Earth, not by your budget.

### Why radar cannot do the mosquito job either

Worth stating because it was the original idea. Radar angular resolution is
`θ ≈ λ/D`. X-band (3 cm) with a 1 m dish gives `θ = 0.03 rad` — at 100 m that is a
**3 metre** position uncertainty. A mosquito is **3 mm**. You are off by **1000×**,
and that is the diffraction limit, not an engineering gap: resolving 3 mm at 100 m
would need a ~100 m antenna.

And the binding constraint is the *laser*, not the sensor: the lethal range is ~3 m
(30 m for Intellectual Ventures' 30 W system). Even a perfect detector at 145 m buys
nothing, because you cannot reach out and touch it.

**That is why the light curtain exists**: it works at 3 m because that is where the
beam works, and it costs $330.

---

## 2. Parts

### Minimum viable (works today)

| # | Part | ~USD | Notes |
|---|---|---|---|
| 1 | **RTL-SDR Blog V3 dongle** | 30 | RTL2832U + R820T2. Get the V3 — it has a proper 1090 MHz input and a bias tee. The generic $8 dongles work but drift badly at 1090 MHz. |
| 2 | **1090 MHz antenna** | 10 | Even the stock whip works. A dedicated 1090 MHz colinear is much better (see §3). |
| 3 | **Coax: SMA male–male, 1 m** | 5 | Low loss matters at 1 GHz. RG-58 is fine for short runs; LMR-200 better. |
| 4 | **USB extension or powered hub** | 5 | Get the dongle near the antenna, not near the PC. |
| | **Total** | **~$50** | |

### Better antenna (biggest single improvement)

The stock whip is a compromise. A **1090 MHz colinear** (8–12 elements) adds
10–20 km of range and costs almost nothing to build:

```
        ┌─ top: 1/4 wave stub, open
        │
   ┌────┴────┐
   │  λ/2    │   λ = 300/1090 = 275 mm  ->  element = 137 mm
   ├─────────┤
   │  λ/2    │   repeat 8-12 times
   ├─────────┤
   │   ...   │   solder the shields together, feed the centre at the bottom
   └────┬────┘
        │
     feed point  ── 50 Ω coax to the dongle
```

Build it from **2.5 mm² copper wire** in a length of PVC conduit. Mount it as high as
you can — height beats everything else, because range is `4.12 × √h`.

### Optional upgrades

| Part | ~USD | Gain |
|---|---|---|
| LNA (low-noise amplifier, 1090 MHz) | 25 | +20–40 km. Put it at the *antenna*, not the receiver. |
| SAW filter (1090 MHz) | 12 | Rejects strong out-of-band signals (cell towers). Essential in cities. |
| Band-pass filter + LNA combo | 40 | Best single upgrade after height. |
| Better coax (LMR-400) | 30 | Only worth it for runs over 5 m. |

---

## 3. Build order

**Step 1 — receive something (30 minutes).**

```bash
# Linux / macOS
sudo apt install rtl-sdr          # or: brew install librtlsdr
rtl_test -s 2400000               # confirm the dongle is seen

# start the demodulator and dump frames
rtl_adsb > capture.txt
```

If you see lines of hex, **you are receiving aircraft**. That is the whole sensor.

**Step 2 — see it on a map.**

```bash
cd adsb
python monitor.py --file capture.txt --lat <your lat> --lon <your lon>
```

**Step 3 — go live.**

```bash
python monitor.py --rtl --lat 33.5731 --lon -7.5898
```

**Step 4 — improve range.** In order of payoff:

1. **Height.** Move the antenna up. Every doubling of height adds ~41% range.
2. **Antenna.** Build the colinear above.
3. **Filter.** If you are near cell towers, a 1090 MHz SAW filter stops the front end
   desensitising.
4. **LNA.** Amplifies signal *and* noise — only helps after 1–3 are done.

**Windows note.** RTL-SDR on Windows needs the Zadig driver swap. Then `rtl_adsb.exe`
runs the same way. If you would rather not, run the receiver on a Raspberry Pi and
point the monitor at the capture file over the network.

---

## 4. What you will actually see

At 11 km altitude the horizon is `4.12 × √11000 ≈ 432 km`. In practice you get
**250–400 km** depending on antenna and terrain. You will see:

- **Cruise traffic** at 10–12 km, 200–400 km out
- **Approach/departure** traffic, 0–5 km altitude, 20–80 km out
- **Ground traffic** if you are near an airport (TC 5–8 surface messages)

Each aircraft gives you: position (2 Hz), barometric altitude, GNSS altitude, ground
speed, track, vertical rate, callsign, squawk, and — from the ICAO 24-bit address —
the registration, which you can look up offline.

---

## 5. Accuracy and limits

**What ADS-B gives you, and how much to trust it:**

| Field | Resolution | Trust |
|---|---|---|
| Position | ~5 m (CPR, 17-bit) | **Broadcast by the aircraft — it can be wrong or spoofed** |
| Barometric altitude | 25 ft | Good |
| GNSS altitude | 25 ft | Good |
| Ground speed | 1 kt | Good |
| Track | ~1° | Good |
| Vertical rate | 64 fpm | Coarse |
| Callsign | exact | **Pilot-entered — typos are common** |

**Important caveats:**

- **ADS-B is unauthenticated.** An aircraft (or a spoofer) can broadcast a false
  position. This is a known aviation-security issue. Do not treat the map as
  authoritative — for air traffic control it is corroborated by primary radar.
- **Position needs two frames.** CPR encodes a zone index, not an angle, so an
  aircraft needs an even *and* an odd frame before its position resolves. New
  aircraft appear on the map a second or two after first reception.
- **You only see cooperative targets.** Aircraft without a working transponder, or
  military traffic that does not broadcast, are invisible. This is a *cooperative*
  surveillance system, not a radar.
- **Terrain blocks.** Hills between you and the horizon cut range. Height of your
  antenna is the fix.

---

## 6. Legal position

**Receiving is legal.** 1090 MHz ADS-B is an openly broadcast signal intended for
public reception. Listening to it is unlicensed in every jurisdiction I am aware of,
including Morocco. There is no transmitter in this project.

**What is not legal:**

| Action | Status |
|---|---|
| Receiving 1090 MHz | **Legal** |
| Decoding and displaying | **Legal** |
| Publishing a feed (e.g. FlightAware, adsbexchange) | Legal, and encouraged |
| **Transmitting on 1090 MHz** | **Illegal** without a licence — this includes "spoofing" tests |
| **Jamming ADS-B or GNSS** | **Serious criminal offence** in most countries |
| Using this to interfere with aircraft | Obviously illegal, and dangerous |

**One practical note:** if you publish a live feed with your exact receiver position,
you are publishing your home location. Round the coordinates in any public feed, or
use a feed service that strips them.

---

## 7. Files

| File | What it is |
|---|---|
| `decoder.py` | Mode-S/ADS-B decoder written from scratch — CRC-24, CPR, all the message types. No dependencies. |
| `receiver.py` | Sources (RTL-SDR / file / synthetic) and the live tracker |
| `monitor.py` | Tkinter polar map with range rings, trails, altitude colouring |
| `adsb/README.md` | This document |

### Verification

The decoder self-test uses **real captured frames**, not invented ones:

```bash
python decoder.py
```

```
CRC valid on 4/4 known-good frames
single-bit error: detected, repaired=True
constructed frame validates: 8DABCDEF8000000000000087C852
  DF17 4840D6  TC=4  callsign=KLM1023
  DF17 40621D  TC=11  alt=38000ft
  DF17 40621D  TC=11  alt=38000ft
  DF17 485020  TC=19  gs=367kt trk=44 vs=640fpm

CPR pair resolved: 40621D -> lat=52.2572021484375, lon=3.91937255859375
  matches the documented position (52.2572, 3.91937)
all checks passed
```

The CPR check is the important one: that pair is a documented test vector with a
known answer, and the decoder reproduces it to 5 decimal places.

---

## 8. Where this leads

If you want to go further, in rough order of payoff:

1. **Better antenna and height** — free range, up to the horizon limit.
2. **Feed to adsbexchange / FlightAware** — free premium accounts, and you contribute
   to the public airspace picture.
3. **MLAT** — with 3+ receivers you can *triangulate* aircraft that do not broadcast
   position (Mode-S only). This is the closest legal thing to building your own radar,
   and it is done entirely by receiving.
4. **Combine with the optical tracker** from the mosquito project — the galvo + Kalman
   pipeline is the same maths, and a camera on a mount can identify what ADS-B only
   locates.

What this will not become is an engagement system. Detection, tracking and display are
what it does, and that is deliberate.
