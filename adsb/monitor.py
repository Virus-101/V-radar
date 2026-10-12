"""
ADS-B airspace monitor — live radar-style map.

Passive receive only. There is no transmitter in this program and none is needed:
aircraft broadcast their own position and we decode it. Receiving 1090 MHz is
unlicensed in every jurisdiction, so this is legal anywhere, including Morocco.

    python monitor.py --synthetic            # demo with no hardware
    python monitor.py --lat 0.0 --lon 0.0
    python monitor.py --file capture.txt     # replay a recording
    python monitor.py --rtl                 # live RTL-SDR via rtl_adsb

If the flag is --synthetic the display is stamped SYNTHETIC so a demo can never be
mistaken for real airspace.
"""

from __future__ import annotations

import argparse
import math
import sys
import time
import tkinter as tk
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from receiver import (FileSource, RTLSDBSource, SyntheticTracker,  # noqa: E402
                      Tracker)

BG      = "#060a0d"
GRID    = "#0f1a20"
RING    = "#16333d"
FG      = "#d8e4e8"
MUTED   = "#5d7a84"
ACCENT  = "#00e5c0"
WARN    = "#ffb020"
DANGER  = "#ff4d6a"
TRAIL   = "#2f6b7a"


class MapView(tk.Canvas):
    """Polar scope centred on the receiver."""

    def __init__(self, master, tracker: Tracker, **kw):
        super().__init__(master, bg=BG, highlightthickness=0, **kw)
        self.tracker = tracker
        self.scale_km = 100.0        # km from centre to edge
        self.selected: str | None = None
        self.bind("<Configure>", lambda e: self.redraw())
        self.bind("<Button-1>", self._on_click)

    def set_scale(self, km: float):
        self.scale_km = km
        self.redraw()

    def _on_click(self, ev):
        cx, cy = self.winfo_width() / 2, self.winfo_height() / 2
        radius = min(cx, cy) - 14
        best, best_d = None, 22
        for t in self.tracker.snapshot():
            x, y = self._project(t, cx, cy, radius)
            d = math.hypot(x - ev.x, y - ev.y)
            if d < best_d:
                best, best_d = t.icao, d
        self.selected = best
        self.redraw()

    def _project(self, track, cx, cy, radius):
        """lat/lon -> canvas xy, north up, equirectangular at the reference."""
        ref_lat = self.tracker.ref_lat
        ref_lon = self.tracker.ref_lon
        dy_km = (track.lat - ref_lat) * 111.0
        dx_km = (track.lon - ref_lon) * 111.0 * math.cos(math.radians(ref_lat))
        k = radius / self.scale_km
        return cx + dx_km * k, cy - dy_km * k

    def redraw(self):
        self.delete("all")
        w, h = self.winfo_width(), self.winfo_height()
        if w < 40 or h < 40:
            return
        cx, cy = w / 2, h / 2
        radius = min(cx, cy) - 14

        # range rings
        for frac in (0.25, 0.5, 0.75, 1.0):
            r = radius * frac
            self.create_oval(cx - r, cy - r, cx + r, cy + r,
                             outline=RING, width=1)
            self.create_text(cx + 5, cy - r + 9, anchor="w",
                             text=f"{self.scale_km * frac:.0f} km",
                             fill=MUTED, font=("Consolas", 7))

        # cardinal spokes
        for ang, lab in ((0, "N"), (90, "E"), (180, "S"), (270, "W")):
            a = math.radians(ang)
            x, y = cx + math.sin(a) * radius, cy - math.cos(a) * radius
            self.create_line(cx, cy, x, y, fill=GRID, width=1)
            self.create_text(cx + math.sin(a) * (radius + 0),
                             cy - math.cos(a) * (radius + 0),
                             text=lab, fill=MUTED, font=("Consolas", 8, "bold"))

        # receiver
        self.create_oval(cx - 3, cy - 3, cx + 3, cy + 3, fill=ACCENT, outline="")
        self.create_text(cx, cy + 14, text="RX", fill=ACCENT,
                         font=("Consolas", 7, "bold"))

        tracks = self.tracker.snapshot()
        # draw low tracks first so high ones sit on top
        for t in sorted(tracks, key=lambda z: z.altitude_ft):
            x, y = self._project(t, cx, cy, radius)
            if not (0 <= x <= w and 0 <= y <= h):
                continue

            # trail
            pts = []
            for (la, lo) in (t.trail or [])[-40:]:
                dy = (la - self.tracker.ref_lat) * 111.0
                dx = (lo - self.tracker.ref_lon) * 111.0 * math.cos(
                    math.radians(self.tracker.ref_lat))
                k = radius / self.scale_km
                pts += [cx + dx * k, cy - dy * k]
            if len(pts) >= 4:
                self.create_line(*pts, fill=TRAIL, width=1)

            col = self._colour(t)
            sel = (t.icao == self.selected)
            r = 5 if sel else 4

            # heading vector, length scaled by speed
            a = math.radians(t.track_deg)
            L = 8 + min(t.ground_speed_kt / 40.0, 12)
            self.create_line(x, y, x + math.sin(a) * L, y - math.cos(a) * L,
                             fill=col, width=2, arrow="last",
                             arrowshape=(6, 7, 3))
            self.create_oval(x - r, y - r, x + r, y + r,
                             fill=BG, outline=col, width=2 if sel else 1)
            if sel:
                self.create_oval(x - 10, y - 10, x + 10, y + 10,
                                 outline=col, width=1)

            if t.callsign:
                self.create_text(x + 9, y - 8, anchor="w", text=t.callsign,
                                 fill=col, font=("Consolas", 8, "bold"))
                self.create_text(x + 9, y + 3, anchor="w",
                                 text=f"{t.altitude_ft:,.0f}ft "
                                      f"{t.distance_km(self.tracker.ref_lat, self.tracker.ref_lon):.0f}km",
                                 fill=MUTED, font=("Consolas", 7))

    @staticmethod
    def _colour(t):
        """Colour by altitude band, so the picture reads at a glance."""
        if t.source == "synthetic":
            return "#9b7bd4"
        a = t.altitude_ft
        if a < 500:
            return "#7a8a92"
        if a < 5000:
            return WARN
        if a < 10000:
            return ACCENT
        return "#5ad0ff"


class Monitor(tk.Tk):
    def __init__(self, tracker: Tracker, ref_lat, ref_lon, synthetic: bool):
        super().__init__()
        self.title("ADS-B Airspace Monitor  —  passive receive, no transmitter")
        self.configure(bg=BG)
        self.geometry("1240x800")
        self.minsize(900, 620)
        self.tracker = tracker
        self.ref_lat, self.ref_lon = ref_lat, ref_lon
        self.synthetic = synthetic
        self._last_draw = 0.0

        self._build()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(80, self._tick)

    def _build(self):
        top = tk.Frame(self, bg=BG)
        top.pack(fill="x", padx=12, pady=(10, 4))
        tk.Label(top, text="ADS-B AIRSPACE MONITOR", bg=BG, fg=ACCENT,
                 font=("Segoe UI Semibold", 13)).pack(side="left")
        tk.Label(top, text="   passive 1090 MHz receive  ·  no transmitter",
                 bg=BG, fg=MUTED, font=("Segoe UI", 9)).pack(side="left")
        if self.synthetic:
            tk.Label(top, text="  SYNTHETIC DATA — NOT REAL AIRSPACE  ",
                     bg="#3a2a55", fg="#d8c4ff",
                     font=("Segoe UI Semibold", 9)).pack(side="right")

        body = tk.Frame(self, bg=BG)
        body.pack(fill="both", expand=True, padx=12, pady=(0, 10))

        left = tk.Frame(body, bg=BG)
        left.pack(side="left", fill="both", expand=True)
        self.map = MapView(left, self.tracker)
        self.map.pack(fill="both", expand=True)

        # scale buttons
        sc = tk.Frame(left, bg=BG)
        sc.pack(fill="x", pady=(6, 0))
        tk.Label(sc, text="range", bg=BG, fg=MUTED,
                 font=("Segoe UI", 8)).pack(side="left", padx=(0, 6))
        for km in (25, 50, 100, 200, 400):
            b = tk.Button(sc, text=f"{km}", width=4, relief="flat", bd=0,
                          bg="#12181d", fg=FG, activebackground="#1a232b",
                          font=("Consolas", 8), cursor="hand2",
                          command=lambda k=km: self.map.set_scale(k))
            b.pack(side="left", padx=2)

        right = tk.Frame(body, bg=BG, width=340)
        right.pack(side="right", fill="y", padx=(12, 0))
        right.pack_propagate(False)

        stats = tk.Frame(right, bg="#0e1418")
        stats.pack(fill="x", pady=(0, 8))
        tk.Label(stats, text="RECEPTION", bg="#0e1418", fg=ACCENT,
                 font=("Segoe UI Semibold", 9)).grid(
                     row=0, column=0, columnspan=2, sticky="w", padx=10, pady=(8, 3))
        self.sv = {}
        for i, (k, lab) in enumerate([
            ("tracks", "Aircraft"), ("frames", "Frames"), ("fps", "Msg/s"),
            ("max_range_km", "Max range"), ("reception_radius_km", "Horizon"),
            ("crc_fail", "CRC fail"), ("repaired", "Repaired"),
        ], start=1):
            tk.Label(stats, text=lab, bg="#0e1418", fg=MUTED,
                     font=("Segoe UI", 8)).grid(row=i, column=0, sticky="w",
                                                padx=(10, 4), pady=1)
            v = tk.StringVar(value="—")
            self.sv[k] = v
            tk.Label(stats, textvariable=v, bg="#0e1418", fg=FG,
                     font=("Consolas", 10, "bold")).grid(
                         row=i, column=1, sticky="e", padx=(4, 10), pady=1)
        tk.Frame(stats, bg="#0e1418", height=6).grid(row=8, column=0)

        tk.Label(right, text="TRAFFIC", bg=BG, fg=ACCENT,
                 font=("Segoe UI Semibold", 9)).pack(anchor="w", pady=(4, 3))
        self.list = tk.Listbox(right, bg="#0e1418", fg=FG, bd=0,
                               highlightthickness=0, font=("Consolas", 8),
                               selectbackground="#1c3a44", activestyle="none")
        self.list.pack(fill="both", expand=True)
        self.list.bind("<<ListboxSelect>>", self._on_select)

        self.detail = tk.Label(right, text="", bg="#0e1418", fg=MUTED,
                               font=("Consolas", 8), justify="left",
                               anchor="w", wraplength=320)
        self.detail.pack(fill="x", pady=(6, 0))

    def _on_select(self, _ev):
        sel = self.list.curselection()
        if sel:
            icao = self.list.get(sel[0]).split()[0]
            self.map.selected = icao
            self.map.redraw()

    def _tick(self):
        now = time.time()
        if now - self._last_draw > 0.5:
            self._last_draw = now
            self._refresh()
        self.after(80, self._tick)

    def _refresh(self):
        s = self.tracker.stats()
        for k, var in self.sv.items():
            v = s.get(k)
            if v is None:
                var.set("—")
            elif k in ("max_range_km", "reception_radius_km"):
                var.set(f"{v:.0f} km")
            elif k == "fps":
                var.set(f"{v:.0f}")
            else:
                var.set(str(v))

        tracks = sorted(self.tracker.snapshot(),
                        key=lambda t: t.distance_km(self.ref_lat, self.ref_lon))
        cur = self.list.curselection()
        keep = self.list.get(cur[0]).split()[0] if cur else None
        self.list.delete(0, "end")
        for t in tracks:
            d = t.distance_km(self.ref_lat, self.ref_lon)
            self.list.insert("end",
                             f"{t.icao} {t.callsign or '—':8s} {d:6.0f}km "
                             f"{t.altitude_ft:7.0f}ft")
            if keep and t.icao == keep:
                self.list.selection_set("end")

        sel = self.map.selected
        if sel and sel in self.tracker.tracks:
            t = self.tracker.tracks[sel]
            brg = t.bearing_from(self.ref_lat, self.ref_lon)
            self.detail.config(
                text=f"ICAO {t.icao}   {t.callsign or '—'}\n"
                     f"{t.aircraft_type or 'type unknown'}\n"
                     f"{t.lat:.4f}, {t.lon:.4f}\n"
                     f"alt {t.altitude_ft:,.0f} ft   vs {t.vertical_rate_fpm:+.0f} fpm\n"
                     f"gs {t.ground_speed_kt:.0f} kt   trk {t.track_deg:.0f}°\n"
                     f"range {t.distance_km(self.ref_lat, self.ref_lon):.1f} km  "
                     f"brg {brg:.0f}°\n"
                     f"radio horizon {t.max_range_km():.0f} km\n"
                     f"msgs {t.messages}   source {t.source}")
        self.map.redraw()

    def _on_close(self):
        self.tracker.stop()
        self.destroy()


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--lat", type=float, default=0.0,
                    help="receiver latitude (default: 0,0 - set to your site)")
    ap.add_argument("--lon", type=float, default=0.0,
                    help="receiver longitude")
    ap.add_argument("--synthetic", action="store_true",
                    help="generate demo traffic (no hardware)")
    ap.add_argument("--file", help="replay newline-delimited hex frames")
    ap.add_argument("--rtl", action="store_true",
                    help="live RTL-SDR via rtl_adsb")
    ap.add_argument("--rtl-tool", default="rtl_adsb")
    args = ap.parse_args()

    if args.synthetic:
        tracker = SyntheticTracker(args.lat, args.lon, n=18)
    elif args.file:
        tracker = Tracker(FileSource(args.file, realtime=True),
                          args.lat, args.lon)
    elif args.rtl:
        tracker = Tracker(RTLSDBSource(args.rtl_tool), args.lat, args.lon)
    else:
        print("Choose a source: --synthetic, --file, or --rtl")
        print("(defaulting to --synthetic so you can see the map)")
        tracker = SyntheticTracker(args.lat, args.lon, n=18)

    tracker.start()
    app = Monitor(tracker, args.lat, args.lon,
                  synthetic=isinstance(tracker, SyntheticTracker))
    app.mainloop()


if __name__ == "__main__":
    main()
