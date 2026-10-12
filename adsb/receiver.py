"""
ADS-B receiver front-end.

Reads 1090 MHz frames from whatever source you have, decodes them with the
from-scratch decoder, and maintains live aircraft state. Receiving ADS-B is
unlicensed in every jurisdiction - this is a passive listener, there is no
transmitter anywhere in this file.

Sources:
  * ``RTLSDBSource``      - RTL-SDR dongle via rtl_adsb or dump1090 (real hardware)
  * ``FileSource``        - newline-delimited hex frames from a file
  * ``SyntheticSource``   - generates plausible traffic so the map works with no
                            hardware. Clearly labelled as synthetic in the UI.

The hardware path is 2 lines of shell: install rtl-sdr, run `rtl_adsb`, pipe the
hex into this. See adsb/README.md for the parts list.
"""

from __future__ import annotations

import math
import queue
import random
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator, Optional

from decoder import Aircraft, Decoder, haversine_km, radio_horizon_km


# ---------------------------------------------------------------------------
# Sources
# ---------------------------------------------------------------------------

class Source:
    """Base class: yields raw hex frames as strings."""

    name = "source"

    def frames(self) -> Iterator[str]:
        raise NotImplementedError

    def close(self) -> None:
        pass


class FileSource(Source):
    """Replay newline-delimited hex frames (one per line).

    This is also the offline path: capture with
    `rtl_adsb | tee capture.txt` then replay it here as often as you like.
    """

    def __init__(self, path: str, realtime: bool = False, speed: float = 1.0):
        self.path = path
        self.realtime = realtime
        self.speed = speed
        self.name = f"file:{Path(path).name}"

    def frames(self) -> Iterator[str]:
        with open(self.path, "r", encoding="utf-8", errors="ignore") as fh:
            for line in fh:
                line = line.strip()
                if line and len(line) >= 28:
                    yield line[:28]
                    if self.realtime:
                        time.sleep(0.002 / max(self.speed, 0.01))


class RTLSDBSource(Source):
    """Live RTL-SDR via rtl_adsb (part of rtl-sdr) or dump1090.

    Requires:
        rtl-sdr installed, dongle present
        Linux/macOS:  sudo apt install rtl-sdr
        Windows:      install the rtl-sdr drivers (Zadig) then rtl_adsb.exe

    Neither rtl_adsb nor dump1090 *transmit* - they only demodulate 1090 MHz.
    """

    def __init__(self, tool: str = "rtl_adsb", args: Optional[list[str]] = None):
        self.tool = tool
        self.args = args or []
        self.name = f"rtl-sdr:{tool}"
        self._proc: Optional[subprocess.Popen] = None

    def frames(self) -> Iterator[str]:
        cmd = [self.tool] + self.args
        try:
            self._proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                text=True, bufsize=1,
            )
        except FileNotFoundError:
            raise RuntimeError(
                f"{self.tool} not found. Install rtl-sdr, or use --file / --synthetic."
            )
        assert self._proc.stdout is not None
        for line in self._proc.stdout:
            line = line.strip().lstrip("*;")
            if len(line) >= 28:
                yield line[:28]

    def close(self) -> None:
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()


class SyntheticSource(Source):
    """Generates plausible traffic so the whole pipeline runs with no hardware.

    Deliberately marked synthetic everywhere it surfaces. It is for developing
    the map and the pipeline, never for drawing conclusions about real airspace.
    """

    name = "synthetic"

    def __init__(self, ref_lat: float, ref_lon: float, n: int = 14, seed: int = 5):
        self.ref_lat, self.ref_lon = ref_lat, ref_lon
        self.n = n
        self.rng = random.Random(seed)
        self.t0 = time.time()
        self.planes = []
        types = ["B738", "A320", "B77W", "A21N", "E190", "B788", "A359", "AT72"]
        for i in range(n):
            brg = self.rng.uniform(0, 360)
            dist = self.rng.uniform(20, 260)
            hdg = (brg + 180 + self.rng.uniform(-35, 35)) % 360
            self.planes.append({
                "icao": f"{self.rng.randrange(0x100000, 0xFFFFFF):06X}",
                "cs": f"{self.rng.choice(['RAM','RYR','AFR','DLH','BAW','THY','UAE','IBE'])}"
                      f"{self.rng.randrange(100, 9999)}",
                "lat": self.ref_lat + (dist / 111.0) * math.cos(math.radians(brg)),
                "lon": self.ref_lon + (dist / (111.0 * max(math.cos(math.radians(self.ref_lat)), .2)))
                                       * math.sin(math.radians(brg)),
                "alt": self.rng.choice([0, 1200, 3500, 8000, 10500, 11200, 11900]) + self.rng.uniform(-200, 200),
                "hdg": hdg,
                "spd": self.rng.uniform(180, 480),
                "type": self.rng.choice(types),
            })

    def frames(self) -> Iterator[str]:
        # The synthetic source bypasses the wire format: it publishes aircraft
        # state directly, because fabricating valid hex frames would be
        # pretending to a fidelity this does not have.
        raise NotImplementedError(
            "SyntheticSource drives the tracker directly; use SyntheticTracker."
        )


# ---------------------------------------------------------------------------
# Tracker
# ---------------------------------------------------------------------------

@dataclass
class Track:
    """A live aircraft, from either real frames or the synthetic source."""

    icao: str
    callsign: str = ""
    lat: float = 0.0
    lon: float = 0.0
    altitude_ft: float = 0.0
    ground_speed_kt: float = 0.0
    track_deg: float = 0.0
    vertical_rate_fpm: float = 0.0
    aircraft_type: str = ""
    source: str = "adsb"          # 'adsb' | 'synthetic'
    last_seen: float = 0.0
    first_seen: float = 0.0
    messages: int = 0
    trail: Optional[list[tuple[float, float]]] = None

    def __post_init__(self):
        if self.trail is None:
            self.trail = []

    @property
    def on_ground(self) -> bool:
        return self.altitude_ft < 100

    def distance_km(self, lat, lon) -> float:
        return haversine_km(lat, lon, self.lat, self.lon)

    def bearing_from(self, lat, lon) -> float:
        p1, p2 = math.radians(lat), math.radians(self.lat)
        dl = math.radians(self.lon - lon)
        y = math.sin(dl) * math.cos(p2)
        x = (math.cos(p1) * math.sin(p2)
             - math.sin(p1) * math.cos(p2) * math.cos(dl))
        return (math.degrees(math.atan2(y, x)) + 360) % 360

    def max_range_km(self) -> float:
        """How far this aircraft could be heard from, given its altitude."""
        return radio_horizon_km(self.altitude_ft)


class Tracker:
    """Maintains live aircraft state from a frame source, on a worker thread."""

    def __init__(self, source: Source, ref_lat: float, ref_lon: float,
                 stale_s: float = 60.0, on_raw=None):
        self.source = source
        self.ref_lat, self.ref_lon = ref_lat, ref_lon
        self.stale_s = stale_s
        #: optional callback(hexstr) invoked for every raw frame - lets the app
        #: record a capture for later replay without touching the source.
        self.on_raw = on_raw
        self.decoder = Decoder(ref_lat, ref_lon)
        self.tracks: dict[str, Track] = {}
        self.lock = threading.Lock()
        self.running = False
        self._thread: Optional[threading.Thread] = None
        self.frame_count = 0
        self.start_time = 0.0
        self.max_seen_km = 0.0

    # -- lifecycle ----------------------------------------------------------
    def start(self):
        if self.running:
            return
        self.running = True
        self.start_time = time.time()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self.running = False
        self.source.close()
        if self._thread:
            self._thread.join(timeout=1.5)

    def _run(self):
        try:
            for hexstr in self.source.frames():
                if not self.running:
                    break
                self._ingest(hexstr)
        except Exception as exc:                      # keep the UI alive
            print(f"[tracker] source error: {exc}")

    # -- ingest -------------------------------------------------------------
    def _ingest(self, hexstr: str):
        if self.on_raw is not None:
            self.on_raw(hexstr)
        now = time.time()
        msg = self.decoder.feed_hex(hexstr, now=now)
        if msg is None:
            return
        self.frame_count += 1

        with self.lock:
            ac = self.decoder.aircraft.get(msg.icao)
            if ac is None or ac.lat is None or ac.lon is None:
                return
            tr = self.tracks.get(msg.icao)
            if tr is None:
                tr = Track(icao=msg.icao, source="adsb", first_seen=now)
                self.tracks[msg.icao] = tr
            tr.lat, tr.lon = ac.lat, ac.lon
            tr.altitude_ft = float(ac.altitude_ft or 0)
            if ac.callsign:
                tr.callsign = ac.callsign
            if ac.ground_speed_kt is not None:
                tr.ground_speed_kt = ac.ground_speed_kt
            if ac.track_deg is not None:
                tr.track_deg = ac.track_deg
            if ac.vertical_rate_fpm is not None:
                tr.vertical_rate_fpm = float(ac.vertical_rate_fpm)
            tr.last_seen = now
            tr.messages = ac.messages

            d = tr.distance_km(self.ref_lat, self.ref_lon)
            self.max_seen_km = max(self.max_seen_km, d)

            tr.trail.append((tr.lat, tr.lon))
            if len(tr.trail) > 400:
                tr.trail.pop(0)

            self._expire(now)

    def _expire(self, now: float):
        dead = [k for k, t in self.tracks.items() if now - t.last_seen > self.stale_s]
        for k in dead:
            del self.tracks[k]

    # -- query --------------------------------------------------------------
    def snapshot(self) -> list[Track]:
        with self.lock:
            return list(self.tracks.values())

    def stats(self) -> dict:
        now = time.time()
        with self.lock:
            live = list(self.tracks.values())
        elapsed = max(now - self.start_time, 0.001)
        return {
            "tracks": len(live),
            "frames": self.frame_count,
            "fps": self.frame_count / elapsed,
            "max_range_km": self.max_seen_km,
            "elapsed_s": elapsed,
            "source": self.source.name,
            "crc_fail": self.decoder.stats["crc_fail"],
            "repaired": self.decoder.stats["repaired"],
            "reception_radius_km": max(
                (t.max_range_km() for t in live), default=0.0),
            "airborne": sum(1 for t in live if t.altitude_ft >= 500),
            "on_ground": sum(1 for t in live if t.altitude_ft < 500),
        }


class SyntheticTracker(Tracker):
    """Tracker fed by the synthetic generator instead of real frames.

    Overrides the ingest path entirely so nothing here can be mistaken for a
    real decode. Tracks carry source='synthetic' and the UI labels them.
    """

    def __init__(self, ref_lat, ref_lon, n=14, seed=5):
        super().__init__(SyntheticSource(ref_lat, ref_lon, n, seed),
                         ref_lat, ref_lon)
        self.gen = SyntheticSource(ref_lat, ref_lon, n, seed)

    def start(self):
        if self.running:
            return
        self.running = True
        self.start_time = time.time()
        self._thread = threading.Thread(target=self._run_synthetic, daemon=True)
        self._thread.start()

    def _run_synthetic(self):
        dt = 0.1
        while self.running:
            now = time.time()
            with self.lock:
                for p in self.gen.planes:
                    # advance along heading
                    dist_km = p["spd"] * 1.852 * dt / 3600.0
                    p["lat"] += (dist_km / 111.0) * math.cos(math.radians(p["hdg"]))
                    p["lon"] += (dist_km / (111.0 * max(
                        math.cos(math.radians(p["lat"])), 0.2))) * math.sin(
                            math.radians(p["hdg"]))

                    tr = self.tracks.get(p["icao"])
                    if tr is None:
                        tr = Track(icao=p["icao"], source="synthetic",
                                   first_seen=now)
                        self.tracks[p["icao"]] = tr
                    tr.callsign = p["cs"]
                    tr.lat, tr.lon = p["lat"], p["lon"]
                    tr.altitude_ft = p["alt"]
                    tr.ground_speed_kt = p["spd"]
                    tr.track_deg = p["hdg"]
                    tr.aircraft_type = p["type"]
                    tr.last_seen = now
                    tr.messages += 1
                    d = tr.distance_km(self.ref_lat, self.ref_lon)
                    self.max_seen_km = max(self.max_seen_km, d)
                    tr.trail.append((tr.lat, tr.lon))
                    if len(tr.trail) > 400:
                        tr.trail.pop(0)
                    self.frame_count += 1
            time.sleep(dt)


if __name__ == "__main__":
    # quick self-check: synthetic tracker should populate and move
    st = SyntheticTracker(33.5731, -7.5898, n=6)
    st.start()
    time.sleep(1.5)
    snap = st.snapshot()
    print(f"synthetic tracks: {len(snap)}")
    for t in snap[:3]:
        print(f"  {t.icao} {t.callsign:8s} {t.altitude_ft:7.0f}ft "
              f"{t.distance_km(33.5731, -7.5898):6.1f}km hdg={t.track_deg:5.1f}")
    print("stats:", {k: (round(v, 1) if isinstance(v, float) else v)
                     for k, v in st.stats().items()})
    st.stop()
    print("ok")
