"""
Mosquito Zapper — desktop control application.

A Tkinter GUI over the real system in this repo. It is not a re-implementation:
it drives MosquitoDetector, KalmanTracker, PanTiltController, LaserController and
OverlayRenderer directly, so what you see in the window is the production pipeline.

Design constraints that shaped this:
  * Only tkinter is available (stdlib). No Qt/GTK dependency.
  * The vision pipeline must not block the UI thread, so it runs in a worker
    thread and hands frames to the GUI through a queue.
  * The laser controls are deliberately awkward: ARM requires a deliberate click,
    and E-STOP is always live. A safety control that is easy to hit by accident
    is not a safety control.

Run:
    python desktop_app.py
    python desktop_app.py --video path/to/footage.mp4
    python desktop_app.py --mock          # no camera, synthesised swarm
"""

from __future__ import annotations

import argparse
import math
import queue
import sys
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import cv2
import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from config import Config                       # noqa: E402
from detector import MosquitoDetector           # noqa: E402
from tracker import KalmanTracker               # noqa: E402
from servo_controller import PanTiltController  # noqa: E402
from laser_controller import LaserController    # noqa: E402
from full_system import OverlayRenderer         # noqa: E402

# --- palette (dark, matching the other artefacts) --------------------------
BG        = "#0b0f12"
PANEL     = "#12181d"
PANEL_HI  = "#1a232b"
FG        = "#d8e4e8"
MUTED     = "#6b8189"
ACCENT    = "#00e5c0"
WARN      = "#ffb020"
DANGER    = "#ff3366"
OK        = "#3ddc84"


class SwarmSimulator:
    """Synthesises mosquito-like footage so the app is demonstrable with no
    camera and no sensor head attached."""

    def __init__(self, w, h, count=6, seed=3):
        self.w, self.h = w, h
        self.rng = np.random.default_rng(seed)
        self.ins = [self._spawn() for _ in range(count)]

    def _spawn(self):
        r = self.rng
        return {
            "x": r.uniform(self.w * .15, self.w * .85),
            "y": r.uniform(self.h * .15, self.h * .70),
            "a": r.uniform(0, 6.283),
            "sp": r.uniform(1.4, 3.0),
            "dt": r.uniform(.15, .6),
        }

    def step(self, dt):
        for b in self.ins:
            b["dt"] -= dt
            if b["dt"] <= 0:
                b["dt"] = self.rng.uniform(.15, .6)
                b["a"] += self.rng.uniform(-1.9, 1.9)
            b["a"] += self.rng.uniform(-.25, .25)
            b["x"] += math.cos(b["a"]) * b["sp"]
            b["y"] += math.sin(b["a"]) * b["sp"]
            m = 18
            if b["x"] < m or b["x"] > self.w - m:
                b["a"] = math.pi - b["a"]; b["x"] = min(max(b["x"], m), self.w - m)
            if b["y"] < m or b["y"] > self.h - m:
                b["a"] = -b["a"]; b["y"] = min(max(b["y"], m), self.h - m)

    def frame(self):
        f = np.full((self.h, self.w, 3), 14, np.uint8)
        for gx in range(0, self.w, 40):
            cv2.line(f, (gx, 0), (gx, self.h), (20, 20, 22), 1)
        for gy in range(0, self.h, 40):
            cv2.line(f, (0, gy), (self.w, gy), (20, 20, 22), 1)
        for b in self.ins:
            cv2.circle(f, (int(b["x"]), int(b["y"])), 3, (235, 235, 235), -1)
        f += self.rng.integers(0, 5, (self.h, self.w, 3), dtype=np.uint8)
        return f


class Pipeline(threading.Thread):
    """Runs the real detection -> tracking -> aim -> interlock loop off the UI
    thread, publishing annotated frames and telemetry through queues."""

    def __init__(self, cfg, source, out_q, tel_q):
        super().__init__(daemon=True)
        self.cfg = cfg
        self.source = source
        self.out_q = out_q
        self.tel_q = tel_q
        self.running = True
        self.paused = False

        self.detector = MosquitoDetector(cfg)
        self.tracker = KalmanTracker(cfg)
        self.servo = PanTiltController(cfg)
        self.laser = LaserController(cfg)
        self.overlay = OverlayRenderer(cfg)

        self.sim = None
        self.cap = None
        self.is_file = False

        if source == "mock":
            self.sim = SwarmSimulator(cfg.get("system.frame_width", 640),
                                      cfg.get("system.frame_height", 480),
                                      cfg.get("system.mock_insects", 6))
        elif source and source != "camera":
            self.cap = cv2.VideoCapture(source)
            self.is_file = True
        else:
            self.cap = cv2.VideoCapture(cfg.get("system.camera_index", 0))
            self.is_file = False

        self.fires = 0
        self.rejects: dict[str, int] = {}
        self.fps = 0.0
        self._n = 0
        self._t0 = time.time()

    # -- external control ---------------------------------------------------
    def request_stop(self):      self.running = False
    def request_pause(self, p):  self.paused = p
    def arm(self):
        # Arm AND enter active mode. Arming alone leaves gate 3 (require_active_mode)
        # blocking every shot, so the operator would see "ARMED" and nothing else.
        self.laser.safety.arm()
        self.laser.safety.set_active(True)

    def disarm(self):
        self.laser.safety.disarm()
        self.laser.safety.set_active(False)

    def estop(self):
        self.laser.safety.trigger_emergency_stop()
        self.laser.safety.set_active(False)

    def reset_estop(self):       self.laser.safety.reset_emergency_stop()

    # -- main loop ----------------------------------------------------------
    def run(self):
        w = self.cfg.get("system.frame_width", 640)
        h = self.cfg.get("system.frame_height", 480)
        dt = 1.0 / max(self.cfg.get("system.fps", 30), 1)

        if self.cap is not None and not self.is_file:
            for _ in range(10):
                self.cap.read()      # flush stale auto-exposure frames

        while self.running:
            if self.paused:
                time.sleep(0.05)
                continue

            if self.sim is not None:
                self.sim.step(dt)
                frame = self.sim.frame()
                time.sleep(dt)
            elif self.cap is not None:
                ok, frame = self.cap.read()
                if not ok:
                    if self.is_file:
                        self.running = False
                        break
                    time.sleep(0.01)
                    continue

            self._n += 1
            dets = self.detector.detect(frame)
            tracks = self.tracker.update(dets)
            confirmed = [t for t in tracks if t.confirmed]

            target = None
            if confirmed:
                cx, cy = w / 2, h / 2
                target = min(confirmed,
                             key=lambda t: (t.last_x - cx) ** 2 + (t.last_y - cy) ** 2)

            ax = ay = None
            if target is not None:
                ax, ay = self.tracker.predict_ahead(target)
                self.servo.aim_at(ax, ay)

            fired, reason = False, "no target"
            if target is not None and ax is not None and ay is not None:
                fired, reason = self.laser.try_fire(target, ax, ay, w // 2, h // 2)
                if fired:
                    self.fires += 1
                else:
                    key = reason.split(" ")[0].split("(")[0].strip()
                    self.rejects[key] = self.rejects.get(key, 0) + 1

            vis = self.overlay.render(frame.copy(), dets, tracks,
                                      self.servo.get_status(),
                                      self.laser.safety.get_status(), self.fps)

            el = time.time() - self._t0
            if el > 0.5:
                self.fps = self._n / el

            # publish newest frame only; drop stale ones so the UI keeps up
            try:
                self.out_q.get_nowait()
            except queue.Empty:
                pass
            try:
                self.out_q.put_nowait((vis, fired, reason, target is not None))
            except queue.Full:
                pass

            try:
                self.tel_q.get_nowait()
            except queue.Empty:
                pass
            try:
                self.tel_q.put_nowait({
                    "fps": self.fps,
                    "detections": len(dets),
                    "tracks": len(tracks),
                    "confirmed": len(confirmed),
                    "target": target.track_id if target else None,
                    "reason": reason,
                    "fires": self.fires,
                    "rejects": dict(self.rejects),
                    "servo": self.servo.get_status(),
                    "laser": self.laser.safety.get_status(),
                    "frame": self._n,
                })
            except queue.Full:
                pass

        if self.cap is not None:
            self.cap.release()
        self.laser.cleanup()
        self.servo.cleanup()


class App(tk.Tk):
    def __init__(self, cfg, source):
        super().__init__()
        self.cfg = cfg
        self._source = source
        self.title("Mosquito Zapper — Control")
        self.configure(bg=BG)
        self.geometry("1180x760")
        self.minsize(980, 660)

        self.out_q: queue.Queue = queue.Queue(maxsize=2)
        self.tel_q: queue.Queue = queue.Queue(maxsize=2)
        self.pipe: Pipeline | None = None
        self._photo = None
        self._last_tel = {}

        self._build_style()
        self._build_ui()

        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(60, self._tick)

    # -- chrome -------------------------------------------------------------
    def _build_style(self):
        s = ttk.Style(self)
        try:
            s.theme_use("clam")
        except tk.TclError:
            pass
        s.configure("TFrame", background=BG)
        s.configure("Panel.TFrame", background=PANEL)
        s.configure("TLabel", background=BG, foreground=FG,
                    font=("Segoe UI", 9))
        s.configure("Head.TLabel", background=BG, foreground=ACCENT,
                    font=("Segoe UI Semibold", 10))
        s.configure("Big.TLabel", background=PANEL, foreground=FG,
                    font=("Consolas", 15, "bold"))
        s.configure("Small.TLabel", background=PANEL, foreground=MUTED,
                    font=("Segoe UI", 8))
        s.configure("TCheckbutton", background=PANEL, foreground=FG,
                    font=("Segoe UI", 9))

    def _btn(self, parent, text, cmd, kind="normal", width=15):
        colors = {
            "normal": (PANEL_HI, FG),
            "accent": ("#0a3d34", ACCENT),
            "warn":   ("#3d2f0a", WARN),
            "danger": ("#3d0a18", DANGER),
        }
        bg, fg = colors[kind]
        b = tk.Button(parent, text=text, command=cmd, width=width,
                      bg=bg, fg=fg, activebackground=bg, activeforeground=fg,
                      relief="flat", bd=0, highlightthickness=1,
                      highlightbackground="#2a353d", font=("Segoe UI", 9),
                      padx=8, pady=5, cursor="hand2")
        return b

    def _build_ui(self):
        top = tk.Frame(self, bg=BG)
        top.pack(fill="x", padx=12, pady=(10, 6))

        tk.Label(top, text="MOSQUITO ZAPPER", bg=BG, fg=ACCENT,
                 font=("Segoe UI Semibold", 13)).pack(side="left")
        tk.Label(top, text="  control", bg=BG, fg=MUTED,
                 font=("Segoe UI", 11)).pack(side="left")
        self.src_lbl = tk.Label(top, text="", bg=BG, fg=MUTED,
                                font=("Consolas", 9))
        self.src_lbl.pack(side="right")

        body = tk.Frame(self, bg=BG)
        body.pack(fill="both", expand=True, padx=12, pady=(0, 10))

        # --- left: video ---------------------------------------------------
        left = tk.Frame(body, bg=BG)
        left.pack(side="left", fill="both", expand=True)

        self.video = tk.Label(left, bg="#05080a", bd=0)
        self.video.pack(fill="both", expand=True)

        self.status = tk.Label(left, text="idle", bg=BG, fg=MUTED,
                               font=("Consolas", 9), anchor="w")
        self.status.pack(fill="x", pady=(4, 0))

        # --- right: controls ----------------------------------------------
        right = tk.Frame(body, bg=BG, width=330)
        right.pack(side="right", fill="y", padx=(12, 0))
        right.pack_propagate(False)

        # telemetry
        tel = tk.Frame(right, bg=PANEL)
        tel.pack(fill="x", pady=(0, 10))
        tk.Label(tel, text="TELEMETRY", bg=PANEL, fg=ACCENT,
                 font=("Segoe UI Semibold", 9)).grid(
                     row=0, column=0, columnspan=2, sticky="w", padx=10, pady=(8, 4))
        self.tel_vars = {}
        rows = [("fps", "FPS"), ("detections", "Detections"), ("tracks", "Tracks"),
                ("confirmed", "Confirmed"), ("target", "Target"), ("fires", "Fires")]
        for i, (k, label) in enumerate(rows, start=1):
            tk.Label(tel, text=label, bg=PANEL, fg=MUTED,
                     font=("Segoe UI", 8)).grid(row=i, column=0, sticky="w",
                                                padx=(10, 4), pady=1)
            v = tk.StringVar(value="—")
            self.tel_vars[k] = v
            tk.Label(tel, textvariable=v, bg=PANEL, fg=FG,
                     font=("Consolas", 11, "bold")).grid(
                         row=i, column=1, sticky="e", padx=(4, 10), pady=1)
        tk.Frame(tel, bg=PANEL, height=6).grid(row=len(rows) + 1, column=0)

        # interlock
        il = tk.Frame(right, bg=PANEL)
        il.pack(fill="x", pady=(0, 10))
        tk.Label(il, text="INTERLOCKS", bg=PANEL, fg=ACCENT,
                 font=("Segoe UI Semibold", 9)).pack(anchor="w", padx=10, pady=(8, 4))
        self.il_rows = {}
        for key, label in [
            ("manual_arm", "Armed"),
            ("require_wingbeat", "Species gate required"),
            ("wingbeat_ok", "Species confirmed"),
            ("emergency_stop", "Emergency stop"),
            ("is_active", "Active mode"),
        ]:
            fr = tk.Frame(il, bg=PANEL)
            fr.pack(fill="x", padx=10, pady=1)
            tk.Label(fr, text=label, bg=PANEL, fg=MUTED,
                     font=("Segoe UI", 8)).pack(side="left")
            dot = tk.Label(fr, text="●", bg=PANEL, fg=MUTED,
                           font=("Segoe UI", 10))
            dot.pack(side="right")
            self.il_rows[key] = dot
        self.wb_lbl = tk.Label(il, text="", bg=PANEL, fg=MUTED,
                               font=("Consolas", 8), wraplength=290,
                               justify="left")
        self.wb_lbl.pack(anchor="w", padx=10, pady=(4, 8))

        # rejections
        rj = tk.Frame(right, bg=PANEL)
        rj.pack(fill="x", pady=(0, 10))
        tk.Label(rj, text="BEAM HELD OFF BY", bg=PANEL, fg=ACCENT,
                 font=("Segoe UI Semibold", 9)).pack(anchor="w", padx=10, pady=(8, 4))
        self.rj_lbl = tk.Label(rj, text="—", bg=PANEL, fg=WARN,
                               font=("Consolas", 8), justify="left", anchor="w",
                               wraplength=290)
        self.rj_lbl.pack(anchor="w", padx=10, pady=(0, 8))

        # run controls
        run = tk.Frame(right, bg=BG)
        run.pack(fill="x", pady=(0, 8))
        self.b_start = self._btn(run, "▶  Start", self._start, "accent", 13)
        self.b_start.pack(side="left", padx=(0, 4))
        self.b_pause = self._btn(run, "⏸  Pause", self._toggle_pause, "normal", 13)
        self.b_pause.pack(side="left")
        self.b_pause.config(state="disabled")

        # laser controls
        tk.Label(right, text="LASER", bg=BG, fg=ACCENT,
                 font=("Segoe UI Semibold", 9)).pack(anchor="w", pady=(6, 4))
        lz = tk.Frame(right, bg=BG)
        lz.pack(fill="x")
        self.b_arm = self._btn(lz, "ARM", self._arm, "warn", 8)
        self.b_arm.pack(side="left", padx=(0, 4))
        self.b_dis = self._btn(lz, "DISARM", self._disarm, "normal", 8)
        self.b_dis.pack(side="left", padx=(0, 4))
        self.b_rst = self._btn(lz, "RESET", self._reset, "normal", 8)
        self.b_rst.pack(side="left")

        self.b_estop = tk.Button(right, text="■  EMERGENCY STOP",
                                 command=self._estop, bg="#5a0a1c", fg="#ffdde4",
                                 activebackground="#7a0e26", activeforeground="#fff",
                                 relief="flat", bd=0, highlightthickness=0,
                                 font=("Segoe UI Semibold", 10), pady=8,
                                 cursor="hand2")
        self.b_estop.pack(fill="x", pady=(8, 0))

        self.gate_var = tk.BooleanVar(value=False)
        cb = ttk.Checkbutton(right, text="Bench mode (disable species gate)",
                             variable=self.gate_var, command=self._toggle_gate)
        cb.pack(anchor="w", pady=(8, 0))
        tk.Label(right, text="Only for bench testing with no sensor head.\n"
                             "Never with a live beam.",
                 bg=BG, fg=MUTED, font=("Segoe UI", 8),
                 justify="left").pack(anchor="w")

        self._set_source_label(self._source)

    def _set_source_label(self, source):
        if source == "mock":
            self.src_lbl.config(text="source: synthesised swarm (no hardware)")
        elif source and source != "camera":
            self.src_lbl.config(text=f"source: {Path(source).name}")
        else:
            idx = self.cfg.get("system.camera_index", 0)
            self.src_lbl.config(text=f"source: camera {idx}")

    # -- actions ------------------------------------------------------------
    def _start(self):
        if self.pipe and self.pipe.is_alive():
            return
        src = self._source
        self.pipe = Pipeline(self.cfg, src, self.out_q, self.tel_q)
        self.pipe.start()
        self.b_start.config(state="disabled")
        self.b_pause.config(state="normal", text="⏸  Pause")

    def _toggle_pause(self):
        if not self.pipe:
            return
        self.pipe.paused = not self.pipe.paused
        self.b_pause.config(text="▶  Resume" if self.pipe.paused else "⏸  Pause")

    def _arm(self):
        if self.pipe:
            self.pipe.arm()

    def _disarm(self):
        if self.pipe:
            self.pipe.disarm()

    def _reset(self):
        if self.pipe:
            self.pipe.reset_estop()

    def _estop(self):
        if self.pipe:
            self.pipe.estop()
            self.b_estop.config(bg="#8a1030")
            self.after(400, lambda: self.b_estop.config(bg="#5a0a1c"))

    def _toggle_gate(self):
        if not self.pipe:
            return
        on = not self.gate_var.get()      # checkbox = "bench mode"
        self.pipe.laser.safety._require_wingbeat = on
        if on:
            self.pipe.laser.safety._firing_zone_radius = 480
            self.pipe.laser.safety._cooldown_per_target_ms = 120
            self.pipe.laser.safety._min_between_shots_ms = 60

    # -- render loop --------------------------------------------------------
    def _tick(self):
        try:
            vis, fired, reason, has_target = self.out_q.get_nowait()
            self._show(vis)
        except queue.Empty:
            pass

        try:
            tel = self.tel_q.get_nowait()
            self._last_tel = tel
            self._render_telemetry(tel)
        except queue.Empty:
            pass

        self.after(30, self._tick)

    def _show(self, bgr):
        rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
        h, w = rgb.shape[:2]
        # letterbox into the available label area
        aw = max(self.video.winfo_width(), 320)
        ah = max(self.video.winfo_height(), 240)
        s = min(aw / w, ah / h)
        nw, nh = max(int(w * s), 1), max(int(h * s), 1)
        interp = cv2.INTER_LINEAR if s > 1 else cv2.INTER_AREA
        resized = cv2.resize(rgb, (nw, nh), interpolation=interp)
        self._photo = tk.PhotoImage(data=cv2.imencode(
            ".png", cv2.cvtColor(resized, cv2.COLOR_RGB2BGR))[1].tobytes())
        self.video.config(image=self._photo)

    def _render_telemetry(self, t):
        for k, v in self.tel_vars.items():
            val = t.get(k)
            if val is None:
                v.set("—")
            elif k == "fps":
                v.set(f"{val:.0f}")
            else:
                v.set(str(val))
        self.status.config(
            text=f"frame {t['frame']}   |   {t['reason']}   |   "
                 f"{'TARGET LOCKED' if t['target'] is not None else 'searching'}")

        ls = t["laser"]
        for key, dot in self.il_rows.items():
            if key == "is_active":
                on = bool(t.get("confirmed", 0)) and not ls.get("emergency_stop")
            else:
                on = bool(ls.get(key, False))
            if key == "emergency_stop":
                dot.config(fg=DANGER if on else MUTED)
            else:
                dot.config(fg=OK if on else MUTED)

        wb = ls.get("wingbeat_reason", "")
        fq = ls.get("wingbeat_freq_hz") or 0
        cf = ls.get("wingbeat_confidence") or 0
        self.wb_lbl.config(
            text=f"{fq:.0f} Hz  conf {cf:.2f}\n{wb}" if fq else wb or "no evidence")

        rej = t.get("rejects") or {}
        if rej:
            self.rj_lbl.config(text="\n".join(
                f"{k:26s} {v}" for k, v in sorted(rej.items(),
                                                 key=lambda kv: -kv[1])[:5]))
        else:
            self.rj_lbl.config(text="—")

    def _on_close(self):
        if self.pipe:
            self.pipe.request_stop()
            self.pipe.join(timeout=2.0)
        self.destroy()


def main():
    ap = argparse.ArgumentParser(description="Mosquito Zapper desktop control")
    ap.add_argument("--video", help="video file to replay")
    ap.add_argument("--mock", action="store_true",
                    help="synthesised swarm, no camera needed")
    ap.add_argument("--camera", type=int, default=None, help="camera index")
    ap.add_argument("--autostart", action="store_true")
    args = ap.parse_args()

    cfg = Config(str(HERE / "config.yaml"))
    cfg._data.setdefault("laser", {})["mock_laser"] = True
    cfg._data.setdefault("pan_tilt", {})["mock_servos"] = True
    cfg._data["pan_tilt"]["pwm_backend"] = "mock"

    if args.camera is not None:
        cfg._data.setdefault("system", {})["camera_index"] = args.camera

    source = "mock" if args.mock else (args.video or "camera")

    app = App(cfg, source)
    if args.autostart:
        app.after(200, app._start)
    app.mainloop()


if __name__ == "__main__":
    main()
