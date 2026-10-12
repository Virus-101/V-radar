"""
Generate a preview of the mosquito zapper pipeline.

Runs the REAL system (detector -> tracker -> servo -> laser interlock -> overlay)
over synthesised footage and writes an annotated MP4. This is not a mock-up: every
box, track ID, aim line and interlock verdict in the output comes from the actual
production code paths.

Usage:
    python preview.py                      # default: 15 s, 4 mosquitoes
    python preview.py --seconds 30 --insects 8
    python preview.py --out my_preview.mp4

The synthesised footage deliberately includes one non-mosquito-sized object so you
can see the size filter reject it, and a stationary "finger" so you can see the
confirmed-track requirement hold the beam off.
"""

from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

import cv2
import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from config import Config                      # noqa: E402
from detector import MosquitoDetector          # noqa: E402
from tracker import KalmanTracker              # noqa: E402
from servo_controller import PanTiltController  # noqa: E402
from laser_controller import LaserController   # noqa: E402
from full_system import OverlayRenderer        # noqa: E402


class Insect:
    """A small erratic flyer, in the spirit of a mosquito."""

    def __init__(self, w, h, rng, speed=2.2):
        self.w, self.h = w, h
        self.x = rng.uniform(w * 0.15, w * 0.85)
        self.y = rng.uniform(h * 0.15, h * 0.70)
        self.a = rng.uniform(0, 2 * math.pi)
        self.speed = speed * rng.uniform(0.7, 1.3)
        self.dart_timer = rng.uniform(0.2, 0.8)
        self.rng = rng
        self.phase = rng.uniform(0, 6.28)

    def step(self, dt):
        self.dart_timer -= dt
        if self.dart_timer <= 0:
            self.dart_timer = self.rng.uniform(0.15, 0.6)
            self.a += self.rng.uniform(-1.9, 1.9)      # sudden direction change
        self.a += self.rng.uniform(-0.25, 0.25)
        self.x += math.cos(self.a) * self.speed
        self.y += math.sin(self.a) * self.speed
        m = 18
        if self.x < m or self.x > self.w - m:
            self.a = math.pi - self.a
            self.x = min(max(self.x, m), self.w - m)
        if self.y < m or self.y > self.h - m:
            self.a = -self.a
            self.y = min(max(self.y, m), self.h - m)
        self.phase += dt * 40


def make_frame(insects, w, h, t, rng, stationary=None, big=None):
    """Render one synthetic sensor frame."""
    frame = np.full((h, w, 3), 10, np.uint8)

    # faint IR-lit backdrop so the scene is not pure black
    frame[:, :] = 14
    for gx in range(0, w, 40):
        cv2.line(frame, (gx, 0), (gx, h), (20, 20, 22), 1)
    for gy in range(0, h, 40):
        cv2.line(frame, (0, gy), (w, gy), (20, 20, 22), 1)

    def blob(cx, cy, r, tone=235):
        # a body plus a wing smear, so it is not a perfect circle
        cv2.circle(frame, (int(cx), int(cy)), r, (tone, tone, tone), -1)
        ox = int(math.cos(t * 30) * (r + 1))
        cv2.line(frame, (int(cx), int(cy)),
                 (int(cx) + ox, int(cy) - r), (tone // 2, tone // 2, tone // 2), 1)

    for ins in insects:
        blob(ins.x, ins.y, 3)

    # A large object: should be REJECTED by the max-area size filter.
    if big is not None:
        cv2.circle(frame, (int(big[0]), int(big[1])), 22, (200, 200, 200), -1)

    # A stationary object: tracked but never confirmed / never fired at.
    if stationary is not None:
        cv2.circle(frame, (int(stationary[0]), int(stationary[1])), 4,
                   (215, 215, 215), -1)

    frame += rng.integers(0, 5, (h, w, 3), dtype=np.uint8)
    return frame


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--seconds", type=float, default=15.0)
    ap.add_argument("--insects", type=int, default=4)
    ap.add_argument("--fps", type=int, default=30)
    ap.add_argument("--width", type=int, default=640)
    ap.add_argument("--height", type=int, default=480)
    ap.add_argument("--out", default=str(HERE.parent / "preview" / "system_preview.mp4"))
    ap.add_argument("--seed", type=int, default=11)
    ap.add_argument("--no-wingbeat-gate", action="store_true",
                    help="Disable the L3 species gate so the aim/fire path is "
                         "visible. ONLY for previewing - never on a live beam.")
    args = ap.parse_args()

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    rng = np.random.default_rng(args.seed)
    w, h = args.width, args.height

    # --- real components, mock backends ------------------------------------
    cfg = Config(str(HERE / "config.yaml"))
    cfg._data.setdefault("laser", {})["mock_laser"] = True
    cfg._data.setdefault("pan_tilt", {})["mock_servos"] = True
    cfg._data["pan_tilt"]["pwm_backend"] = "mock"
    # Size filter: min 5 / max 200 px area (from config). The 22 px-radius
    # object is ~1520 px and must be rejected.
    cfg._data.setdefault("detection", {})["min_area"] = 5
    cfg._data["detection"]["max_area"] = 200

    detector = MosquitoDetector(cfg)
    tracker = KalmanTracker(cfg)
    servo = PanTiltController(cfg)
    laser = LaserController(cfg)
    overlay = OverlayRenderer(cfg)

    if args.no_wingbeat_gate:
        laser.safety._require_wingbeat = False
        # Preview insects roam the whole frame; the production radius (80 px) is
        # tuned for a servo whose reach is limited to near-centre. Widen it so
        # the aim/fire path is visible. Not a production setting.
        laser.safety._firing_zone_radius = max(w, h) * 0.75
        laser.safety._cooldown_per_target_ms = 120
        laser.safety._min_between_shots_ms = 60
    laser.safety.arm()
    laser.safety.set_active(True)

    insects = [Insect(w, h, rng) for _ in range(args.insects)]
    stationary = (w * 0.78, h * 0.80)     # a "finger": tracked, never confirmed
    big = (w * 0.20, h * 0.30)            # a "bird": rejected by size

    n_frames = int(args.seconds * args.fps)
    writer = cv2.VideoWriter(str(out_path),
                             cv2.VideoWriter_fourcc(*"mp4v"),
                             args.fps, (w, h))

    log = []
    dt = 1.0 / args.fps
    fires = 0
    rejects = {"NO_CONFIRMED_TRACK": 0, "NO_WINGBEAT_CONFIRM": 0,
               "OUTSIDE_FIRING_ZONE": 0, "TARGET_COOLDOWN": 0,
               "SHOT_COOLDOWN": 0}

    for i in range(n_frames):
        t = i * dt
        for ins in insects:
            ins.step(dt)

        # occasionally respawn one at the edge, like a real swarm
        if i % 90 == 0 and i > 0:
            insects[i % len(insects)] = Insect(w, h, rng)

        frame = make_frame(insects, w, h, t, rng, stationary, big)

        # --- REAL PIPELINE -------------------------------------------------
        detections = detector.detect(frame)

        # scale from detection res back to full frame
        for d in detections:
            d.x = int(d.x * w / cfg.get("detection.downscale_width", w))
            d.y = int(d.y * h / cfg.get("detection.downscale_height", h))

        tracks = tracker.update(detections)
        confirmed = [tr for tr in tracks if tr.confirmed]

        # target = confirmed track nearest frame centre
        target = None
        if confirmed:
            cx, cy = w / 2, h / 2
            target = min(confirmed,
                         key=lambda tr: (tr.last_x - cx) ** 2 + (tr.last_y - cy) ** 2)

        aim_x = aim_y = None
        if target is not None:
            aim_x, aim_y = tracker.predict_ahead(target)
            servo.aim_at(aim_x, aim_y)

        # Species gate: no photodiode fitted, so no evidence -> gate closed.
        # This is the correct fail-safe state and the preview shows it.
        fired, reason = False, "no target"
        if target is not None:
            fired, reason = laser.try_fire(target, aim_x, aim_y, w // 2, h // 2)
            if fired:
                fires += 1
            else:
                for key in rejects:
                    if reason.startswith(key):
                        rejects[key] += 1
                        break

        # --- overlay (real renderer) ---------------------------------------
        servo_status = servo.get_status()
        laser_status = laser.safety.get_status()
        vis = overlay.render(frame.copy(), detections, tracks,
                             servo_status, laser_status, args.fps)

        if fired:
            cv2.putText(vis, "FIRED", (w // 2 - 40, h - 20),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 255), 2)
        else:
            cv2.putText(vis, f"HOLD: {reason.split(' ')[0]}", (12, h - 14),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 200, 255), 1)

        cv2.putText(vis, f"frame {i+1}/{n_frames}", (w - 150, h - 14),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.45, (160, 160, 160), 1)

        writer.write(vis)
        if i % 30 == 0:
            log.append((i, len(detections), len(tracks), len(confirmed),
                        target.track_id if target else None, reason, fired))

    writer.release()
    laser.cleanup()
    servo.cleanup()

    # --- report ------------------------------------------------------------
    print(f"wrote {out_path}  ({n_frames} frames, {args.seconds:.0f}s @ {args.fps}fps)")
    print(f"\n{'frame':>6} {'dets':>5} {'tracks':>7} {'conf':>5} {'target':>7}  reason")
    print("-" * 74)
    for row in log[:14]:
        i, nd, nt, nc, tid, reason, fired = row
        print(f"{i:>6} {nd:>5} {nt:>7} {nc:>5} {str(tid):>7}  {reason}")
    print("-" * 74)
    print(f"total fires: {fires}")
    print("interlock rejections (beam held off):")
    for k, v in rejects.items():
        if v:
            print(f"   {k:28s} {v}")
    print("\nNote: with no photodiode channel fitted the species gate (gate 5) "
          "correctly\n      holds the beam off. Set "
          "laser.safety_interlocks.require_wingbeat=false\n      to preview the "
          "aim/fire path, or fit the sensor head to open it properly.")


if __name__ == "__main__":
    main()
