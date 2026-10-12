"""
ADS-B Airspace Monitor — desktop application.

A full control surface over the passive 1090 MHz receiver in this folder. No
transmitter anywhere: receiving ADS-B is unlicensed in every jurisdiction.

    python desktop.py                       # synthetic demo (no hardware)
    python desktop.py --rtl                 # live RTL-SDR
    python desktop.py --file capture.txt    # replay a recording
    python desktop.py --lat 0.0 --lon 0.0

Features
    * polar scope with range rings, trails, altitude colouring, heading vectors
    * live filtering: altitude band, max range, callsign/ICAO search, watchlist
    * sortable traffic table with selection -> detail panel + map highlight
    * record raw frames to a capture file for later replay
    * export the current picture to CSV / JSON
    * reception analytics: message rate, CRC health, altitude distribution

Design notes
    * The tracker runs on its own thread; the UI polls a snapshot, so a slow
      source never freezes the window.
    * All decode state is owned by the tracker. The UI only ever reads.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from receiver import (FileSource, RTLSDBSource, SyntheticTracker,  # noqa: E402
                      Tracker)
from position_fusion import (PositionSource, Status, fuse,       # noqa: E402
                             mlat_sigma_from_gdop)

# --- palette ---------------------------------------------------------------
BG      = "#060a0d"
PANEL   = "#0e1418"
PANEL2  = "#121b21"
BORDER  = "#1d2b33"
FG      = "#d8e4e8"
MUTED   = "#5d7a84"
ACCENT  = "#00e5c0"
WARN    = "#ffb020"
DANGER  = "#ff4d6a"
VIOLET  = "#9b7bd4"
TRAIL   = "#2f6b7a"

ALT_BANDS = [
    ("Ground",   0,      500,    "#7a8a92"),
    ("Low",      500,    5000,   WARN),
    ("Mid",      5000,   10000,  ACCENT),
    ("High",     10000,  60000,  "#5ad0ff"),
]


# --- MLAT support on Track -------------------------------------------------
# Additive fields, attached here rather than editing receiver.py: the receiver
# knows nothing about MLAT and should not have to.
def _track_mlat_lat(self):
    return getattr(self, "_mlat_lat", None)


def _install_mlat_fields():
    from receiver import Track
    if getattr(Track, "_mlat_installed", False):
        return
    def _init(self, *a, **kw):
        self._mlat_lat = None
        self._mlat_lon = None
        self._mlat_sigma = None
        self._mlat_gdop = None
        self._fusion = None
    orig_post = Track.__post_init__

    def post(self):
        orig_post(self)
        _init(self)
    Track.__post_init__ = post
    Track._mlat_installed = True


_install_mlat_fields()


def band_colour(alt_ft: float, synthetic: bool) -> str:
    if synthetic:
        return VIOLET
    for _, lo, hi, col in ALT_BANDS:
        if lo <= alt_ft < hi:
            return col
    return "#5ad0ff"


def band_name(alt_ft: float) -> str:
    for name, lo, hi, _ in ALT_BANDS:
        if lo <= alt_ft < hi:
            return name
    return "High"


class Scope(tk.Canvas):
    """Polar airspace scope, north-up, centred on the receiver."""

    def __init__(self, master, app: "App", **kw):
        super().__init__(master, bg=BG, highlightthickness=0, **kw)
        self.app = app
        self.selected: str | None = None
        self.show_trails = True
        self.show_labels = True
        self.show_vectors = True
        self.bind("<Configure>", lambda e: self.redraw())
        self.bind("<Button-1>", self._on_click)

    # -- interaction --------------------------------------------------------
    def _on_click(self, ev):
        cx, cy = self.winfo_width() / 2, self.winfo_height() / 2
        radius = min(cx, cy) - 16
        best, best_d = None, 24
        for t in self.app.visible_tracks():
            x, y = self._project(t, cx, cy, radius)
            d = math.hypot(x - ev.x, y - ev.y)
            if d < best_d:
                best, best_d = t.icao, d
        self.selected = best
        self.app.select_icao(best)
        self.redraw()

    # -- projection ---------------------------------------------------------
    def _project(self, t, cx, cy, radius):
        ref_lat = self.app.tracker.ref_lat
        ref_lon = self.app.tracker.ref_lon
        dy_km = (t.lat - ref_lat) * 111.0
        dx_km = (t.lon - ref_lon) * 111.0 * math.cos(math.radians(ref_lat))
        k = radius / max(self.app.range_km, 1.0)
        return cx + dx_km * k, cy - dy_km * k

    # -- render -------------------------------------------------------------
    def redraw(self):
        self.delete("all")
        w, h = self.winfo_width(), self.winfo_height()
        if w < 60 or h < 60:
            return
        cx, cy = w / 2, h / 2
        radius = min(cx, cy) - 16
        R = self.app.range_km

        for frac in (0.25, 0.5, 0.75, 1.0):
            r = radius * frac
            self.create_oval(cx - r, cy - r, cx + r, cy + r,
                             outline=BORDER, width=1)
            self.create_text(cx + 4, cy - r + 9, anchor="w",
                             text=f"{R * frac:.0f} km", fill=MUTED,
                             font=("Consolas", 7))

        for ang, lab in ((0, "N"), (90, "E"), (180, "S"), (270, "W")):
            a = math.radians(ang)
            x, y = cx + math.sin(a) * radius, cy - math.cos(a) * radius
            self.create_line(cx, cy, x, y, fill="#0f1a20", width=1)
            self.create_text(x, y - 9 if ang == 0 else (y + 9 if ang == 180 else y),
                             text=lab, fill=MUTED, font=("Consolas", 8, "bold"))

        self.create_oval(cx - 3, cy - 3, cx + 3, cy + 3, fill=ACCENT, outline="")
        self.create_text(cx, cy + 15, text="RX", fill=ACCENT,
                         font=("Consolas", 7, "bold"))

        tracks = self.app.visible_tracks()
        for t in sorted(tracks, key=lambda z: z.altitude_ft):
            x, y = self._project(t, cx, cy, radius)
            if not (-20 <= x <= w + 20 and -20 <= y <= h + 20):
                continue
            col = band_colour(t.altitude_ft, t.source == "synthetic")
            sel = (t.icao == self.selected)

            if self.show_trails and t.trail:
                pts = []
                for (la, lo) in t.trail[-60:]:
                    dy = (la - self.app.tracker.ref_lat) * 111.0
                    dx = (lo - self.app.tracker.ref_lon) * 111.0 * math.cos(
                        math.radians(self.app.tracker.ref_lat))
                    k = radius / max(R, 1.0)
                    pts += [cx + dx * k, cy - dy * k]
                if len(pts) >= 4:
                    self.create_line(*pts, fill=TRAIL, width=1)

            if self.show_vectors:
                a = math.radians(t.track_deg)
                L = 9 + min(t.ground_speed_kt / 35.0, 14)
                self.create_line(x, y, x + math.sin(a) * L, y - math.cos(a) * L,
                                 fill=col, width=2, arrow="last",
                                 arrowshape=(6, 7, 3))

            # integrity flag: a red ring around a track whose ADS-B position
            # disagrees with MLAT beyond what the geometry allows
            fr = self.app.integrity.get(t.icao) if self.app.v_mlat.get() else None
            if fr is not None and fr.status in (Status.SUSPECT, Status.DIVERGENT):
                ring = DANGER if fr.status is Status.DIVERGENT else WARN
                rr = 9
                self.create_oval(x - rr, y - rr, x + rr, y + rr,
                                 outline=ring, width=2, dash=(3, 2))
                self.create_text(x, y - rr - 7, text="!", fill=ring,
                                 font=("Consolas", 9, "bold"))

            r = 5 if sel else 4
            self.create_oval(x - r, y - r, x + r, y + r, fill=BG,
                             outline=col, width=2 if sel else 1)
            if sel:
                self.create_oval(x - 11, y - 11, x + 11, y + 11,
                                 outline=col, width=1)

            if self.show_labels and (sel or self.app.label_all):
                self.create_text(x + 9, y - 7, anchor="w",
                                 text=t.callsign or t.icao, fill=col,
                                 font=("Consolas", 8, "bold"))
                self.create_text(x + 9, y + 4, anchor="w",
                                 text=f"{t.altitude_ft:,.0f}ft "
                                      f"{t.distance_km(self.app.tracker.ref_lat, self.app.tracker.ref_lon):.0f}km",
                                 fill=MUTED, font=("Consolas", 7))


class App(tk.Tk):
    def __init__(self, tracker: Tracker, synthetic: bool):
        super().__init__()
        self.tracker = tracker
        self.synthetic = synthetic

        self.title("ADS-B Airspace Monitor — passive receive, no transmitter")
        self.configure(bg=BG)
        self.geometry("1420x860")
        self.minsize(1080, 680)

        # --- view state ---
        self.range_km = 100.0
        self.label_all = False
        self.filters = {
            "min_alt": 0.0,
            "max_alt": 60000.0,
            "max_range": 1000.0,
            "search": "",
            "watchlist_only": False,
        }
        self.watchlist: set[str] = set()
        self.sort_key = "range"
        self.sort_desc = False
        self.selected: str | None = None

        # --- MLAT / integrity ---
        # A synthetic MLAT fix is derived from each track so the integrity
        # pipeline is demonstrable without a receiver network. On real hardware
        # this is replaced by mlat.solve_fix() output.
        self.mlat_enabled = synthetic          # only meaningful in the demo
        self.integrity = {}                    # icao -> FusionResult
        self.clock_sigma_ns = 10.0

        # --- recording ---
        self.rec_fh = None
        self.rec_path: Path | None = None
        self.rec_count = 0
        self.tracker.on_raw = self._on_raw

        self._last_refresh = 0.0
        self._build()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(60, self._tick)

    # -- chrome -------------------------------------------------------------
    def _mkbtn(self, parent, text, cmd, kind="normal", width=None):
        c = {"normal": (PANEL2, FG), "accent": ("#0a3d34", ACCENT),
             "warn": ("#3d2f0a", WARN), "danger": ("#3d0a18", DANGER)}[kind]
        return tk.Button(parent, text=text, command=cmd, width=width,
                         bg=c[0], fg=c[1], activebackground=c[0],
                         activeforeground=c[1], relief="flat", bd=0,
                         highlightthickness=1, highlightbackground=BORDER,
                         font=("Segoe UI", 8), padx=7, pady=4, cursor="hand2")

    def _build(self):
        top = tk.Frame(self, bg=BG)
        top.pack(fill="x", padx=12, pady=(9, 5))
        tk.Label(top, text="ADS-B AIRSPACE MONITOR", bg=BG, fg=ACCENT,
                 font=("Segoe UI Semibold", 13)).pack(side="left")
        tk.Label(top, text="   passive 1090 MHz  ·  no transmitter",
                 bg=BG, fg=MUTED, font=("Segoe UI", 9)).pack(side="left")
        self.src_lbl = tk.Label(top, text="", bg=BG, fg=MUTED,
                                font=("Consolas", 9))
        self.src_lbl.pack(side="right")
        if self.synthetic:
            tk.Label(top, text="  SYNTHETIC — NOT REAL AIRSPACE  ",
                     bg="#3a2a55", fg="#d8c4ff",
                     font=("Segoe UI Semibold", 9)).pack(side="right", padx=8)

        body = tk.Frame(self, bg=BG)
        body.pack(fill="both", expand=True, padx=12, pady=(0, 10))

        # ---------- left: scope ----------
        left = tk.Frame(body, bg=BG)
        left.pack(side="left", fill="both", expand=True)
        self.scope = Scope(left, self)
        self.scope.pack(fill="both", expand=True)

        bar = tk.Frame(left, bg=BG)
        bar.pack(fill="x", pady=(6, 0))
        tk.Label(bar, text="range", bg=BG, fg=MUTED,
                 font=("Segoe UI", 8)).pack(side="left", padx=(0, 5))
        for km in (25, 50, 100, 200, 400):
            self._mkbtn(bar, f"{km}", lambda k=km: self._set_range(k),
                        width=4).pack(side="left", padx=1)
        tk.Label(bar, text="   ", bg=BG).pack(side="left")
        self.v_trails = tk.BooleanVar(value=True)
        self.v_labels = tk.BooleanVar(value=False)
        self.v_vectors = tk.BooleanVar(value=True)
        for var, lab, attr in ((self.v_trails, "trails", "show_trails"),
                               (self.v_labels, "all labels", "show_labels"),
                               (self.v_vectors, "vectors", "show_vectors")):
            tk.Checkbutton(bar, text=lab, variable=var, bg=BG, fg=MUTED,
                           selectcolor=PANEL, activebackground=BG,
                           activeforeground=FG, font=("Segoe UI", 8),
                           command=lambda v=var, a=attr: self._toggle(a, v.get())
                           ).pack(side="left", padx=4)

        # ---------- right: panels ----------
        right = tk.Frame(body, bg=BG, width=430)
        right.pack(side="right", fill="y", padx=(12, 0))
        right.pack_propagate(False)

        # reception
        rec = tk.Frame(right, bg=PANEL)
        rec.pack(fill="x", pady=(0, 8))
        tk.Label(rec, text="RECEPTION", bg=PANEL, fg=ACCENT,
                 font=("Segoe UI Semibold", 9)).grid(
                     row=0, column=0, columnspan=4, sticky="w", padx=10, pady=(7, 3))
        self.sv = {}
        fields = [("tracks", "Aircraft"), ("airborne", "Airborne"),
                  ("on_ground", "Ground"), ("frames", "Frames"),
                  ("fps", "Msg/s"), ("max_range_km", "Max range"),
                  ("reception_radius_km", "Horizon"), ("crc_fail", "CRC fail")]
        for i, (k, lab) in enumerate(fields):
            r, c = 1 + i // 2, (i % 2) * 2
            tk.Label(rec, text=lab, bg=PANEL, fg=MUTED,
                     font=("Segoe UI", 8)).grid(row=r, column=c, sticky="w",
                                                padx=(10 if c == 0 else 6, 3), pady=1)
            v = tk.StringVar(value="—")
            self.sv[k] = v
            tk.Label(rec, textvariable=v, bg=PANEL, fg=FG,
                     font=("Consolas", 10, "bold")).grid(
                         row=r, column=c + 1, sticky="w", padx=(0, 10), pady=1)
        tk.Frame(rec, bg=PANEL, height=5).grid(row=1 + len(fields) // 2 + 1,
                                               column=0)

        # filters
        fl = tk.Frame(right, bg=PANEL)
        fl.pack(fill="x", pady=(0, 8))
        tk.Label(fl, text="FILTERS", bg=PANEL, fg=ACCENT,
                 font=("Segoe UI Semibold", 9)).grid(
                     row=0, column=0, columnspan=3, sticky="w", padx=10, pady=(7, 3))

        tk.Label(fl, text="search", bg=PANEL, fg=MUTED,
                 font=("Segoe UI", 8)).grid(row=1, column=0, sticky="w", padx=(10, 4))
        self.search_var = tk.StringVar()
        e = tk.Entry(fl, textvariable=self.search_var, bg=PANEL2, fg=FG,
                     insertbackground=FG, relief="flat", font=("Consolas", 9),
                     highlightthickness=1, highlightbackground=BORDER)
        e.grid(row=1, column=1, columnspan=2, sticky="ew", padx=(0, 10), pady=2)
        self.search_var.trace_add("write", lambda *a: self._sync_filters())
        fl.columnconfigure(1, weight=1)

        tk.Label(fl, text="altitude band", bg=PANEL, fg=MUTED,
                 font=("Segoe UI", 8)).grid(row=2, column=0, sticky="w", padx=(10, 4))
        self.band_var = tk.StringVar(value="all")
        band = ttk.Combobox(fl, textvariable=self.band_var, state="readonly",
                            values=["all"] + [b[0] for b in ALT_BANDS],
                            width=10, font=("Segoe UI", 8))
        band.grid(row=2, column=1, sticky="w", pady=2)
        band.bind("<<ComboboxSelected>>", lambda e: self._sync_filters())

        tk.Label(fl, text="max range", bg=PANEL, fg=MUTED,
                 font=("Segoe UI", 8)).grid(row=3, column=0, sticky="w", padx=(10, 4))
        self.range_filt = tk.DoubleVar(value=1000.0)
        s = tk.Scale(fl, from_=10, to=500, orient="horizontal", variable=self.range_filt,
                     bg=PANEL, fg=FG, troughcolor=PANEL2, highlightthickness=0,
                     showvalue=True, length=170, font=("Consolas", 7),
                     command=lambda v: self._sync_filters())
        s.grid(row=3, column=1, columnspan=2, sticky="ew", padx=(0, 10))

        self.v_mlat = tk.BooleanVar(value=True)
        tk.Checkbutton(fl, text="show MLAT integrity", variable=self.v_mlat,
                       bg=PANEL, fg=MUTED, selectcolor=PANEL2,
                       activebackground=PANEL, activeforeground=FG,
                       font=("Segoe UI", 8)).grid(
                           row=4, column=2, sticky="w", padx=4)

        self.v_watch = tk.BooleanVar(value=False)
        tk.Checkbutton(fl, text="watchlist only", variable=self.v_watch, bg=PANEL,
                       fg=MUTED, selectcolor=PANEL2, activebackground=PANEL,
                       activeforeground=FG, font=("Segoe UI", 8),
                       command=self._sync_filters).grid(
                           row=4, column=0, columnspan=2, sticky="w", padx=10)
        tk.Frame(fl, bg=PANEL, height=6).grid(row=5, column=0)

        # traffic table
        tf = tk.Frame(right, bg=BG)
        tf.pack(fill="both", expand=True)
        hdr = tk.Frame(tf, bg=BG)
        hdr.pack(fill="x")
        tk.Label(hdr, text="TRAFFIC", bg=BG, fg=ACCENT,
                 font=("Segoe UI Semibold", 9)).pack(side="left")
        self.count_lbl = tk.Label(hdr, text="", bg=BG, fg=MUTED,
                                  font=("Consolas", 8))
        self.count_lbl.pack(side="right")

        cols = ("icao", "cs", "range", "alt", "gs", "band", "int")
        self.tree = ttk.Treeview(tf, columns=cols, show="headings", height=11)
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("Treeview", background=PANEL, fieldbackground=PANEL,
                        foreground=FG, font=("Consolas", 8), rowheight=18,
                        borderwidth=0)
        style.configure("Treeview.Heading", background=PANEL2, foreground=MUTED,
                        font=("Segoe UI", 8), relief="flat")
        style.map("Treeview", background=[("selected", "#1c3a44")])
        for c, t, w, anchor in (("icao", "ICAO", 62, "w"), ("cs", "CALLSIGN", 78, "w"),
                                ("range", "RNG km", 58, "e"), ("alt", "ALT ft", 68, "e"),
                                ("gs", "GS kt", 50, "e"), ("band", "BAND", 58, "w"),
                                ("int", "INT", 44, "center")):
            self.tree.heading(c, text=t,
                              command=lambda cc=c: self._sort_by(cc))
            self.tree.column(c, width=w, anchor=anchor, stretch=False)
        self.tree.pack(fill="both", expand=True, pady=(3, 0))
        self.tree.bind("<<TreeviewSelect>>", self._on_tree_select)
        self.tree.bind("<Double-1>", lambda e: self._toggle_watch_selected())

        # position sources / integrity
        pf = tk.Frame(right, bg=PANEL)
        pf.pack(fill="x", pady=(0, 6))
        tk.Label(pf, text="POSITION INTEGRITY", bg=PANEL, fg=ACCENT,
                 font=("Segoe UI Semibold", 9)).grid(
                     row=0, column=0, columnspan=2, sticky="w", padx=10, pady=(6, 2))
        self.pv = {}
        for i, (k, lab) in enumerate([
            ("mlat_on", "MLAT enabled"), ("agree", "ADS-B / MLAT agree"),
            ("mlat_only", "MLAT only"), ("suspect", "Suspect"),
            ("divergent", "Divergent"),
        ], start=1):
            tk.Label(pf, text=lab, bg=PANEL, fg=MUTED,
                     font=("Segoe UI", 8)).grid(row=i, column=0, sticky="w",
                                                padx=(10, 4), pady=0)
            v = tk.StringVar(value="—")
            self.pv[k] = v
            tk.Label(pf, textvariable=v, bg=PANEL, fg=FG,
                     font=("Consolas", 9, "bold")).grid(
                         row=i, column=1, sticky="e", padx=(4, 10), pady=0)
        tk.Frame(pf, bg=PANEL, height=4).grid(row=6, column=0)

        # detail
        self.detail = tk.Label(right, text="select an aircraft", bg=PANEL,
                               fg=MUTED, font=("Consolas", 8), justify="left",
                               anchor="w", wraplength=410, padx=8, pady=6)
        self.detail.pack(fill="x", pady=(6, 6))

        # actions
        act = tk.Frame(right, bg=BG)
        act.pack(fill="x")
        self.rec_btn = self._mkbtn(act, "● REC", self._toggle_record, "warn")
        self.rec_btn.pack(side="left", padx=(0, 4))
        self._mkbtn(act, "Export CSV", self._export_csv).pack(side="left", padx=2)
        self._mkbtn(act, "Export JSON", self._export_json).pack(side="left", padx=2)
        self._mkbtn(act, "★ Watch", self._toggle_watch_selected).pack(side="left", padx=2)

        self._set_source_label()

    def _set_source_label(self):
        t = self.tracker
        if isinstance(t, SyntheticTracker):
            self.src_lbl.config(text="source: synthetic generator")
        else:
            self.src_lbl.config(text=f"source: {t.source.name}")

    # -- helpers ------------------------------------------------------------
    def _set_range(self, km):
        self.range_km = float(km)
        self.scope.redraw()

    def _toggle(self, attr, val):
        setattr(self.scope, attr, val)
        self.scope.redraw()

    def _sync_filters(self):
        self.filters["search"] = self.search_var.get().strip().upper()
        self.filters["watchlist_only"] = self.v_watch.get()
        self.filters["max_range"] = self.range_filt.get()
        band = self.band_var.get()
        if band == "all":
            self.filters["min_alt"], self.filters["max_alt"] = 0.0, 60000.0
        else:
            for name, lo, hi, _ in ALT_BANDS:
                if name == band:
                    self.filters["min_alt"], self.filters["max_alt"] = float(lo), float(hi)
        self.scope.redraw()

    def _sort_by(self, key):
        if self.sort_key == key:
            self.sort_desc = not self.sort_desc
        else:
            self.sort_key, self.sort_desc = key, False

    def visible_tracks(self):
        """Tracks passing the current filters. Single source of truth for both
        the scope and the table, so they can never disagree."""
        f = self.filters
        out = []
        for t in self.tracker.snapshot():
            if not (f["min_alt"] <= t.altitude_ft < f["max_alt"]):
                continue
            d = t.distance_km(self.tracker.ref_lat, self.tracker.ref_lon)
            if d > f["max_range"]:
                continue
            if f["search"]:
                hay = f"{t.icao} {t.callsign}".upper()
                if f["search"] not in hay:
                    continue
            if f["watchlist_only"] and t.icao not in self.watchlist:
                continue
            out.append(t)
        return out

    def select_icao(self, icao):
        self.selected = icao
        for iid in self.tree.selection():
            self.tree.selection_remove(iid)
        for iid in self.tree.get_children():
            if self.tree.item(iid, "values")[0] == icao:
                self.tree.selection_set(iid)
                self.tree.see(iid)
                break

    def _on_tree_select(self, _ev):
        sel = self.tree.selection()
        if sel:
            icao = self.tree.item(sel[0], "values")[0]
            self.selected = icao
            self.scope.selected = icao
            self.scope.redraw()

    def _toggle_watch_selected(self):
        if self.selected:
            if self.selected in self.watchlist:
                self.watchlist.discard(self.selected)
            else:
                self.watchlist.add(self.selected)

    # -- recording ----------------------------------------------------------
    def _on_raw(self, hexstr: str):
        if self.rec_fh is not None:
            self.rec_fh.write(hexstr + "\n")
            self.rec_count += 1

    def _toggle_record(self):
        if self.rec_fh is None:
            path = filedialog.asksaveasfilename(
                title="Record raw ADS-B frames",
                defaultextension=".txt",
                initialfile=f"adsb_{time.strftime('%Y%m%d_%H%M%S')}.txt",
                filetypes=[("Frame capture", "*.txt"), ("All files", "*.*")])
            if not path:
                return
            self.rec_fh = open(path, "w", encoding="utf-8")
            self.rec_path = Path(path)
            self.rec_count = 0
            self.rec_btn.config(text="■ STOP", bg="#5a0a1c", fg="#ffdde4")
        else:
            self.rec_fh.close()
            self.rec_fh = None
            self.rec_btn.config(text="● REC", bg="#3d2f0a", fg=WARN)
            messagebox.showinfo(
                "Recording stopped",
                f"{self.rec_count:,} frames written to\n{self.rec_path}\n\n"
                "Replay with:\n  python desktop.py --file "
                f"{self.rec_path.name}")

    # -- export -------------------------------------------------------------
    def _export_rows(self):
        rows = []
        for t in self.visible_tracks():
            rows.append({
                "icao": t.icao, "callsign": t.callsign,
                "lat": round(t.lat, 6), "lon": round(t.lon, 6),
                "altitude_ft": round(t.altitude_ft, 1),
                "ground_speed_kt": round(t.ground_speed_kt, 1),
                "track_deg": round(t.track_deg, 1),
                "vertical_rate_fpm": round(t.vertical_rate_fpm, 1),
                "range_km": round(t.distance_km(self.tracker.ref_lat,
                                               self.tracker.ref_lon), 2),
                "bearing_deg": round(t.bearing_from(self.tracker.ref_lat,
                                                    self.tracker.ref_lon), 1),
                "band": band_name(t.altitude_ft),
                "source": t.source, "messages": t.messages,
            })
        return rows

    def _export_csv(self):
        rows = self._export_rows()
        if not rows:
            messagebox.showinfo("Export", "No tracks match the current filters.")
            return
        path = filedialog.asksaveasfilename(defaultextension=".csv",
                                            initialfile="adsb_traffic.csv",
                                            filetypes=[("CSV", "*.csv")])
        if not path:
            return
        with open(path, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)
        messagebox.showinfo("Export", f"{len(rows)} tracks written to\n{path}")

    def _export_json(self):
        rows = self._export_rows()
        if not rows:
            messagebox.showinfo("Export", "No tracks match the current filters.")
            return
        path = filedialog.asksaveasfilename(defaultextension=".json",
                                            initialfile="adsb_traffic.json",
                                            filetypes=[("JSON", "*.json")])
        if not path:
            return
        payload = {
            "receiver": {"lat": self.tracker.ref_lat, "lon": self.tracker.ref_lon},
            "exported": time.time(),
            "source": self.tracker.source.name,
            "synthetic": self.synthetic,
            "stats": self.tracker.stats(),
            "tracks": rows,
        }
        Path(path).write_text(json.dumps(payload, indent=2), encoding="utf-8")
        messagebox.showinfo("Export", f"{len(rows)} tracks written to\n{path}")

    # -- loop ---------------------------------------------------------------
    def _tick(self):
        now = time.time()
        if now - self._last_refresh > 0.5:
            self._last_refresh = now
            self._refresh()
        self.after(60, self._tick)

    def _refresh(self):
        st = self.tracker.stats()
        for k, var in self.sv.items():
            v = st.get(k)
            if v is None:
                var.set("—")
            elif k in ("max_range_km", "reception_radius_km"):
                var.set(f"{v:.0f} km")
            else:
                var.set(f"{v:.0f}" if isinstance(v, float) else str(v))

        self._update_integrity()

        tracks = self.visible_tracks()
        total = len(self.tracker.snapshot())
        self.count_lbl.config(text=f"{len(tracks)}/{total} shown"
                                   + (f"   ★{len(self.watchlist)}" if self.watchlist else ""))

        key = self.sort_key
        if key == "range":
            tracks.sort(key=lambda t: t.distance_km(self.tracker.ref_lat,
                                                    self.tracker.ref_lon),
                        reverse=self.sort_desc)
        elif key == "alt":
            tracks.sort(key=lambda t: t.altitude_ft, reverse=self.sort_desc)
        elif key == "gs":
            tracks.sort(key=lambda t: t.ground_speed_kt, reverse=self.sort_desc)
        elif key == "cs":
            tracks.sort(key=lambda t: t.callsign, reverse=self.sort_desc)
        elif key == "band":
            tracks.sort(key=lambda t: t.altitude_ft, reverse=self.sort_desc)
        elif key == "int":
            tracks.sort(key=lambda t: self.integrity.get(t.icao).status.value
                        if t.icao in self.integrity else "", reverse=self.sort_desc)
        else:
            tracks.sort(key=lambda t: t.icao, reverse=self.sort_desc)

        sel = self.selected
        self.tree.delete(*self.tree.get_children())
        for t in tracks:
            d = t.distance_km(self.tracker.ref_lat, self.tracker.ref_lon)
            star = "★" if t.icao in self.watchlist else " "
            fr = self.integrity.get(t.icao)
            flag = ""
            if fr is not None and self.mlat_enabled:
                flag = {"agree": "ok", "mlat_only": "mlat", "suspect": "SUS",
                        "divergent": "DIV", "adsb_only": "adsb"}[fr.status.value]
            self.tree.insert("", "end", values=(
                t.icao, f"{star}{t.callsign or '—'}", f"{d:.0f}",
                f"{t.altitude_ft:,.0f}", f"{t.ground_speed_kt:.0f}",
                band_name(t.altitude_ft), flag))
        if sel:
            self.select_icao(sel)

        if sel and sel in self.tracker.tracks:
            t = self.tracker.tracks[sel]
            d = t.distance_km(self.tracker.ref_lat, self.tracker.ref_lon)
            brg = t.bearing_from(self.tracker.ref_lat, self.tracker.ref_lon)
            vs = t.vertical_rate_fpm
            trend = "climbing" if vs > 64 else ("descending" if vs < -64 else "level")

            # Position-integrity lines for the selected aircraft.
            integ = ""
            fr = self.integrity.get(t.icao)
            if fr is not None and self.mlat_enabled:
                integ = (f"\nintegrity {fr.status.value.upper()}  "
                         f"src={fr.chosen}  sigma={fr.sigma_m:.0f}m")
                if fr.disagreement_m > 0:
                    integ += (f"\ndisagree {fr.disagreement_m:.0f}m "
                              f"({fr.sigma_ratio:.1f} sigma)")
                for n in fr.notes[:2]:
                    integ += f"\n  - {n}"

            self.detail.config(
                text=f"ICAO {t.icao}    {t.callsign or '(no callsign)'}"
                     f"{'   ★ WATCHED' if t.icao in self.watchlist else ''}\n"
                     f"position  {t.lat:.5f}, {t.lon:.5f}\n"
                     f"altitude  {t.altitude_ft:,.0f} ft   {trend} {vs:+.0f} fpm\n"
                     f"motion    {t.ground_speed_kt:.0f} kt on {t.track_deg:.0f}°\n"
                     f"from RX   {d:.1f} km at {brg:.0f}°\n"
                     f"horizon   {t.max_range_km():.0f} km (max possible at this alt)\n"
                     f"messages  {t.messages}    source {t.source}"
                     + integ,
                fg=FG)
        elif sel:
            self.detail.config(text=f"{sel} no longer tracked", fg=MUTED)
        else:
            self.detail.config(text="select an aircraft on the scope or in the "
                                    "table", fg=MUTED)

        # Position-integrity summary panel.
        ic = getattr(self, "_integrity_counts", {})
        self.pv["mlat_on"].set("yes" if self.mlat_enabled else "no")
        for key in ("agree", "mlat_only", "suspect", "divergent"):
            n = ic.get(key, 0)
            self.pv[key].set(str(n) if self.mlat_enabled else "—")

        self.scope.redraw()

    def _update_integrity(self):
        """Fuse ADS-B and MLAT positions for every track, flagging disagreement.

        With no MLAT network the tracker supplies only ADS-B. To make the
        integrity path demonstrable the demo mode synthesises an MLAT fix per
        track with a realistic GDOP; on hardware this comes from mlat.solve_fix.
        """
        import random
        results = {}
        counts = {"agree": 0, "mlat_only": 0, "suspect": 0, "divergent": 0,
                  "adsb_only": 0}
        for t in self.tracker.snapshot():
            adsb = PositionSource("adsb", t.lat, t.lon, t.altitude_ft * 0.3048,
                                  5.0, timestamp=t.last_seen)
            mlat = None
            if self.mlat_enabled and t.source == "synthetic":
                # deterministic per-aircraft pseudo-MLAT so the picture is
                # stable across refreshes rather than flickering
                rnd = random.Random(hash(t.icao) & 0xFFFF)
                gdop = rnd.choice([1.2, 2.0, 3.5, 6.0, 15.0, 80.0])
                sigma = mlat_sigma_from_gdop(gdop, self.clock_sigma_ns)
                # most aircraft honest; a couple offset to show the flag
                offset_m = 0.0
                if rnd.random() < 0.12:
                    offset_m = rnd.choice([200.0, 1500.0, 8000.0])
                dlat = offset_m / 111000.0
                mlat = PositionSource("mlat", t.lat + dlat, t.lon,
                                      t.altitude_ft * 0.3048, sigma,
                                      timestamp=t.last_seen, gdop=gdop)
            r = fuse(t.icao, adsb, mlat)
            if r is None:
                continue
            results[t.icao] = r
            counts[r.status.value] = counts.get(r.status.value, 0) + 1
        self.integrity = results
        self._integrity_counts = counts

    def _on_close(self):
        if self.rec_fh:
            self.rec_fh.close()
        self.tracker.stop()
        self.destroy()


def main():
    ap = argparse.ArgumentParser(description="ADS-B airspace monitor (desktop)")
    ap.add_argument("--lat", type=float, default=0.0)
    ap.add_argument("--lon", type=float, default=0.0)
    ap.add_argument("--synthetic", action="store_true", default=False)
    ap.add_argument("--file")
    ap.add_argument("--rtl", action="store_true")
    ap.add_argument("--rtl-tool", default="rtl_adsb")
    args = ap.parse_args()

    synthetic = False
    if args.file:
        tracker = Tracker(FileSource(args.file, realtime=True), args.lat, args.lon)
    elif args.rtl:
        tracker = Tracker(RTLSDBSource(args.rtl_tool), args.lat, args.lon)
    else:
        tracker = SyntheticTracker(args.lat, args.lon, n=18)
        synthetic = True

    tracker.start()
    App(tracker, synthetic).mainloop()


if __name__ == "__main__":
    main()
