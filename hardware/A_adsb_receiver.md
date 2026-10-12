# System A — ADS-B Airspace Receiver

**Long-range air surveillance that is legal everywhere, because it only listens.**

| | |
|---|---|
| **Range** | **250–400 km** (altitude-limited) |
| **Cost** | **$50 minimum**, $150 well-equipped |
| **Licence** | **None.** Passive receive at 1090 MHz. |
| **Build time** | One afternoon |
| **Software** | `adsb/desktop.py` (done, tested) |

---

## 1. Why this design

Range is set by **geometry, not power**. ADS-B is a broadcast — the aircraft tells you
where it is. Your job is only to *hear* it, and hearing is limited by the radio
horizon:

```
   horizon_km ≈ 4.12 × ( √h_aircraft_m + √h_antenna_m )
```

| Aircraft altitude | Horizon (2 m antenna) |
|---|---|
| 1,000 ft (305 m) | ~78 km |
| 10,000 ft (3,048 m) | ~233 km |
| 35,000 ft (10,668 m) | **~432 km** |

A radar at the same location with 1 kW and a 1 m dish sees an airliner at ~46 km.
**The passive receiver beats it by 9×, for 1/100th the cost, with no licence.**

---

## 2. Parts

### Minimum viable — $50

| # | Part | ~USD | Notes |
|---|---|---|---|
| 1 | **RTL-SDR Blog V3** | 30 | RTL2832U + R820T2, TCXO, SMA in, bias tee. **Get the V3** — generic $8 dongles drift badly at 1090 MHz. |
| 2 | **1090 MHz antenna** | 10 | Stock whip works. See §3 for a better one. |
| 3 | **SMA male–male coax, 1 m** | 5 | RG-58 fine for short runs. |
| 4 | **USB extension cable** | 5 | Get the dongle *at* the antenna, not at the PC. Loss is in the coax. |
| | **Total** | **$50** | |

### Well-equipped — $150

| # | Part | ~USD | Gain |
|---|---|---|---|
| 5 | **1090 MHz SAW filter** | 12 | **Do this early.** Near cell towers, strong out-of-band signals desensitise the front end. |
| 6 | **1090 MHz LNA** (low-noise amp) | 25 | +20–40 km. Mount at the **antenna**, not the receiver. |
| 7 | **LMR-200 coax, 5 m** | 20 | Lower loss than RG-58 at 1 GHz. |
| 8 | **Colinear antenna** (DIY, §3) | 15 | +10–20 km over a whip. |
| 9 | **Raspberry Pi Zero 2 W** | 20 | Run the receiver headless, feed the desktop app over the network. |
| 10 | **Outdoor enclosure + mount** | 30 | IP65 box, mast clamps. |
| | **Total** | **~$150** | |

### Upgrade order (most payoff first)

1. **Height.** Every doubling of antenna height adds ~41% range. This is free and
   beats every electronic upgrade.
2. **Filter** (if urban). Prevents overload.
3. **Antenna** (colinear).
4. **LNA.** Amplifies signal *and* noise — only worth it after 1–3.

---

## 3. The antenna

### Option 1 — stock whip (start here)

Screw it on, get it near a window, confirm you are receiving. Done in 5 minutes.

### Option 2 — 1090 MHz colinear (build it)

The best value in the whole project. Element length:

```
   λ = 300 / 1090 MHz = 0.2752 m
   half-wave element  = λ/2 = 137.6 mm
```

```
                    │
              ┌─────┴─────┐   top: λ/4 stub, left OPEN
              │           │
              ├───────────┤
              │  137.6mm  │  ← λ/2 section
              ├───────────┤
              │  137.6mm  │  ← repeat 8-12 times
              ├───────────┤
              │    ...    │
              └─────┬─────┘
                    │
              feed point ──▶ 50 Ω coax to the dongle
```

**Build:** 2.5 mm² solid copper wire inside 20 mm PVC conduit. Solder the element
joints; the coax centre goes to the **bottom** element, the shield to a small
ground plane (or a 1/4-wave radials set, ~69 mm each).

**Mount:** as high as you safely can. On a roof is worth more than any amplifier.

### Option 3 — commercial

A dedicated 1090 MHz colinear from an aviation supplier is $40–80 and performs
similarly to a good DIY build. Buy it if you would rather not solder.

---

## 4. Build order

### Step 1 — hear something (30 min)

```bash
sudo apt install rtl-sdr          # Linux
brew install librtlsdr            # macOS

rtl_test -s 2400000               # is the dongle seen?
rtl_adsb | head -20               # hex frames = you are receiving aircraft
```

If hex scrolls past, **the sensor works.** Everything after this is range tuning.

### Step 2 — see it

```bash
cd adsb
rtl_adsb > capture.txt &          # record for replay
python desktop.py --rtl --lat <your-lat> --lon <your-lon>
```

Or replay a recording:

```bash
python desktop.py --file capture.txt --lat 33.5731 --lon -7.5898
```

### Step 3 — tune

- Move the antenna up. Re-check `Max range` in the RECEPTION panel.
- Add the SAW filter if `CRC fail` is high or tracks drop in and out.
- Add the LNA last.

**Success looks like:** 100–300 aircraft seen over an hour, `Max range` climbing
toward 400 km, `CRC fail` staying near zero.

### Step 4 — go headless (optional)

Put the dongle + Pi + antenna on the roof. Run `rtl_adsb | tee /var/log/adsb.txt`.
Point the desktop app at the file over the network, or add a small socket relay.

---

## 5. Verification

The decoder ships with real captured frames and a documented CPR test vector:

```bash
cd adsb && python decoder.py
```

```
CRC valid on 4/4 known-good frames
single-bit error: detected, repaired=True
constructed frame validates: 8DABCDEF8000000000000087C852
  DF17 4840D6  TC=4  callsign=KLM1023
  DF17 40621D  TC=11  alt=38000ft
  DF17 485020  TC=19  gs=367kt trk=44 vs=640fpm

CPR pair resolved: 40621D -> lat=52.2572021484375, lon=3.91937255859375
  matches the documented position (52.2572, 3.91937)
all checks passed
```

**Field check:** pick one aircraft on your map, note its callsign, and compare
against Flightradar24 or adsbexchange. Position, altitude and track should match.

---

## 6. What you will and will not see

**Will see:** all transponder-equipped aircraft within the radio horizon — airliners,
business jets, most GA, helicopters, and airport surface traffic within ~10 km.

**Will not see:**

| | Why |
|---|---|
| Military traffic | Often does not broadcast, or broadcasts a false ID |
| Aircraft with a failed transponder | Nothing to receive |
| Anything below your horizon | Terrain or Earth curvature blocks it |
| Ground vehicles | Not broadcasting |

**This is cooperative surveillance, not radar.** You see only aircraft that choose to
tell you where they are. That is both its strength (free, exact data) and its limit
(no independent verification).

**ADS-B is unauthenticated.** Positions can be spoofed. For safety-critical use it is
corroborated by primary radar; do not treat this map as authoritative for anything
that matters.

---

## 7. Legal

| Action | Status |
|---|---|
| Receive 1090 MHz | **Legal** everywhere |
| Decode and display | **Legal** |
| Publish a feed | Legal — adsbexchange / FlightAware will give you a free premium account |
| **Transmit on 1090 MHz** | **Illegal** without a licence |
| **Jam ADS-B or GNSS** | **Serious criminal offence** |

**Privacy note:** publishing a live feed with exact coordinates publishes your home
address. Round them, or use a service that strips them.

---

## 8. Where this leads

1. **Better antenna + height** — free range, up to the horizon limit.
2. **Feed a public network** — contribute to the airspace picture, get premium access.
3. **MLAT** — with 3+ receivers, triangulate aircraft that do not broadcast position
   (Mode-S only). This is the closest legal thing to building your own radar, and it
   is done entirely by receiving.
4. **Pair with the optical tracker** — the Kalman + galvo pipeline in `system/` is
   the same maths. A camera on a mount can *identify* what ADS-B only *locates*.

Detection, tracking and display. Not an engagement system, and deliberately so.
