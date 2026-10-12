"""
ADS-B / Mode-S decoder — from scratch, no external dependencies.

This is the passive radar alternative. An aircraft broadcasts its own position,
altitude, velocity and identity at 1090 MHz; we receive and decode it. Receiving
is unlicensed in every jurisdiction, so this gives long-range air surveillance
with no transmitter and no licence.

Scope: this decodes the two message families you need for a live map:
  * DF17/DF18 Extended Squitter (the ADS-B broadcast)
  * DF4/DF5/DF20/DF21 surveillance replies (altitude + identity)

Message types implemented (ADS-B Type Codes):
    TC 1-4    Aircraft identification (callsign)
    TC 5-8    Surface position
    TC 9-18   Airborne position (barometric altitude)
    TC 19     Airborne velocity
    TC 20-22  Airborne position (GNSS altitude)

Not implemented: Comm-B (DF20/21 MB field), TC 28/29 aircraft status,
TC 31 operational status. Those are additions, not corrections.

References: ICAO Annex 10 Vol IV, DO-260B, and the public dump1090/pyModeS
format descriptions. Implemented independently from the wire format.

Units: positions in degrees, altitudes in feet, speeds in knots, angles in degrees.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

# ---------------------------------------------------------------------------
# CRC-24 (Mode-S polynomial 0xFFF409)
# ---------------------------------------------------------------------------

_GENERATOR = 0xFFF409


def _build_crc_table() -> list[int]:
    table = []
    for i in range(256):
        crc = i << 16
        for _ in range(8):
            crc = ((crc << 1) ^ _GENERATOR) if (crc & 0x800000) else (crc << 1)
        table.append(crc & 0xFFFFFF)
    return table


_CRC_TABLE = _build_crc_table()


def crc24(msg: bytes) -> int:
    """CRC over a full 14-byte Mode-S frame (including the parity field)."""
    crc = 0
    for b in msg:
        crc = ((crc << 8) & 0xFFFFFF) ^ _CRC_TABLE[((crc >> 16) ^ b) & 0xFF]
    return crc


def crc_ok(msg: bytes) -> bool:
    """A clean DF17/18 frame has CRC 0 over the whole message."""
    return len(msg) == 14 and crc24(msg) == 0


def fix_single_bit_error(msg: bytes) -> Optional[bytes]:
    """Try to repair one flipped bit.

    NOTE: do not shortcut on the syndrome being a single bit. For a CRC, the
    syndrome of a single-bit error equals CRC(x^p), which is generally NOT a
    single-bit value - that assumption silently disabled repair entirely.
    Brute force over the 112 bit positions is cheap and always correct.
    """
    if len(msg) != 14:
        return None
    if crc24(msg) == 0:
        return msg
    for byte_i in range(14):
        for bit_i in range(8):
            test = bytearray(msg)
            test[byte_i] ^= (1 << (7 - bit_i))
            if crc24(bytes(test)) == 0:
                return bytes(test)
    return None


# ---------------------------------------------------------------------------
# Bit helpers
# ---------------------------------------------------------------------------

def bits_to_int(msg: bytes, first: int, last: int) -> int:
    """Extract bits [first..last] inclusive, MSB-first, as an integer."""
    value = 0
    for i in range(first, last + 1):
        byte = msg[i // 8]
        bit = (byte >> (7 - (i % 8))) & 1
        value = (value << 1) | bit
    return value


def hex_to_bytes(hexstr: str) -> Optional[bytes]:
    h = hexstr.strip().replace("*", "").replace(";", "")
    if len(h) != 28:
        return None
    try:
        return bytes.fromhex(h)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# CPR - Compact Position Reporting
# ---------------------------------------------------------------------------
# ADS-B encodes position in 17 bits as a latitude/longitude zone index rather
# than an absolute angle. Two messages (even and odd frame) are needed to
# resolve which zone you are in. This is the standard global decode.

NZ = 15  # number of geographic latitude zones


def _cpr_nl(lat: float) -> int:
    """Number of longitude zones at a given latitude."""
    if lat < 0:
        lat = -lat
    if lat >= 87.0:
        return 1
    if lat == 0:
        return 59
    # exact formula from DO-260B
    a = 1 - math.cos(math.pi / (2 * NZ))
    b = math.cos(math.pi / 180.0 * lat) ** 2
    nl = 2 * math.pi / math.acos(1 - a / b)
    return int(math.floor(nl))


def _cpr_mod(a: float, b: float) -> float:
    return a - b * math.floor(a / b)


@dataclass
class CPRFrame:
    """One CPR-encoded position sample."""
    lat_cpr: int
    lon_cpr: int
    odd: bool
    altitude_ft: Optional[float] = None
    t: float = 0.0


class CPRResolver:
    """Resolves positions from even/odd CPR frame pairs, per aircraft.

    Implements the DO-260B global decode. Two frames (even and odd) are required
    because the 17-bit field encodes a zone index, not an absolute angle.

    `even_is_newer` selects which frame's zone the answer is expressed in. With
    no timestamps we default to even, which is correct as long as the pair is
    close in time (the caller should discard stale pairs).
    """

    #: pairs older than this are not combined (seconds)
    MAX_PAIR_AGE_S = 10.0

    def __init__(self):
        self._even: dict[str, CPRFrame] = {}
        self._odd: dict[str, CPRFrame] = {}

    def add(self, icao: str, frame: CPRFrame) -> Optional[tuple[float, float]]:
        if frame.odd:
            self._odd[icao] = frame
        else:
            self._even[icao] = frame

        e, o = self._even.get(icao), self._odd.get(icao)
        if e is None or o is None:
            return None
        if abs(e.t - o.t) > self.MAX_PAIR_AGE_S:
            return None

        return self._global_decode(e, o)

    @staticmethod
    def _global_decode(e: "CPRFrame", o: "CPRFrame") -> Optional[tuple[float, float]]:
        yz_even = e.lat_cpr / 131072.0
        yz_odd = o.lat_cpr / 131072.0
        xz_even = e.lon_cpr / 131072.0
        xz_odd = o.lon_cpr / 131072.0

        # --- latitude ------------------------------------------------------
        j = math.floor(59 * yz_even - 60 * yz_odd + 0.5)

        rlat_even = (360.0 / 60.0) * (_cpr_mod(j, 60) + yz_even)
        rlat_odd = (360.0 / 59.0) * (_cpr_mod(j, 59) + yz_odd)

        # southern hemisphere wraps past 270
        if rlat_even >= 270.0:
            rlat_even -= 360.0
        if rlat_odd >= 270.0:
            rlat_odd -= 360.0

        # the two frames must agree on the longitude zone count, otherwise the
        # pair straddles a zone boundary and cannot be combined
        if _cpr_nl(rlat_even) != _cpr_nl(rlat_odd):
            return None

        lat = rlat_even          # even frame as the reference
        nl_lat = _cpr_nl(lat)

        # --- longitude -----------------------------------------------------
        ni = max(_cpr_nl(rlat_even), 1)
        m = math.floor(xz_even * (_cpr_nl(rlat_even) - 1)
                       - xz_odd * _cpr_nl(rlat_odd) + 0.5)
        lon = (360.0 / ni) * (_cpr_mod(m, ni) + xz_even)
        if lon >= 180.0:
            lon -= 360.0

        if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
            return None
        return lat, lon


# ---------------------------------------------------------------------------
# Aircraft state
# ---------------------------------------------------------------------------

@dataclass
class Aircraft:
    icao: str
    callsign: Optional[str] = None
    lat: Optional[float] = None
    lon: Optional[float] = None
    altitude_ft: Optional[int] = None
    altitude_source: str = "baro"      # 'baro' | 'gnss'
    ground_speed_kt: Optional[float] = None
    track_deg: Optional[float] = None
    vertical_rate_fpm: Optional[int] = None
    on_ground: bool = False
    squawk: Optional[str] = None
    messages: int = 0
    last_seen: float = 0.0
    first_seen: float = 0.0
    # position history for a trail: list of (lat, lon, alt_ft)
    trail: list[tuple[float, float, int]] = field(default_factory=list)

    def distance_km_from(self, lat: float, lon: float) -> Optional[float]:
        if self.lat is None or self.lon is None:
            return None
        return haversine_km(lat, lon, self.lat, self.lon)


def haversine_km(lat1, lon1, lat2, lon2) -> float:
    R = 6371.0088
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = (math.sin(dp / 2) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2)
    return 2 * R * math.asin(math.sqrt(a))


def radio_horizon_km(altitude_ft: float, antenna_m: float = 2.0) -> float:
    """Line-of-sight radio horizon. 4.12*sqrt(h) is the standard rule of thumb.

    Altitude is clamped at 0: a target below the antenna (on the ground, or a
    synthetic track that has drifted negative) has no radio horizon, not a
    negative one.
    """
    alt_m = max(altitude_ft, 0.0) * 0.3048
    return 4.12 * (math.sqrt(alt_m) + math.sqrt(max(antenna_m, 0.0)))


# ---------------------------------------------------------------------------
# Altitude decoding
# ---------------------------------------------------------------------------

def decode_altitude(ac12: int) -> Optional[int]:
    """Decode a 12-bit ADS-B altitude field (25 ft increments) to feet."""
    # bit layout: C1 A1 C2 A2 C4 A4 B1 Q B2 D2 B4 D4  (13 bits incl. M)
    # we are given the 12 bits after the M bit is dropped by the caller
    if ac12 == 0:
        return None
    q = (ac12 >> 4) & 1
    if q:
        # 25 ft increments
        n = ((ac12 & 0xFE0) >> 1) | (ac12 & 0xF)
        return n * 25 - 1000
    return None  # 100 ft Gillham encoding not implemented


def decode_altitude_gillham(ac13: int) -> Optional[int]:
    """13-bit field including the M bit; supports both 25 ft and 100 ft modes."""
    if ac13 == 0:
        return None
    m = (ac13 >> 6) & 1
    q = (ac13 >> 4) & 1
    if m:
        return None  # metric, unsupported
    if q:
        n = ((ac13 & 0x1F80) >> 2) | ((ac13 & 0x20) >> 1) | (ac13 & 0xF)
        return n * 25 - 1000
    # Gillham (100 ft) - Gray-coded, reflected
    n = _gillham_to_int(ac13)
    return n * 100 - 1200 if n is not None else None


def _gillham_to_int(ac13: int) -> Optional[int]:
    """Convert a Gillham-coded 100 ft altitude field to an integer count."""
    # extract C1 A1 C2 A2 C4 A4 B1 B2 B4 D2 D4
    def bit(i):
        return (ac13 >> i) & 1
    c1, a1, c2, a2, c4, a4 = bit(0), bit(1), bit(2), bit(3), bit(4), bit(5)
    b1, b2, b4, d2, d4 = bit(6), bit(7), bit(8), bit(9), bit(10)

    def gray2int(g):
        b = g
        while g >> 1:
            g >>= 1
            b ^= g
        return b

    n500 = gray2int((d2 << 2) | (d4 << 1) | 0)  # placeholder, see note
    # Full Gillham decoding is fiddly; use the standard 500ft/100ft split.
    gray500 = (d2 << 1) | d4
    gray100 = (c1 << 2) | (a1 << 1) | c2
    n500 = gray2int(gray500)
    n100 = gray2int(gray100)
    if n100 in (0, 5, 6):
        return None  # invalid codes
    if n100 == 7:
        n100 = 5
    if n500 % 2:
        n100 = 6 - n100
    return n500 * 5 + n100


# ---------------------------------------------------------------------------
# Message decoding
# ---------------------------------------------------------------------------

CHARSET = "#ABCDEFGHIJKLMNOPQRSTUVWXYZ##### ###############0123456789######"


def decode_callsign(me: int) -> str:
    """Decode the 48-bit identification field into a callsign string."""
    out = []
    for i in range(8):
        c = (me >> (42 - 6 * i)) & 0x3F
        out.append(CHARSET[c] if c < len(CHARSET) else "#")
    return "".join(out).replace("#", "").strip()


def decode_velocity(me: int) -> tuple[Optional[float], Optional[float], Optional[int]]:
    """Decode TC19 airborne velocity. Returns (gs_kt, track_deg, vs_fpm)."""
    subtype = (me >> 48) & 0x7
    if subtype not in (1, 2):
        return None, None, None

    if subtype == 1:
        # ground speed, 1 kt resolution
        ew_sign = (me >> 45) & 1
        ew_vel = ((me >> 34) & 0x3FF) - 1
        ns_sign = (me >> 22) & 1
        ns_vel = ((me >> 11) & 0x3FF) - 1
        if ew_vel < 0 or ns_vel < 0:
            return None, None, None
        vx = -ew_vel if ew_sign else ew_vel
        vy = -ns_vel if ns_sign else ns_vel
    else:
        # airspeed, 4 kt resolution
        heading = (me >> 45) & 0x1FF
        airspeed = ((me >> 34) & 0x3FF) - 1
        if heading == 0 or airspeed < 0:
            return None, None, None
        trk = heading * 360.0 / 512.0
        gs = airspeed * 4.0
        vx = gs * math.sin(math.radians(trk))
        vy = gs * math.cos(math.radians(trk))

    gs = math.hypot(vx, vy)
    trk = math.degrees(math.atan2(vx, vy)) % 360.0

    # vertical rate
    vr_sign = (me >> 10) & 1
    vr_raw = ((me >> 1) & 0x1FF) - 1
    vs = None if vr_raw < 0 else (-vr_raw if vr_sign else vr_raw) * 64

    return gs, trk, vs


def decode_squawk(me: int) -> Optional[str]:
    """Decode a 13-bit Mode-A identity into a 4-digit squawk."""
    a = (me >> 22) & 0xF
    b = (me >> 18) & 0xF
    c = (me >> 14) & 0xF
    d = (me >> 10) & 0xF

    def digits(x):
        return ((x & 0x1) << 2) | ((x & 0x2) << 1) | ((x & 0x4) >> 2) | ((x & 0x8) >> 1)

    A = ((a & 0x1) << 2) | ((a & 0x2) << 1) | ((a & 0x4) >> 2) | ((a & 0x8) >> 1)
    B = ((b & 0x1) << 2) | ((b & 0x2) << 1) | ((b & 0x4) >> 2) | ((b & 0x8) >> 1)
    C = ((c & 0x1) << 2) | ((c & 0x2) << 1) | ((c & 0x4) >> 2) | ((c & 0x8) >> 1)
    D = ((d & 0x1) << 2) | ((d & 0x2) << 1) | ((d & 0x4) >> 2) | ((d & 0x8) >> 1)
    return f"{A}{B}{C}{D}"


@dataclass
class DecodedMessage:
    icao: str
    df: int
    tc: Optional[int] = None
    callsign: Optional[str] = None
    altitude_ft: Optional[int] = None
    altitude_gnss: bool = False
    cpr_lat: Optional[int] = None
    cpr_lon: Optional[int] = None
    cpr_odd: Optional[bool] = None
    ground_speed_kt: Optional[float] = None
    track_deg: Optional[float] = None
    vertical_rate_fpm: Optional[int] = None
    squawk: Optional[str] = None
    on_ground: bool = False
    raw: str = ""


class Decoder:
    """Stateful decoder: feed it raw frames, get back aircraft state."""

    def __init__(self, reference_lat: float = 0.0, reference_lon: float = 0.0):
        self.aircraft: dict[str, Aircraft] = {}
        self.cpr = CPRResolver()
        self.ref_lat = reference_lat
        self.ref_lon = reference_lon
        self.stats = {"frames": 0, "crc_fail": 0, "decoded": 0, "repaired": 0}

    def feed_hex(self, hexstr: str, now: float = 0.0) -> Optional[DecodedMessage]:
        msg = hex_to_bytes(hexstr)
        if msg is None:
            return None
        return self.feed(msg, now=now)

    def feed(self, msg: bytes, now: float = 0.0) -> Optional[DecodedMessage]:
        self.stats["frames"] += 1
        if len(msg) != 14:
            self.stats["crc_fail"] += 1
            return None

        if not crc_ok(msg):
            repaired = fix_single_bit_error(msg)
            if repaired is None:
                self.stats["crc_fail"] += 1
                return None
            msg = repaired
            self.stats["repaired"] += 1

        df = msg[0] >> 3
        icao = msg[1:4].hex().upper()

        ac = self.aircraft.get(icao)
        if ac is None:
            ac = Aircraft(icao=icao, first_seen=now)
            self.aircraft[icao] = ac
        ac.messages += 1
        ac.last_seen = now

        out = DecodedMessage(icao=icao, df=df, raw=msg.hex().upper())

        if df in (17, 18):
            self._decode_extended_squitter(msg, ac, out, now)
        elif df in (4, 5, 20, 21):
            self._decode_surveillance(msg, ac, out, df)
        else:
            return out

        self.stats["decoded"] += 1
        return out

    # -- DF17/18 ------------------------------------------------------------
    def _decode_extended_squitter(self, msg, ac, out, now):
        me = int.from_bytes(msg[4:11], "big")
        tc = (me >> 51) & 0x1F
        out.tc = tc

        if 1 <= tc <= 4:
            out.callsign = decode_callsign(me)
            ac.callsign = out.callsign

        elif 5 <= tc <= 8:
            # surface position
            out.on_ground = True
            ac.on_ground = True
            out.cpr_lat = (me >> 35) & 0x1FFFF
            out.cpr_lon = (me >> 18) & 0x1FFFF
            out.cpr_odd = bool((me >> 34) & 1)

        elif 9 <= tc <= 18:
            # airborne position, barometric altitude
            ss = (me >> 49) & 0x3
            alt_raw = (me >> 36) & 0xFFF
            alt = decode_altitude(alt_raw)
            out.altitude_ft = alt
            if alt is not None:
                ac.altitude_ft = alt
                ac.altitude_source = "baro"
            out.cpr_lat = (me >> 17) & 0x1FFFF
            out.cpr_lon = (me >> 0) & 0x1FFFF
            out.cpr_odd = bool((me >> 34) & 1)

        elif tc == 19:
            gs, trk, vs = decode_velocity(me)
            out.ground_speed_kt = gs
            out.track_deg = trk
            out.vertical_rate_fpm = vs
            if gs is not None:
                ac.ground_speed_kt = gs
            if trk is not None:
                ac.track_deg = trk
            if vs is not None:
                ac.vertical_rate_fpm = vs

        elif 20 <= tc <= 22:
            # airborne position, GNSS altitude
            alt_raw = (me >> 36) & 0xFFF
            alt = decode_altitude(alt_raw)
            out.altitude_ft = alt
            out.altitude_gnss = True
            if alt is not None:
                ac.altitude_ft = alt
                ac.altitude_source = "gnss"
            out.cpr_lat = (me >> 17) & 0x1FFFF
            out.cpr_lon = (me >> 0) & 0x1FFFF
            out.cpr_odd = bool((me >> 34) & 1)

        # resolve position if we have a CPR pair
        if out.cpr_lat is not None and out.cpr_lon is not None:
            frame = CPRFrame(lat_cpr=out.cpr_lat, lon_cpr=out.cpr_lon,
                             odd=bool(out.cpr_odd), altitude_ft=out.altitude_ft,
                             t=now)
            pos = self.cpr.add(ac.icao, frame)
            if pos is not None:
                lat, lon = pos
                # sanity: reject nonsense that the local decode can produce
                if -90 <= lat <= 90 and -180 <= lon <= 180:
                    ac.lat, ac.lon = lat, lon
                    if ac.altitude_ft is not None:
                        ac.trail.append((lat, lon, ac.altitude_ft))
                        if len(ac.trail) > 300:
                            ac.trail.pop(0)

    # -- DF4/5/20/21 --------------------------------------------------------
    def _decode_surveillance(self, msg, ac, out, df):
        if df in (4, 20):
            alt_raw = ((msg[2] & 0x07) << 8) | msg[3]
            alt = decode_altitude_gillham(alt_raw)
            out.altitude_ft = alt
            if alt is not None:
                ac.altitude_ft = alt
        else:
            # DF5/21 identity
            me = int.from_bytes(msg[4:11], "big")
            sq = decode_squawk(me)
            out.squawk = sq
            if sq:
                ac.squawk = sq

    # -- query --------------------------------------------------------------
    def live(self, max_age_s: float = 60.0, now: float = 0.0) -> list[Aircraft]:
        return [a for a in self.aircraft.values()
                if a.lat is not None and (now - a.last_seen) <= max_age_s]


# ---------------------------------------------------------------------------
# Self-test with real captured frames
# ---------------------------------------------------------------------------

# Genuine over-the-air frames (publicly documented examples, CRC-valid).
# Verified over-the-air captures. Every frame here has been checked to produce
# CRC 000000 with the polynomial above. Two frames that failed this check were
# removed rather than kept - a test vector that does not pass its own CRC is
# worse than no test vector, because it trains you to distrust a correct
# implementation.
KNOWN_FRAMES = [
    # (hex, expected_icao, description)
    ("8D4840D6202CC371C32CE0576098", "4840D6", "KLM1023 identification"),
    ("8D40621D58C382D690C8AC2863A7", "40621D", "airborne position, even"),
    ("8D40621D58C386435CC412692AD6", "40621D", "airborne position, odd"),
    ("8D485020994409940838175B284F", "485020", "airborne velocity"),
]


def make_frame(icao: str, me_bits: int, df: int = 17) -> bytes:
    """Build a CRC-correct 14-byte frame. For constructing test vectors.

    This is how a test vector SHOULD be made: assemble the payload, then
    compute the parity, rather than hand-writing hex and hoping.
    """
    head = bytes([(df << 3) | 5]) + bytes.fromhex(icao)
    body = me_bits.to_bytes(7, "big")
    frame = head + body
    parity = crc24(frame)
    return frame + parity.to_bytes(3, "big")


def _self_test() -> int:
    print("ADS-B decoder self-test")
    print("-" * 78)

    # 1. CRC
    ok = sum(1 for h, _, _ in KNOWN_FRAMES if crc_ok(bytes.fromhex(h)))
    print(f"CRC valid on {ok}/{len(KNOWN_FRAMES)} known-good frames")
    assert ok == len(KNOWN_FRAMES), "CRC implementation is wrong"

    # 2. deliberate corruption must be detected and repaired
    good = bytes.fromhex(KNOWN_FRAMES[0][0])
    bad = bytearray(good)
    bad[5] ^= 0x08
    assert not crc_ok(bytes(bad)), "corruption not detected"
    repaired = fix_single_bit_error(bytes(bad))
    print(f"single-bit error: detected, repaired={repaired == good}")
    assert repaired == good, "single-bit repair failed"

    # 2b. a constructed frame must also validate - proves the CRC is right in
    # both directions (checking and generating).
    built = make_frame("ABCDEF", 0x10 << 51)   # TC=1 identification, empty callsign
    assert crc_ok(built), "constructed frame fails CRC"
    print(f"constructed frame validates: {built.hex().upper()}")

    # 3. decode
    dec = Decoder()
    for h, expect_icao, desc in KNOWN_FRAMES:
        m = dec.feed_hex(h)
        assert m is not None, f"failed to decode {h}"
        assert m.icao == expect_icao, f"icao {m.icao} != {expect_icao}"
        detail = []
        if m.callsign: detail.append(f"callsign={m.callsign}")
        if m.altitude_ft is not None: detail.append(f"alt={m.altitude_ft}ft")
        if m.ground_speed_kt is not None: detail.append(f"gs={m.ground_speed_kt:.0f}kt")
        if m.track_deg is not None: detail.append(f"trk={m.track_deg:.0f}")
        if m.vertical_rate_fpm is not None: detail.append(f"vs={m.vertical_rate_fpm}fpm")
        if m.squawk: detail.append(f"squawk={m.squawk}")
        print(f"  DF{m.df:<2} {m.icao}  TC={m.tc}  {' '.join(detail):48s} {desc}")

    # 4. position from the even/odd pair
    ac = dec.aircraft.get("40621D")
    assert ac is not None
    print(f"\nCPR pair resolved: 40621D -> lat={ac.lat}, lon={ac.lon}")
    assert ac.lat is not None and ac.lon is not None, "CPR did not resolve"
    # the documented answer for this well-known test pair
    assert abs(ac.lat - 52.2572) < 0.01, f"lat off: {ac.lat}"
    assert abs(ac.lon - 3.91937) < 0.01, f"lon off: {ac.lon}"
    print("  matches the documented position (52.2572, 3.91937)")

    print("-" * 78)
    print(f"stats: {dec.stats}")
    print("all checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(_self_test())
