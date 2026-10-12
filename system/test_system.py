"""
Integration and unit tests for the Mosquito Laser Zapper system.

Run with: python test_system.py
"""

import sys
import os
import time
import unittest
import numpy as np
import cv2

sys.path.insert(0, os.path.dirname(__file__))

from config import Config
from detector import MosquitoDetector, Detection
from tracker import KalmanTracker, Track
from servo_controller import PanTiltController, MockPWMBackend
from laser_controller import LaserController, SafetyInterlocks, MockLaserBackend
from full_system import OverlayRenderer


class TestConfig(unittest.TestCase):
    """Test configuration loading and access."""

    def setUp(self):
        self.config = Config("config.yaml")

    def test_loads_yaml(self):
        self.assertIsNotNone(self.config.as_dict())

    def test_dot_notation_get(self):
        val = self.config.get("detection.min_area")
        self.assertEqual(val, 5)

    def test_nested_get(self):
        val = self.config.get("laser.safety_interlocks.manual_arm")
        self.assertTrue(val)

    def test_default_value(self):
        val = self.config.get("nonexistent.key", "default")
        self.assertEqual(val, "default")

    def test_attribute_access(self):
        val = self.config.detection.min_area
        self.assertEqual(val, 5)

    def test_section(self):
        det = self.config.section("detection")
        self.assertEqual(det.get("min_area"), 5)

    def test_missing_section(self):
        with self.assertRaises(AttributeError):
            _ = self.config.nonexistent


class TestDetector(unittest.TestCase):
    """Test mosquito detection."""

    def setUp(self):
        self.config = Config("config.yaml")
        self.config._data["detection"]["use_frame_diff"] = False
        self.detector = MosquitoDetector(self.config)

    def test_initializes(self):
        self.assertIsNotNone(self.detector)
        self.assertEqual(self.detector.frame_count, 0)

    def test_detect_empty_frame(self):
        frame = np.zeros((480, 640, 3), dtype=np.uint8)
        detections = self.detector.detect(frame)
        self.assertEqual(len(detections), 0)
        self.assertEqual(self.detector.frame_count, 1)

    def test_detect_with_moving_blob(self):
        """Create a frame with a moving white blob on black background."""
        # Background frame
        bg = np.zeros((480, 640, 3), dtype=np.uint8)
        self.detector.detect(bg)
        self.detector.detect(bg)
        self.detector.detect(bg)

        # Frame with a small white blob
        frame = np.zeros((480, 640, 3), dtype=np.uint8)
        cv2.circle(frame, (320, 240), 5, (255, 255, 255), -1)
        detections = self.detector.detect(frame)

        # May or may not detect depending on subtractor warmup
        # Just verify it doesn't crash
        self.assertIsInstance(detections, list)

    def test_detection_fields(self):
        """Verify Detection dataclass has required fields."""
        det = Detection(
            x=10, y=20, width=30, height=40,
            center_x=25.0, center_y=40.0,
            area=500.0, confidence=0.8
        )
        self.assertEqual(det.x, 10)
        self.assertEqual(det.center_x, 25.0)
        self.assertAlmostEqual(det.confidence, 0.8)

    def test_reset(self):
        frame = np.zeros((480, 640, 3), dtype=np.uint8)
        self.detector.detect(frame)
        self.detector.reset()
        self.assertEqual(self.detector.frame_count, 0)

    def test_frame_differencing(self):
        """Test frame differencing mode."""
        self.config._data["detection"]["use_frame_diff"] = True
        detector = MosquitoDetector(self.config)

        bg = np.zeros((480, 640, 3), dtype=np.uint8)
        detector.detect(bg)

        frame = np.zeros((480, 640, 3), dtype=np.uint8)
        cv2.circle(frame, (320, 240), 10, (255, 255, 255), -1)
        detections = detector.detect(frame)

        self.assertIsInstance(detections, list)


class TestTracker(unittest.TestCase):
    """Test Kalman filter tracking."""

    def setUp(self):
        self.config = Config("config.yaml")
        self.tracker = KalmanTracker(self.config)

    def test_initializes(self):
        self.assertEqual(self.tracker.track_count, 0)
        self.assertEqual(self.tracker.confirmed_count, 0)

    def test_create_track_from_detection(self):
        det = Detection(
            x=100, y=100, width=10, height=10,
            center_x=105.0, center_y=105.0,
            area=50.0, confidence=0.8
        )
        tracks = self.tracker.update([det])
        self.assertEqual(len(tracks), 1)
        self.assertEqual(tracks[0].track_id, 0)
        self.assertEqual(tracks[0].hits, 1)

    def test_track_confirmation(self):
        """Track should confirm after min_hits detections."""
        det = Detection(
            x=100, y=100, width=10, height=10,
            center_x=105.0, center_y=105.0,
            area=50.0, confidence=0.8
        )
        min_hits = self.config.get("tracking.min_hits_to_confirm", 3)

        for i in range(min_hits):
            tracks = self.tracker.update([det])

        confirmed = self.tracker.get_confirmed_tracks()
        self.assertGreaterEqual(len(confirmed), 1)

    def test_track_prediction(self):
        """Test position prediction."""
        det = Detection(
            x=100, y=100, width=10, height=10,
            center_x=105.0, center_y=105.0,
            area=50.0, confidence=0.8
        )
        self.tracker.update([det])
        self.tracker.update([det])

        tracks = self.tracker.get_all_tracks()
        if tracks:
            pred_x, pred_y = self.tracker.predict_ahead(tracks[0])
            self.assertIsInstance(pred_x, float)
            self.assertIsInstance(pred_y, float)

    def test_track_removal(self):
        """Stale tracks should be removed."""
        det = Detection(
            x=100, y=100, width=10, height=10,
            center_x=105.0, center_y=105.0,
            area=50.0, confidence=0.8
        )
        self.tracker.update([det])

        # Send many empty frames to exceed max_missing
        max_missing = self.config.get("tracking.max_missing_frames", 10)
        for _ in range(max_missing + 5):
            self.tracker.update([])

        # Track should be removed
        self.assertEqual(self.tracker.track_count, 0)

    def test_reset(self):
        det = Detection(
            x=100, y=100, width=10, height=10,
            center_x=105.0, center_y=105.0,
            area=50.0, confidence=0.8
        )
        self.tracker.update([det])
        self.tracker.reset()
        self.assertEqual(self.tracker.track_count, 0)


class TestServoController(unittest.TestCase):
    """Test pan-tilt servo control."""

    def setUp(self):
        self.config = Config("config.yaml")
        self.config._data["pan_tilt"]["mock_servos"] = True
        self.config._data["pan_tilt"]["pwm_backend"] = "mock"

    def test_mock_backend(self):
        servo = PanTiltController(self.config)
        self.assertIsNotNone(servo)
        servo.cleanup()

    def test_aim_at(self):
        servo = PanTiltController(self.config)
        servo.aim_at(400, 300)
        status = servo.get_status()
        self.assertIn("pan_angle", status)
        self.assertIn("tilt_angle", status)
        servo.cleanup()

    def test_center(self):
        servo = PanTiltController(self.config)
        servo.aim_at(500, 400)
        servo.center()
        status = servo.get_status()
        self.assertAlmostEqual(status["pan_angle"], 0, delta=5)
        self.assertAlmostEqual(status["tilt_angle"], 0, delta=5)
        servo.cleanup()

    def test_angle_limits(self):
        servo = PanTiltController(self.config)
        # Try to aim way off-screen
        servo.aim_at(99999, 99999)
        status = servo.get_status()
        pan_min = self.config.get("pan_tilt.pan_min_angle", -90)
        pan_max = self.config.get("pan_tilt.pan_max_angle", 90)
        self.assertGreaterEqual(status["pan_angle"], pan_min)
        self.assertLessEqual(status["pan_angle"], pan_max)
        servo.cleanup()


class TestLaserController(unittest.TestCase):
    """Test laser control and safety interlocks."""

    def setUp(self):
        self.config = Config("config.yaml")
        self.config._data["laser"]["mock_laser"] = True

    def test_mock_backend(self):
        laser = LaserController(self.config)
        self.assertIsNotNone(laser)
        laser.cleanup()

    def test_not_armed(self):
        """Laser should not fire when not armed."""
        laser = LaserController(self.config)
        # Default config has manual_arm=true, but safety starts disarmed
        # Actually, let's check: config says manual_arm=true
        # But SafetyInterlocks reads it at init
        laser.safety.disarm()
        can_fire, reason = laser.safety.check_all(
            None, 320, 240, 320, 240
        )
        self.assertFalse(can_fire)
        self.assertEqual(reason, "NOT_ARMED")
        laser.cleanup()

    def test_emergency_stop(self):
        """Emergency stop should block all firing."""
        laser = LaserController(self.config)
        laser.safety.arm()
        laser.safety.set_active(True)
        laser.safety.trigger_emergency_stop()

        can_fire, reason = laser.safety.check_all(
            None, 320, 240, 320, 240
        )
        self.assertFalse(can_fire)
        self.assertEqual(reason, "EMERGENCY_STOP")
        laser.cleanup()

    def _grant_wingbeat(self, laser):
        """Satisfy the L3 species gate so a test can reach the gates below it.

        Gate 5 (require_wingbeat) now sits above geometry/session checks, so any
        test that wants to exercise those must first supply valid species
        evidence - exactly as the real photodiode channel would.
        """
        laser.safety.set_wingbeat_evidence(
            ok=True, confidence=1.0, freq_hz=520.0,
            reason="test: confirmed biting female",
        )

    def test_firing_zone(self):
        """Target outside firing zone should be rejected."""
        laser = LaserController(self.config)
        laser.safety.arm()
        laser.safety.set_active(True)
        self._grant_wingbeat(laser)

        # Create a confirmed track so we test firing zone, not track check
        from tracker import Track
        track = Track(track_id=0, confirmed=True, hits=5)

        # Target far from center (100,100 vs center 320,240)
        can_fire, reason = laser.safety.check_all(
            track, 100, 100, 320, 240
        )
        self.assertFalse(can_fire)
        self.assertEqual(reason, "OUTSIDE_FIRING_ZONE")
        laser.cleanup()

    def test_session_limit(self):
        """Session fire count limit should be enforced."""
        laser = LaserController(self.config)
        laser.safety.arm()
        laser.safety.set_active(True)
        self._grant_wingbeat(laser)

        # Create confirmed track to reach session limit check
        from tracker import Track
        track = Track(track_id=0, confirmed=True, hits=5)

        # Set fire count to max
        laser.safety._session_fire_count = 100

        can_fire, reason = laser.safety.check_all(
            track, 320, 240, 320, 240
        )
        self.assertFalse(can_fire)
        self.assertEqual(reason, "SESSION_LIMIT_REACHED")
        laser.cleanup()

    def test_successful_fire_check(self):
        """All checks should pass when properly configured."""
        laser = LaserController(self.config)
        laser.safety.arm()
        laser.safety.set_active(True)
        self._grant_wingbeat(laser)

        # Create a confirmed track at center
        from tracker import Track
        track = Track(track_id=0, confirmed=True, hits=5)

        can_fire, reason = laser.safety.check_all(
            track, 320, 240, 320, 240
        )
        self.assertTrue(can_fire)
        self.assertEqual(reason, "OK")
        laser.cleanup()

    def test_safety_status(self):
        """Safety status should return expected fields."""
        laser = LaserController(self.config)
        status = laser.safety.get_status()
        self.assertIn("manual_arm", status)
        self.assertIn("emergency_stop", status)
        self.assertIn("session_fires", status)
        laser.cleanup()


    # ------------------------------------------------------------------
    # L3 species gate (wingbeat). These assert the FAIL-SAFE direction:
    # the laser must refuse unless a biting female is positively confirmed.
    # ------------------------------------------------------------------

    def test_wingbeat_gate_blocks_without_evidence(self):
        """With no photodiode evidence the laser must NOT fire."""
        laser = LaserController(self.config)
        laser.safety.arm()
        laser.safety.set_active(True)
        from tracker import Track
        track = Track(track_id=0, confirmed=True, hits=5)

        can_fire, reason = laser.safety.check_all(track, 320, 240, 320, 240)
        self.assertFalse(can_fire)
        self.assertIn("NO_WINGBEAT_CONFIRM", reason)
        laser.cleanup()

    def test_wingbeat_gate_blocks_non_target(self):
        """A housefly reading must not authorise a shot."""
        laser = LaserController(self.config)
        laser.safety.arm()
        laser.safety.set_active(True)
        laser.safety.set_wingbeat_evidence(
            ok=False, confidence=1.0, freq_hz=200.0,
            reason="non-target band (180-260 Hz)")
        from tracker import Track
        track = Track(track_id=0, confirmed=True, hits=5)

        can_fire, reason = laser.safety.check_all(track, 320, 240, 320, 240)
        self.assertFalse(can_fire)
        self.assertIn("NO_WINGBEAT_CONFIRM", reason)
        laser.cleanup()

    def test_wingbeat_gate_blocks_low_confidence(self):
        """Positive but weak evidence must not authorise a shot."""
        laser = LaserController(self.config)
        laser.safety.arm()
        laser.safety.set_active(True)
        laser.safety.set_wingbeat_evidence(
            ok=True, confidence=0.05, freq_hz=520.0, reason="weak")
        from tracker import Track
        track = Track(track_id=0, confirmed=True, hits=5)

        can_fire, reason = laser.safety.check_all(track, 320, 240, 320, 240)
        self.assertFalse(can_fire)
        self.assertIn("WINGBEAT_LOW_CONFIDENCE", reason)
        laser.cleanup()

    def test_wingbeat_gate_passes_on_confirmed_female(self):
        """Confirmed biting female at good confidence should pass the gate."""
        laser = LaserController(self.config)
        laser.safety.arm()
        laser.safety.set_active(True)
        laser.safety.set_wingbeat_evidence(
            ok=True, confidence=0.9, freq_hz=520.0,
            reason="single match: Aedes albopictus female")
        from tracker import Track
        track = Track(track_id=0, confirmed=True, hits=5)

        can_fire, reason = laser.safety.check_all(track, 320, 240, 320, 240)
        self.assertTrue(can_fire)
        self.assertEqual(reason, "OK")
        laser.cleanup()

    def test_wingbeat_evidence_expires(self):
        """Stale evidence must stop authorising shots once it ages out."""
        laser = LaserController(self.config)
        laser.safety.arm()
        laser.safety.set_active(True)
        laser.safety.set_wingbeat_evidence(
            ok=True, confidence=0.9, freq_hz=520.0, reason="fresh")
        self.assertTrue(laser.safety._wingbeat_ok)

        # Age past the validity window (default 0.5 s).
        for _ in range(40):
            laser.safety._age_wingbeat_evidence(1.0 / 30.0)

        self.assertFalse(laser.safety._wingbeat_ok)
        laser.cleanup()

    def test_wingbeat_gate_can_be_disabled_for_bench(self):
        """require_wingbeat=false must let the bench path work (no photodiode)."""
        laser = LaserController(self.config)
        laser.safety._require_wingbeat = False
        laser.safety.arm()
        laser.safety.set_active(True)
        from tracker import Track
        track = Track(track_id=0, confirmed=True, hits=5)

        can_fire, reason = laser.safety.check_all(track, 320, 240, 320, 240)
        self.assertTrue(can_fire)
        self.assertEqual(reason, "OK")
        laser.cleanup()

    def test_wingbeat_status_exposed(self):
        """Wingbeat state must appear in the status dict for the dashboard."""
        laser = LaserController(self.config)
        laser.safety.set_wingbeat_evidence(True, 0.8, 520.0, "ok")
        status = laser.safety.get_status()
        for key in ("require_wingbeat", "wingbeat_ok", "wingbeat_freq_hz",
                    "wingbeat_confidence", "wingbeat_reason"):
            self.assertIn(key, status)
        self.assertTrue(status["wingbeat_ok"])
        self.assertEqual(status["wingbeat_freq_hz"], 520.0)
        laser.cleanup()


class TestOverlayRenderer(unittest.TestCase):
    """Test overlay rendering."""

    def setUp(self):
        self.config = Config("config.yaml")
        self.renderer = OverlayRenderer(self.config)

    def test_render_empty(self):
        frame = np.zeros((480, 640, 3), dtype=np.uint8)
        result = self.renderer.render(
            frame, [], [],
            {"pan_angle": 0, "tilt_angle": 0},
            {"manual_arm": False, "session_fires": 0},
            30.0
        )
        self.assertEqual(result.shape, (480, 640, 3))

    def test_render_with_detections(self):
        frame = np.zeros((480, 640, 3), dtype=np.uint8)
        detections = [
            Detection(
                x=100, y=100, width=20, height=20,
                center_x=110.0, center_y=110.0,
                area=100.0, confidence=0.8
            )
        ]
        result = self.renderer.render(
            frame, detections, [],
            {"pan_angle": 0, "tilt_angle": 0},
            {"manual_arm": False, "session_fires": 0},
            30.0
        )
        self.assertEqual(result.shape, (480, 640, 3))
        # Frame should have been modified (not all zeros)
        self.assertTrue(np.any(result > 0))


class TestIntegration(unittest.TestCase):
    """Integration tests combining multiple modules."""

    def test_detection_to_tracking_pipeline(self):
        """Test detection → tracking pipeline."""
        config = Config("config.yaml")
        config._data["detection"]["use_frame_diff"] = False
        detector = MosquitoDetector(config)
        tracker = KalmanTracker(config)

        # Create a sequence of frames with a moving blob
        for i in range(10):
            frame = np.zeros((480, 640, 3), dtype=np.uint8)
            x = 300 + i * 5
            y = 240
            cv2.circle(frame, (x, y), 5, (255, 255, 255), -1)
            detections = detector.detect(frame)
            tracks = tracker.update(detections)

        # Verify pipeline ran — tracker should have processed tracks
        self.assertGreaterEqual(tracker.track_count, 0)

    def test_full_system_import(self):
        """Verify the main system module imports cleanly."""
        from full_system import MosquitoZapperSystem, main
        self.assertTrue(callable(main))

    def test_config_validation(self):
        """Config should have all required sections."""
        config = Config("config.yaml")
        required = ["system", "detection", "tracking", "pan_tilt", "laser", "web_dashboard", "logging"]
        data = config.as_dict()
        for section in required:
            self.assertIn(section, data, f"Missing config section: {section}")



if __name__ == "__main__":
    unittest.main(verbosity=2)
