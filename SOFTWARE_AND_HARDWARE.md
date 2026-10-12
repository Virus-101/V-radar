# Software Surface & Hardware Linkage

How the four software modules bind to the physical detector head. Every pin, gate and
hand-off below is read from the actual source, not designed on paper.

---

## 1. The software surface

### 1.1 Module map

```
                          ┌─────────────────────────────┐
                          │      MosquitoZapperSystem    │  full_system.py
                          │  .run() .pause() .shutdown() │  ← owns the main loop
                          └──────────────┬──────────────┘
                                         │ orchestrates
        ┌────────────────┬───────────────┼───────────────┬────────────────┐
        ▼                ▼               ▼               ▼                ▼
 ┌─────────────┐ ┌─────────────┐ ┌──────────────┐ ┌──────────────┐ ┌───────────┐
 │Mosquito     │ │Kalman       │ │PanTilt       │ │Laser         │ │Web        │
 │Detector     │ │Tracker      │ │Controller    │ │Controller    │ │Dashboard  │
 │             │ │             │ │              │ │              │ │           │
 │.detect()    │ │.update()    │ │.aim_at()     │ │.try_fire()   │ │.start()   │
 │.reset()     │ │.predict_    │ │.center()     │ │.stop()       │ │.update_   │
 │.frame_count │ │  ahead()    │ │.get_status() │ │.emergency_   │ │  frame()  │
 │             │ │.get_        │ │              │ │  stop()      │ │.update_   │
 │             │ │  confirmed()│ │              │ │.safety()     │ │  stats()  │
 └──────┬──────┘ └──────┬──────┘ └──────┬───────┘ └──────┬───────┘ └─────┬─────┘
        │               │               │                │               │
        │ Detection     │ Track         │ pan/tilt       │ enable +      │ MJPEG
        │ (x,y,w,h,conf)│ (id,confirmed)│ (pulse_us)     │ power %       │ + REST
        ▼               ▼               ▼                ▼               ▼
 ═══════════════════════════════════════════════════════════════════════════════
                         H A R D W A R E   B O U N D A R Y
 ═══════════════════════════════════════════════════════════════════════════════
   camera /          (in-memory)      GPIO 18, 19      GPIO 26       TCP :5000
   detector head                      → PCA9685        → MOSFET      → browser
```

### 1.2 The main loop (full_system.py, `_process_frame`)

The order matters — it is a pipeline, and each stage gates the next:

```
 1. DETECT      self._detector.detect(frame)
                └─ run at REDUCED res (320×240), then scale boxes back to 640×480

 2. TRACK       self._tracker.update(detections)
                └─ Kalman filter + nearest-neighbour association

 3. AIM         self._target_track = self._select_target()
                pred_x, pred_y = self._tracker.predict_ahead(track)
                self._servo.aim_at(pred_x, pred_y)          ← PREDICTED, not current

 4. FIRE        if self._target_track: self._try_fire_laser()
                └─ LaserController.try_fire(...) → SafetyInterlocks.check_all(...)

 5. TIMEOUT     if safety.check_continuous_timeout(): laser.stop()

 6. PUBLISH     self._web.update_frame(overlay) / .update_stats(...)
```

**Target selection priority:** confirmed tracks only, then closest to frame centre.
Unconfirmed tracks are aimed at but **never fired at** — that is the L3 interlock.

---

## 2. The interlock chain — the real gate order

`SafetyInterlocks.check_all()` returns `(bool, reason)`. It short-circuits in this
exact order; the first failure wins:

| # | Gate | Config key | Reject reason |
|---|---|---|---|
| 1 | Emergency stop | *(runtime)* | `EMERGENCY_STOP` |
| 2 | Manual arming | `manual_arm` | `NOT_ARMED` |
| 3 | Active mode | `require_active_mode` | `NOT_ACTIVE` |
| 4 | Confirmed track | `require_confirmed_track` | `NO_CONFIRMED_TRACK` |
| **5** | **Species / wingbeat (L3)** | `require_wingbeat` | `NO_WINGBEAT_CONFIRM` / `WINGBEAT_LOW_CONFIDENCE` |
| 6 | Inside firing zone | `firing_zone_radius: 80` | `OUTSIDE_FIRING_ZONE` |
| 7 | Per-target cooldown | `cooldown_per_target_ms: 500` | `TARGET_COOLDOWN (Nms)` |
| 8 | Session limit | `max_fires_per_session: 100` | `SESSION_LIMIT_REACHED` |
| 9 | Time window | `firing_time_window: None` | `OUTSIDE_TIME_WINDOW` |
| 10 | Min between shots | `min_time_between_shots_ms: 100` | `SHOT_COOLDOWN (Nms)` |

Plus two that run *outside* `check_all()`:

| Gate | Config key | Action |
|---|---|---|
| Max continuous fire | `max_continuous_fire_ms: 200` | `laser.stop()` |
| Emergency stop | REST `/api/emergency_stop` | latches until reset |

**Verified behaviour:** with default config and no track, `check_all()` returns
`(False, "NO_CONFIRMED_TRACK")`. With a confirmed track but no photodiode evidence it
returns `(False, "NO_WINGBEAT_CONFIRM (...)")`. **42/42 system tests pass.**

### Gate 5 — the species gate (now wired)

`require_wingbeat` **defaults to `true`**. A gate that defaults to off is not a safety
gate. The laser now refuses to fire unless the photodiode channel has positively
identified the target as a biting female.

Verified end-to-end with real synthesised wingbeat bursts:

| Evidence | Result |
|---|---|
| No photodiode evidence | `(False, "NO_WINGBEAT_CONFIRM (no wingbeat evidence yet)")` |
| Ambiguous 600 Hz (Aedes ♀ *or* Anopheles ♂) | `(False, "NO_WINGBEAT_CONFIRM (4 candidates spanning both sexes...)")` |
| Housefly 200 Hz | `(False, "NO_WINGBEAT_CONFIRM (non-target band (180-260 Hz))")` |
| Confirmed ♀ *Aedes albopictus*, 520 Hz, conf 1.00 | `(True, "OK")` |
| Evidence aged past 0.5 s | `(False, ...)` — stale reading cannot authorise a shot |

**Evidence expires.** `wingbeat_evidence_max_age_s: 0.5` — a reading from an insect
that has already left the curtain cannot authorise a later shot. This matters because
the insect that produced the reading may have gone and a hand may have entered.

**Bench override:** `require_wingbeat: false` lets the pipeline run with no photodiode
fitted. That is the *only* reason to disable it, and it should never be disabled on a
system with a live beam.

---

## 3. Hardware linkage — pins and buses

### 3.1 GPIO assignment

| Function | Config key | Default | Bus / target |
|---|---|---|---|
| Pan servo | `pan_tilt.pan_pin` | **GPIO 18** | → PCA9685 ch0 (or direct PWM) |
| Tilt servo | `pan_tilt.tilt_pin` | **GPIO 19** | → PCA9685 ch1 |
| Laser enable | `laser.laser_pin` | **GPIO 26** | → MOSFET gate (IRLZ44N) |
| Laser PWM | `laser.use_pwm` / `pwm_frequency` | True / 1000 Hz | power modulation |
| Web dashboard | — | TCP **:5000** | Flask MJPEG + REST |

### 3.2 Servo signal chain

```
PanTiltController.aim_at(pixel_x, pixel_y)
        │
        ├─ pixel → angle:  (px - x_center) / pixels_per_degree_x   [10.0 px/°]
        ├─ dead zone:      ±15 px  → no movement (prevents jitter)
        ├─ EMA smoothing:  alpha = 0.3
        ├─ rate limit:     max_step_per_frame = 5°
        └─ angle → pulse:  500–2500 µs  (over -90°…+90° pan, -45°…+45° tilt)
                │
                ▼
        PWMBackend.set_servo_pulse(pin, pulse_us)
                │
        ┌───────┴────────┬──────────────┬───────────────┐
        ▼                ▼              ▼               ▼
   MockPWMBackend   RPiGPIOBackend  PigpioBackend   (PCA9685)
   records pulse    software PWM    hardware PWM    I²C → 16 ch
   (tests)          (jittery)       (best)          (recommended)
```

**Backend is chosen by `pan_tilt.pwm_backend`** (`pigpio` default). `mock_servos: true`
swaps in `MockPWMBackend`, which just records `{pin: pulse_us}` so the whole pipeline
runs on a laptop with no hardware.

> **Hardware caveat.** `pixels_per_degree: 10.0` and `x_center: 320` are *assumed*
> values, not calibrated ones. They depend on camera FOV, mount geometry and servo
> horn offset. Until you calibrate them against a real target, aiming will be
> systematically off. This is the first thing to tune on real hardware.

### 3.3 Laser signal chain

```
LaserController.try_fire(track, x, y, cx, cy, power)
        │
        ├─ SafetyInterlocks.check_all(...)  → 9 gates, see §2
        │       └─ (False, reason) → return immediately, laser stays off
        │
        ├─ backend.set_laser_power(power_percent)
        ├─ backend.set_laser(True)
        ├─ safety.record_fire(track_id)
        └─ → LaserBackend
                │
        ┌───────┴────────┬──────────────────────┐
        ▼                ▼                      ▼
   MockLaserBackend  RPiGPIOLaserBackend   (relay/MOSFET)
   records state     GPIO 26, BCM mode      actual beam
   (tests)           PWM 1 kHz for power
```

`RPiGPIOLaserBackend` drives the pin **LOW at construction** — it fails safe on import.

### 3.4 Where the laser actually attaches

The controller only ever toggles **one GPIO line**. The physical wiring that line
implies, per `hardware/specs.md` §assembly:

```
  GPIO 26 ──▶ 220 Ω ──▶ IRLZ44N gate
                          │
  laser V+ ────┬──────────┤ drain
               │          │
            laser LD   source ──▶ GND
               │
            flyback diode (reverse-biased across LD)
               │
              GND
```

A MOSFET is used, not a bare GPIO, because the laser needs more current than a pin can
source. The flyback diode is not optional — an inductive laser driver will destroy the
GPIO without it.

> **This is the section that changes if you build a real kill stage.** A 100 mW diode
> needs ~300 mA. The 3 W source actually required for lethality needs **several amps**
> and real thermal management — a different driver, a heatsink, and probably TEC
> cooling. The GPIO/MOSFET *architecture* is right; the component ratings are not.
> See `laser_power_and_safety.md`.

---

## 4. Detection: two separate implementations

There are **two** detectors in this repo and they are not shared code. Worth knowing
so you do not fix one and expect the other to change:

| | `detection/detect.py` | `system/detector.py` |
|---|---|---|
| Purpose | standalone pipeline, training-data collection | embedded in the control loop |
| Backends | YOLOv8-nano **or** MOG2 fallback | MOG2 / KNN / frame-diff |
| Morphology | **dilate 3×3 + close** (fixed — see README) | open 3×3 → close 5×5 → **dilate ×2** |
| min_area | 20 | 5 |
| Output | JSONL + video | in-memory `Detection` objects |
| Measured recall | 98% frames, median 4/4 blobs | 99% frames, median 3/4 blobs |

The system version avoids the opening bug because it **dilates twice after opening**,
which restores the eroded sliver. I tested both rather than assuming the fix
transferred — it did not need to.

> **Neither uses the photodiode wingbeat channel.** `detection/wingbeat.py` is
> implemented and self-tests 12/12, but it is **not yet wired into the interlock
> chain**. The system currently gates on track confirmation and geometry only —
> it has no species check. That is the most important gap in the whole build.

---

## 5. The species gate — now closed

```
  IMPLEMENTED + CONNECTED
  ─────────────────────────────────────────────────────────────
  detection/wingbeat.py            system/laser_controller.py
    analyse_burst(trace)             check_all() gate 5
    → frequency, species, sex          require_wingbeat: true
    → should_fire                      → NO_WINGBEAT_CONFIRM
    self-test 12/12 ✓                  evidence expires after 0.5 s
                                      42/42 system tests ✓
```

Data flow:

```
  photodiode burst (numpy array)
        │
        ▼  MosquitoZapperSystem._update_wingbeat_evidence()   each frame
  wingbeat.analyse_burst(burst)
        │  → should_fire, confidence, frequency_hz, reason
        ▼
  SafetyInterlocks.set_wingbeat_evidence(...)
        │
        ├─ no new burst this frame → _age_wingbeat_evidence(dt)
        │                             closes the gate after 0.5 s
        ▼
  check_all() gate 5 → NO_WINGBEAT_CONFIRM / WINGBEAT_LOW_CONFIDENCE / OK
```

The system now fires only at what the photodiode channel has positively identified as
a **biting female**. An ambiguous reading (600 Hz is both *Aedes* female and
*Anopheles* male) does **not** open the gate — the classifier's candidate set must
contain nothing but biting females.

**To drive it on real hardware:** the RP2040 samples the photodiode and hands the main
loop a burst array via `self._wingbeat_burst`. Nothing else needs to change.

---

## 6. REST API surface

| Method | Route | Effect |
|---|---|---|
| GET | `/` | dashboard HTML |
| GET | `/video_feed` | MJPEG stream (overlay-rendered) |
| GET | `/api/status` | JSON: fps, detections, tracks, confirmed, fires, interlock state |
| POST | `/api/arm` | `safety.arm()` — gate 2 |
| POST | `/api/disarm` | `safety.disarm()` |
| POST | `/api/emergency_stop` | latches estop — gate 1 |
| POST | `/api/reset_estop` | clears the latch |

Arm/disarm/estop are deliberately **POST-only** so they cannot be triggered by a
browser prefetch or a link.
