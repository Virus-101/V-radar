"""
Pan-Tilt Servo Control Module.

Controls pan and tilt servos to aim at targets. Supports multiple
PWM backends (pigpio for hardware PWM, RPi.GPIO for software PWM,
or a mock backend for development). Converts pixel offsets to angle
commands and applies smoothing to prevent jerky movement.
"""

import time
import math
from typing import Optional, Tuple
from abc import ABC, abstractmethod


class PWMBackend(ABC):
    """Abstract base for PWM backends."""

    @abstractmethod
    def set_servo_pulse(self, pin: int, pulse_us: int) -> None:
        """Set servo pulse width in microseconds.

        Args:
            pin: GPIO pin number (BCM)
            pulse_us: Pulse width in microseconds (500-2500 typical)
        """
        pass

    @abstractmethod
    def cleanup(self) -> None:
        """Release resources."""
        pass


class MockPWMBackend(PWMBackend):
    """Mock backend for development — prints commands only."""

    def __init__(self):
        self._last_pulse = {}

    def set_servo_pulse(self, pin: int, pulse_us: int) -> None:
        self._last_pulse[pin] = pulse_us

    def cleanup(self) -> None:
        self._last_pulse.clear()

    def get_last_pulse(self, pin: int) -> Optional[int]:
        return self._last_pulse.get(pin)


class RPiGPIOBackend(PWMBackend):
    """Software PWM using RPi.GPIO."""

    def __init__(self):
        import RPi.GPIO as GPIO
        self._GPIO = GPIO
        self._GPIO.setmode(GPIO.BCM)
        self._GPIO.setwarnings(False)
        self._pwm = {}

    def setup_pin(self, pin: int, frequency: int = 50) -> None:
        """Set up a pin for PWM output."""
        self._GPIO.setup(pin, self._GPIO.OUT)
        self._pwm[pin] = self._GPIO.PWM(pin, frequency)
        self._pwm[pin].start(0)

    def set_servo_pulse(self, pin: int, pulse_us: int) -> None:
        if pin not in self._pwm:
            return
        # Convert pulse width to duty cycle
        # 50 Hz = 20ms period
        duty = (pulse_us / 20000.0) * 100.0
        self._pwm[pin].ChangeDutyCycle(duty)

    def cleanup(self) -> None:
        for pwm in self._pwm.values():
            pwm.stop()
        self._GPIO.cleanup()


class PigpioBackend(PWMBackend):
    """Hardware PWM using pigpio (better accuracy, no jitter)."""

    def __init__(self):
        import pigpio
        self._pi = pigpio.pi()
        if not self._pi.connected:
            raise RuntimeError("Failed to connect to pigpio daemon")

    def set_servo_pulse(self, pin: int, pulse_us: int) -> None:
        self._pi.set_servo_pulsewidth(pin, pulse_us)

    def cleanup(self) -> None:
        # Set pins to neutral then release
        self._pi.stop()


class PanTiltController:
    """Pan-tilt servo controller for aiming at targets.

    Converts pixel coordinates to servo angles, applies smoothing
    and rate limiting, and maps angles to PWM pulse widths.
    """

    def __init__(self, config):
        self.config = config

        # GPIO pins
        self._pan_pin = config.get("pan_tilt.pan_pin", 18)
        self._tilt_pin = config.get("pan_tilt.tilt_pin", 19)

        # Pulse width mapping
        self._pan_min_us = config.get("pan_tilt.pan_min_us", 500)
        self._pan_max_us = config.get("pan_tilt.pan_max_us", 2500)
        self._tilt_min_us = config.get("pan_tilt.tilt_min_us", 500)
        self._tilt_max_us = config.get("pan_tilt.tilt_max_us", 2500)

        # Angle limits
        self._pan_min = config.get("pan_tilt.pan_min_angle", -90)
        self._pan_max = config.get("pan_tilt.pan_max_angle", 90)
        self._tilt_min = config.get("pan_tilt.tilt_min_angle", -45)
        self._tilt_max = config.get("pan_tilt.tilt_max_angle", 45)

        # Home position
        self._pan_home = config.get("pan_tilt.pan_home", 0)
        self._tilt_home = config.get("pan_tilt.tilt_home", 0)

        # Smoothing
        self._max_step = config.get("pan_tilt.max_step_per_frame", 5)
        self._alpha = config.get("pan_tilt.smoothing_alpha", 0.3)

        # Pixel-to-angle conversion
        self._ppd_x = config.get("pan_tilt.pixels_per_degree_x", 10.0)
        self._ppd_y = config.get("pan_tilt.pixels_per_degree_y", 10.0)
        self._center_x = config.get("pan_tilt.x_center", 320)
        self._center_y = config.get("pan_tilt.y_center", 240)

        # Dead zone
        self._dead_x = config.get("pan_tilt.dead_zone_x", 15)
        self._dead_y = config.get("pan_tilt.dead_zone_y", 15)

        # Current position (smoothed)
        self._current_pan = self._pan_home
        self._current_tilt = self._tilt_home

        # Target position
        self._target_pan = self._pan_home
        self._target_tilt = self._tilt_home

        # PWM backend
        self._mock = config.get("pan_tilt.mock_servos", False)
        self._backend = self._create_backend()

        # Initialize to home
        self._apply_position()
        time.sleep(0.5)

    def _create_backend(self) -> PWMBackend:
        """Create the appropriate PWM backend."""
        backend_name = self.config.get("pan_tilt.pwm_backend", "mock")

        if self._mock or backend_name == "mock":
            return MockPWMBackend()
        elif backend_name == "pigpio":
            try:
                return PigpioBackend()
            except Exception:
                print("WARNING: pigpio not available, falling back to mock")
                return MockPWMBackend()
        elif backend_name == "rpi.gpio":
            try:
                backend = RPiGPIOBackend()
                backend.setup_pin(self._pan_pin)
                backend.setup_pin(self._tilt_pin)
                return backend
            except Exception:
                print("WARNING: RPi.GPIO not available, falling back to mock")
                return MockPWMBackend()
        else:
            return MockPWMBackend()

    def aim_at(self, pixel_x: float, pixel_y: float) -> None:
        """Aim the servos at a pixel coordinate.

        Args:
            pixel_x: Target X in image coordinates
            pixel_y: Target Y in image coordinates
        """
        # Compute offset from center
        dx = pixel_x - self._center_x
        dy = pixel_y - self._center_y

        # Apply dead zone
        if abs(dx) < self._dead_x:
            dx = 0
        if abs(dy) < self._dead_y:
            dy = 0

        # Convert pixel offset to angle offset
        angle_offset_pan = -dx / self._ppd_x  # Negative: moving right = pan left
        angle_offset_tilt = dy / self._ppd_y   # Positive: moving down = tilt down

        # Compute target angles
        self._target_pan = self._current_pan + angle_offset_pan
        self._target_tilt = self._current_tilt + angle_offset_tilt

        # Clamp to limits
        self._target_pan = max(self._pan_min, min(self._pan_max, self._target_pan))
        self._target_tilt = max(self._tilt_min, min(self._tilt_max, self._target_tilt))

        self._update_position()

    def center(self) -> None:
        """Return to home position."""
        self._target_pan = self._pan_home
        self._target_tilt = self._tilt_home
        self._update_position()

    def _update_position(self) -> None:
        """Apply smoothing and rate limiting, then output to servos."""
        # Compute desired delta
        pan_delta = self._target_pan - self._current_pan
        tilt_delta = self._target_tilt - self._current_tilt

        # Rate limit
        if abs(pan_delta) > self._max_step:
            pan_delta = math.copysign(self._max_step, pan_delta)
        if abs(tilt_delta) > self._max_step:
            tilt_delta = math.copysign(self._max_step, tilt_delta)

        # EMA smoothing
        self._current_pan += self._alpha * pan_delta
        self._current_tilt += self._alpha * tilt_delta

        # Clamp
        self._current_pan = max(self._pan_min, min(self._pan_max, self._current_pan))
        self._current_tilt = max(self._tilt_min, min(self._tilt_max, self._current_tilt))

        self._apply_position()

    def _apply_position(self) -> None:
        """Convert current angles to PWM and send to servos."""
        pan_pulse = self._angle_to_pulse(
            self._current_pan,
            self._pan_min, self._pan_max,
            self._pan_min_us, self._pan_max_us
        )
        tilt_pulse = self._angle_to_pulse(
            self._current_tilt,
            self._tilt_min, self._tilt_max,
            self._tilt_min_us, self._tilt_max_us
        )

        self._backend.set_servo_pulse(self._pan_pin, pan_pulse)
        self._backend.set_servo_pulse(self._tilt_pin, tilt_pulse)

    def _angle_to_pulse(self, angle: float,
                        angle_min: float, angle_max: float,
                        pulse_min: int, pulse_max: int) -> int:
        """Linear mapping from angle to pulse width."""
        if angle_max == angle_min:
            return (pulse_min + pulse_max) // 2
        ratio = (angle - angle_min) / (angle_max - angle_min)
        ratio = max(0.0, min(1.0, ratio))
        return int(pulse_min + ratio * (pulse_max - pulse_min))

    def get_position(self) -> Tuple[float, float]:
        """Return current (pan, tilt) angles in degrees."""
        return self._current_pan, self._current_tilt

    def get_status(self) -> dict:
        """Return status dict for the web dashboard."""
        return {
            "pan_angle": round(self._current_pan, 2),
            "tilt_angle": round(self._current_tilt, 2),
            "pan_target": round(self._target_pan, 2),
            "tilt_target": round(self._target_tilt, 2),
            "backend": type(self._backend).__name__,
            "mock": self._mock,
        }

    def cleanup(self) -> None:
        """Release GPIO resources."""
        self.center()
        time.sleep(0.3)
        self._backend.cleanup()
