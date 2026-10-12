"""
Laser Control Module with Safety Interlocks.

Controls a laser module via GPIO. Implements multiple safety
interlocks that must all pass before the laser can fire:
  - Manual arming switch
  - Active mode required
  - Confirmed track required
  - Target within firing zone
  - Per-target cooldown
  - Max continuous fire time
  - Firing time window
  - Max fires per session
  - Emergency stop
  - Minimum time between shots
"""

import time
import threading
from typing import Optional, Tuple
from datetime import datetime
from abc import ABC, abstractmethod


class LaserBackend(ABC):
    """Abstract base for laser GPIO backends."""

    @abstractmethod
    def set_laser(self, on: bool) -> None:
        """Turn laser on or off."""
        pass

    @abstractmethod
    def set_laser_power(self, power_percent: float) -> None:
        """Set laser power level (0-100)."""
        pass

    @abstractmethod
    def cleanup(self) -> None:
        """Release resources."""
        pass


class MockLaserBackend(LaserBackend):
    """Mock backend for development."""

    def __init__(self):
        self._state = False
        self._power = 0.0

    def set_laser(self, on: bool) -> None:
        self._state = on

    def set_laser_power(self, power_percent: float) -> None:
        self._power = power_percent

    def cleanup(self) -> None:
        self._state = False

    @property
    def state(self) -> bool:
        return self._state

    @property
    def power(self) -> float:
        return self._power


class RPiGPIOLaserBackend(LaserBackend):
    """RPi.GPIO-based laser control."""

    def __init__(self, pin: int):
        import RPi.GPIO as GPIO
        self._GPIO = GPIO
        self._pin = pin
        GPIO.setmode(GPIO.BCM)
        GPIO.setup(pin, GPIO.OUT)
        GPIO.output(pin, GPIO.LOW)
        self._pwm = None

    def set_laser(self, on: bool) -> None:
        self._GPIO.output(self._pin, self._GPIO.HIGH if on else self._GPIO.LOW)

    def set_laser_power(self, power_percent: float) -> None:
        if self._pwm is None:
            self._pwm = self._GPIO.PWM(self._pin, 1000)  # 1kHz
            self._pwm.start(0)
        duty = max(0.0, min(100.0, power_percent))
        self._pwm.ChangeDutyCycle(duty)

    def cleanup(self) -> None:
        self.set_laser(False)
        if self._pwm:
            self._pwm.stop()
        self._GPIO.cleanup()


class SafetyInterlocks:
    """Manages all safety interlock checks."""

    def __init__(self, config):
        self.config = config

        self._manual_arm = config.get("laser.safety_interlocks.manual_arm", True)
        self._require_active = config.get("laser.safety_interlocks.require_active_mode", True)
        self._require_confirmed = config.get("laser.safety_interlocks.require_confirmed_track", True)
        self._firing_zone_radius = config.get("laser.safety_interlocks.firing_zone_radius", 80)
        self._cooldown_per_target_ms = config.get("laser.safety_interlocks.cooldown_per_target_ms", 500)
        self._max_continuous_ms = config.get("laser.safety_interlocks.max_continuous_fire_ms", 200)
        self._max_fires = config.get("laser.safety_interlocks.max_fires_per_session", 100)
        self._min_between_shots_ms = config.get("laser.safety_interlocks.min_time_between_shots_ms", 100)
        self._time_window = config.get("laser.safety_interlocks.firing_time_window", None)

        # Species gate (L3). This is the wingbeat check: the photodiode channel
        # measures the insect's wingbeat frequency and confirms it is a biting
        # female mosquito before the beam is allowed on.
        #
        # Defaults to True = REQUIRED, because a gate that defaults to off is
        # not a safety gate. Set `require_wingbeat: false` only for bench
        # testing without the photodiode channel fitted.
        self._require_wingbeat = config.get(
            "laser.safety_interlocks.require_wingbeat", True)
        self._min_wingbeat_confidence = config.get(
            "laser.safety_interlocks.min_wingbeat_confidence", 0.35)

        # Runtime state
        self._emergency_stop = False
        self._is_active = False
        self._session_fire_count = 0
        self._last_shot_time = 0.0
        self._continuous_start = 0.0
        self._is_firing = False

        # Species evidence supplied by the photodiode channel. Set by
        # set_wingbeat_evidence() from the main loop each frame.
        self._wingbeat_ok = False
        self._wingbeat_confidence = 0.0
        self._wingbeat_freq_hz = 0.0
        self._wingbeat_reason = "no wingbeat evidence yet"
        self._wingbeat_age_s = float("inf")

        # Per-target last fire times
        self._target_fire_times: dict = {}

    def set_wingbeat_evidence(self, ok: bool, confidence: float = 0.0,
                              freq_hz: float = 0.0, reason: str = "") -> None:
        """Record the photodiode channel's verdict for the current frame.

        Called by the main loop with the result of
        detection/wingbeat.analyse_burst() on the latest photodiode burst.

        `ok` must mean "every candidate is a biting female" - i.e. the
        classifier could not resolve the reading to anything other than a
        biting female. An ambiguous reading (e.g. 600 Hz, which is both
        Aedes female and Anopheles male) is NOT ok and must be passed as False.
        """
        self._wingbeat_ok = bool(ok)
        self._wingbeat_confidence = float(confidence)
        self._wingbeat_freq_hz = float(freq_hz)
        self._wingbeat_reason = reason or ("wingbeat confirmed" if ok else "not confirmed")
        self._wingbeat_age_s = 0.0

    def _age_wingbeat_evidence(self, dt: float) -> None:
        """Expire wingbeat evidence so a stale reading cannot authorise a shot.

        Evidence is only valid for a short window: the insect that produced it
        may have left the curtain and a different one (or a hand) entered.
        """
        self._wingbeat_age_s += dt
        max_age = self.config.get(
            "laser.safety_interlocks.wingbeat_evidence_max_age_s", 0.5)
        if self._wingbeat_age_s > max_age:
            self._wingbeat_ok = False

    def arm(self) -> None:
        """Arm the laser manually."""
        self._manual_arm = True

    def disarm(self) -> None:
        """Disarm the laser manually."""
        self._manual_arm = False
        self._is_firing = False

    def set_active(self, active: bool) -> None:
        """Set active mode (system is actively hunting)."""
        self._is_active = active
        if not active:
            self._is_firing = False

    def trigger_emergency_stop(self) -> None:
        """Emergency stop — immediately kill laser."""
        self._emergency_stop = True
        self._is_firing = False

    def reset_emergency_stop(self) -> None:
        """Reset emergency stop condition."""
        self._emergency_stop = False

    def reset_session(self) -> None:
        """Reset per-session counters."""
        self._session_fire_count = 0
        self._target_fire_times.clear()

    def check_all(self, track, target_x: float, target_y: float,
                  frame_center_x: float, frame_center_y: float) -> Tuple[bool, str]:
        """Check all safety interlocks.

        Args:
            track: The track being targeted (or None)
            target_x: Target X position
            target_y: Target Y position
            frame_center_x: Center X of frame
            frame_center_y: Center Y of frame

        Returns:
            (can_fire, reason) — can_fire is True if all checks pass.
        """
        # 1. Emergency stop
        if self._emergency_stop:
            return False, "EMERGENCY_STOP"

        # 2. Manual arming
        if not self._manual_arm:
            return False, "NOT_ARMED"

        # 3. Active mode
        if self._require_active and not self._is_active:
            return False, "NOT_ACTIVE"

        # 4. Confirmed track
        if self._require_confirmed and (track is None or not track.confirmed):
            return False, "NO_CONFIRMED_TRACK"

        # 5. Species / wingbeat (L3). Only fires at what the photodiode channel
        #    has positively identified as a biting female mosquito.
        #
        #    This is the gate that stops the system firing at a male, a
        #    non-biting species, or a housefly that slipped the size filter.
        #    It is placed AFTER track confirmation so we do not waste the check
        #    on a track we would reject anyway, and BEFORE geometry so a
        #    species-negative never counts as a fire attempt.
        if self._require_wingbeat:
            if not self._wingbeat_ok:
                return False, f"NO_WINGBEAT_CONFIRM ({self._wingbeat_reason})"
            if self._wingbeat_confidence < self._min_wingbeat_confidence:
                return False, (
                    f"WINGBEAT_LOW_CONFIDENCE "
                    f"({self._wingbeat_confidence:.2f} < {self._min_wingbeat_confidence:.2f})"
                )

        # 6. Firing zone
        dx = target_x - frame_center_x
        dy = target_y - frame_center_y
        distance = (dx ** 2 + dy ** 2) ** 0.5
        if distance > self._firing_zone_radius:
            return False, "OUTSIDE_FIRING_ZONE"

        # 7. Per-target cooldown
        if track is not None:
            track_id = track.track_id
            last_fire = self._target_fire_times.get(track_id, 0.0)
            elapsed = (time.time() - last_fire) * 1000.0
            if elapsed < self._cooldown_per_target_ms:
                return False, f"TARGET_COOLDOWN ({int(elapsed)}ms)"

        # 8. Max fires per session
        if self._session_fire_count >= self._max_fires:
            return False, "SESSION_LIMIT_REACHED"

        # 9. Time window
        if self._time_window is not None:
            if not self._in_time_window():
                return False, "OUTSIDE_TIME_WINDOW"

        # 10. Min time between shots
        elapsed_since_last = (time.time() - self._last_shot_time) * 1000.0
        if elapsed_since_last < self._min_between_shots_ms:
            return False, f"SHOT_COOLDOWN ({int(elapsed_since_last)}ms)"

        return True, "OK"

    def record_fire(self, track_id: int) -> None:
        """Record that a shot was fired.

        Args:
            track_id: ID of the target that was fired upon.
        """
        now = time.time()
        self._session_fire_count += 1
        self._last_shot_time = now
        self._target_fire_times[track_id] = now
        self._continuous_start = now
        self._is_firing = True

    def record_stop(self) -> None:
        """Record that firing has stopped."""
        self._is_firing = False

    def check_continuous_timeout(self) -> bool:
        """Check if continuous firing has exceeded max duration.

        Returns:
            True if firing should be stopped.
        """
        if not self._is_firing:
            return False
        elapsed = (time.time() - self._continuous_start) * 1000.0
        return elapsed > self._max_continuous_ms

    def _in_time_window(self) -> bool:
        """Check if current time is within firing window."""
        if self._time_window is None:
            return True
        try:
            now = datetime.now().time()
            start_str, end_str = self._time_window.split("-")
            start = datetime.strptime(start_str.strip(), "%H:%M").time()
            end = datetime.strptime(end_str.strip(), "%H:%M").time()
            if start < end:
                return start <= now <= end
            else:
                # Window crosses midnight
                return now >= start or now <= end
        except (ValueError, AttributeError):
            return True

    def get_status(self) -> dict:
        """Return safety system status."""
        return {
            "manual_arm": self._manual_arm,
            "active_mode": self._is_active,
            "emergency_stop": self._emergency_stop,
            "is_firing": self._is_firing,
            "session_fires": self._session_fire_count,
            "max_fires": self._max_fires,
            "cooldown_ms": self._cooldown_per_target_ms,
            "continuous_limit_ms": self._max_continuous_ms,
            "require_wingbeat": self._require_wingbeat,
            "wingbeat_ok": self._wingbeat_ok,
            "wingbeat_freq_hz": self._wingbeat_freq_hz,
            "wingbeat_confidence": self._wingbeat_confidence,
            "wingbeat_reason": self._wingbeat_reason,
            "wingbeat_age_s": (None if self._wingbeat_age_s == float("inf")
                               else round(self._wingbeat_age_s, 3)),
        }


class LaserController:
    """High-level laser controller with safety interlocks.

    Combines the GPIO backend with the safety interlock system
    to provide a safe firing interface.
    """

    def __init__(self, config):
        self.config = config
        self._pin = config.get("laser.laser_pin", 26)
        self._mock = config.get("laser.mock_laser", False)
        self._default_power = config.get("laser.default_power", "medium")
        self._power_levels = config.get("laser.power_levels", {})

        # Safety interlocks
        self._safety = SafetyInterlocks(config)

        # Backend
        self._backend = self._create_backend()

        # State
        self._is_firing = False

    def _create_backend(self) -> LaserBackend:
        """Create the appropriate backend."""
        if self._mock:
            return MockLaserBackend()

        try:
            return RPiGPIOLaserBackend(self._pin)
        except Exception:
            print("WARNING: RPi.GPIO not available, using mock laser")
            return MockLaserBackend()

    def try_fire(self, track, target_x: float, target_y: float,
                 frame_center_x: float, frame_center_y: float,
                 power: Optional[str] = None) -> Tuple[bool, str]:
        """Attempt to fire the laser at a target.

        Args:
            track: The target track
            target_x: Target X position
            target_y: Target Y position
            frame_center_x: Center X of frame
            frame_center_y: Center Y of frame
            power: Power level ("low", "medium", "high")

        Returns:
            (fired, reason) — fired is True if the laser was activated.
        """
        can_fire, reason = self._safety.check_all(
            track, target_x, target_y,
            frame_center_x, frame_center_y
        )

        if not can_fire:
            return False, reason

        # Get power level
        if power is None:
            power = self._default_power
        power_pct = self._power_levels.get(power, 60.0)

        # Fire!
        self._backend.set_laser_power(power_pct)
        self._backend.set_laser(True)
        self._is_firing = True
        self._safety.record_fire(track.track_id if track else -1)

        return True, "FIRED"

    def stop(self) -> None:
        """Stop firing immediately."""
        self._backend.set_laser(False)
        self._is_firing = False
        self._safety.record_stop()

    def emergency_stop(self) -> None:
        """Emergency stop — kill laser and require manual reset."""
        self.stop()
        self._safety.trigger_emergency_stop()

    @property
    def is_firing(self) -> bool:
        return self._is_firing

    @property
    def safety(self) -> SafetyInterlocks:
        return self._safety

    def cleanup(self) -> None:
        """Stop laser and release resources."""
        self.stop()
        self._backend.cleanup()
