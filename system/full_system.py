"""
Mosquito Laser Zapper — Full Integrated System.

Main entry point that integrates all subsystems:
  1. Video capture (OpenCV)
  2. Mosquito detection (background subtraction)
  3. Target tracking (Kalman filter)
  4. Pan-tilt servo control
  5. Laser firing with safety interlocks
  6. Web dashboard (Flask)
  7. Configuration system (YAML)

Usage:
    python full_system.py [--config config.yaml] [--no-web] [--mock]

Author: Mosquito Zapper Project
License: MIT
"""

import os
import sys
import time
import signal
import logging
import argparse
import threading
from pathlib import Path
from typing import Optional, List, Tuple
from dataclasses import dataclass, field
from collections import deque

import cv2
import numpy as np

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent))

from config import Config
from detector import MosquitoDetector, Detection
from tracker import KalmanTracker, Track
from servo_controller import PanTiltController
from laser_controller import LaserController
from web_dashboard import WebDashboard


# ============================================================
#  Logging Setup
# ============================================================

def setup_logging(config) -> logging.Logger:
    """Configure logging with file and console handlers."""
    log_config = config.section("logging")
    log_level = getattr(logging, log_config.get("level", "INFO").upper(), logging.INFO)
    log_file = log_config.get("file", "mosquito_zapper.log")
    max_bytes = log_config.get("max_bytes", 5242880)
    backup_count = log_config.get("backup_count", 3)
    console_output = log_config.get("console_output", True)

    logger = logging.getLogger("mosquito_zapper")
    logger.setLevel(log_level)

    # File handler with rotation
    try:
        from logging.handlers import RotatingFileHandler
        file_handler = RotatingFileHandler(
            log_file, maxBytes=max_bytes, backupCount=backup_count
        )
        file_handler.setLevel(log_level)
        file_formatter = logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
        )
        file_handler.setFormatter(file_formatter)
        logger.addHandler(file_handler)
    except Exception as e:
        print(f"Warning: Could not set up file logging: {e}")

    # Console handler
    if console_output:
        console_handler = logging.StreamHandler()
        console_handler.setLevel(log_level)
        console_formatter = logging.Formatter(
            "[%(levelname)s] %(message)s"
        )
        console_handler.setFormatter(console_formatter)
        logger.addHandler(console_handler)

    return logger


# ============================================================
#  Overlay Renderer
# ============================================================

class OverlayRenderer:
    """Renders detection and tracking overlays on frames."""

    # Color palette (BGR)
    COLOR_DETECTION = (0, 255, 0)       # Green
    COLOR_TRACK = (255, 128, 0)         # Blue
    COLOR_CONFIRMED = (0, 128, 255)     # Orange
    COLOR_PREDICTION = (255, 0, 255)    # Magenta
    COLOR_FIRING_ZONE = (0, 255, 255)   # Yellow
    COLOR_CROSSHAIR = (0, 0, 255)       # Red
    COLOR_TEXT = (255, 255, 255)        # White

    def __init__(self, config):
        self.config = config
        self._show_detections = True
        self._show_tracks = True
        self._show_trails = True
        self._show_firing_zone = True
        self._show_crosshair = True
        self._show_info = True

    def render(self, frame: np.ndarray,
               detections: List[Detection],
               tracks: List[Track],
               servo_status: dict,
               laser_status: dict,
               fps: float) -> np.ndarray:
        """Render all overlays on a frame.

        Args:
            frame: BGR image
            detections: Current detections
            tracks: Current tracks
            servo_status: Servo status dict
            laser_status: Laser safety status dict
            fps: Current FPS

        Returns:
            Frame with overlays drawn.
        """
        h, w = frame.shape[:2]
        cx, cy = w // 2, h // 2

        # Firing zone circle
        if self._show_firing_zone:
            radius = self.config.get("laser.safety_interlocks.firing_zone_radius", 80)
            cv2.circle(frame, (cx, cy), radius, self.COLOR_FIRING_ZONE, 1)

        # Crosshair at center
        if self._show_crosshair:
            cv2.drawMarker(frame, (cx, cy), self.COLOR_CROSSHAIR,
                          cv2.MARKER_CROSS, 20, 2)

        # Detections
        if self._show_detections:
            for det in detections:
                cv2.rectangle(frame, (det.x, det.y),
                            (det.x + det.width, det.y + det.height),
                            self.COLOR_DETECTION, 1)
                cv2.circle(frame, (int(det.center_x), int(det.center_y)),
                          2, self.COLOR_DETECTION, -1)

        # Tracks
        if self._show_tracks:
            for track in tracks:
                color = self.COLOR_CONFIRMED if track.confirmed else self.COLOR_TRACK

                # Bounding box at current position
                x = int(track.last_x)
                y = int(track.last_y)
                cv2.circle(frame, (x, y), 8, color, 2)

                # Track ID
                cv2.putText(frame, f"#{track.track_id}",
                          (x + 10, y - 10),
                          cv2.FONT_HERSHEY_SIMPLEX, 0.4, color, 1)

                # Trail
                if self._show_trails and len(track.trail) > 1:
                    points = list(track.trail)
                    for i in range(1, len(points)):
                        pt1 = (int(points[i-1][0]), int(points[i-1][1]))
                        pt2 = (int(points[i][0]), int(points[i][1]))
                        alpha = i / len(points)
                        trail_color = tuple(int(c * alpha) for c in color)
                        cv2.line(frame, pt1, pt2, trail_color, 1)

                # Velocity vector
                if abs(track.vx) > 0.5 or abs(track.vy) > 0.5:
                    end_x = int(x + track.vx * 5)
                    end_y = int(y + track.vy * 5)
                    cv2.arrowedLine(frame, (x, y), (end_x, end_y),
                                  self.COLOR_PREDICTION, 1)

        # Info panel
        if self._show_info:
            self._draw_info_panel(frame, detections, tracks, servo_status,
                                laser_status, fps)

        return frame

    def _draw_info_panel(self, frame: np.ndarray,
                        detections: List[Detection],
                        tracks: List[Track],
                        servo_status: dict,
                        laser_status: dict,
                        fps: float) -> None:
        """Draw information panel on frame."""
        h, w = frame.shape[:2]

        # Semi-transparent background
        panel_w = 200
        panel_h = 120
        overlay = frame.copy()
        cv2.rectangle(overlay, (5, 5), (panel_w, panel_h), (0, 0, 0), -1)
        cv2.addWeighted(overlay, 0.6, frame, 0.4, 0, frame)

        # Text lines
        lines = [
            f"FPS: {fps:.1f}",
            f"Detections: {len(detections)}",
            f"Tracks: {len(tracks)}",
            f"Confirmed: {sum(1 for t in tracks if t.confirmed)}",
            f"Pan: {servo_status.get('pan_angle', 0):.1f} deg",
            f"Tilt: {servo_status.get('tilt_angle', 0):.1f} deg",
            f"Laser: {'ARMED' if laser_status.get('manual_arm') else 'SAFE'}",
            f"Fires: {laser_status.get('session_fires', 0)}",
        ]

        y_offset = 20
        for line in lines:
            cv2.putText(frame, line, (10, y_offset),
                       cv2.FONT_HERSHEY_SIMPLEX, 0.4, self.COLOR_TEXT, 1)
            y_offset += 14


# ============================================================
#  Main System
# ============================================================

class MosquitoZapperSystem:
    """Main system integrating all subsystems.

    Manages the main processing loop, coordinates between
    detection, tracking, servo control, and laser firing.
    """

    def __init__(self, config_path: Optional[str] = None,
                 no_web: bool = False, mock: bool = False):
        """Initialize the full system.

        Args:
            config_path: Path to YAML config file
            no_web: Disable web dashboard
            mock: Force mock mode for all hardware
        """
        # Load configuration
        self.config = Config(config_path)

        # Override with command-line flags
        if mock:
            self.config._data["pan_tilt"]["mock_servos"] = True
            self.config._data["laser"]["mock_laser"] = True
        if no_web:
            self.config._data["system"]["web_dashboard_enabled"] = False

        # Setup logging
        self.logger = setup_logging(self.config)
        self.logger.info("=" * 60)
        self.logger.info("Mosquito Laser Zapper System Starting")
        self.logger.info("=" * 60)

        # Initialize subsystems
        self._init_camera()
        self._init_detector()
        self._init_tracker()
        self._init_servo()
        self._init_laser()
        self._init_web_dashboard()

        # Overlay renderer
        self.overlay = OverlayRenderer(self.config)

        # Runtime state
        self._running = False
        self._paused = False
        self._frame_count = 0
        self._fps = 0.0
        self._fps_history = deque(maxlen=30)
        self._last_fps_time = time.time()
        self._start_time = time.time()

        # Processing
        self._process_every_n = self.config.get("performance.process_every_n_frames", 1)
        self._detection_width = self.config.get("performance.detection_width", 320)
        self._detection_height = self.config.get("performance.detection_height", 240)

        # Current state
        self._current_detections: List[Detection] = []
        self._current_tracks: List[Track] = []
        self._target_track: Optional[Track] = None

        # Statistics
        self._total_fires = 0
        # Latest photodiode burst (numpy array) supplied by the sensor head.
        # None until a burst arrives; see _update_wingbeat_evidence().
        self._wingbeat_burst = None
        self._total_detections = 0

        # Signal handling
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)

        self.logger.info("System initialization complete")

    def _init_camera(self) -> None:
        """Initialize video capture.

        Source is `system.camera_index` (an int) for a live camera, or
        `system.video_source` (a path) to replay a video file. The file path
        form is what makes the pipeline testable and demonstrable without a
        camera attached, and what the preview/demo scripts use.
        """
        video_source = self.config.get("system.video_source", None)
        camera_index = self.config.get("system.camera_index", 0)
        width = self.config.get("system.frame_width", 640)
        height = self.config.get("system.frame_height", 480)

        if video_source:
            self.logger.info(f"Opening video file {video_source} ({width}x{height})")
            self._cap = cv2.VideoCapture(video_source)
            self._is_video_file = True
        else:
            self.logger.info(f"Opening camera {camera_index} ({width}x{height})")
            self._cap = cv2.VideoCapture(camera_index)
            self._is_video_file = False

        if not self._cap.isOpened():
            self.logger.error(f"Failed to open source: {video_source or camera_index}")
            raise RuntimeError(f"Cannot open source: {video_source or camera_index}")

        self._cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
        self._cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
        self._cap.set(cv2.CAP_PROP_FPS, self.config.get("system.fps", 30))

        actual_width = int(self._cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        actual_height = int(self._cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        self.logger.info(f"Camera opened: {actual_width}x{actual_height}")

    def _init_detector(self) -> None:
        """Initialize mosquito detector."""
        if self.config.get("system.detection_enabled", True):
            self.logger.info("Initializing mosquito detector")
            self._detector = MosquitoDetector(self.config)
        else:
            self.logger.warning("Detection disabled")
            self._detector = None

    def _init_tracker(self) -> None:
        """Initialize target tracker."""
        if self.config.get("system.tracking_enabled", True):
            self.logger.info("Initializing Kalman tracker")
            self._tracker = KalmanTracker(self.config)
        else:
            self.logger.warning("Tracking disabled")
            self._tracker = None

    def _init_servo(self) -> None:
        """Initialize pan-tilt servo controller."""
        if self.config.get("system.servo_enabled", True):
            self.logger.info("Initializing pan-tilt servos")
            try:
                self._servo = PanTiltController(self.config)
            except Exception as e:
                self.logger.error(f"Failed to initialize servos: {e}")
                self._servo = None
        else:
            self.logger.warning("Servo control disabled")
            self._servo = None

    def _init_laser(self) -> None:
        """Initialize laser controller."""
        if self.config.get("system.laser_enabled", True):
            self.logger.info("Initializing laser controller")
            try:
                self._laser = LaserController(self.config)
            except Exception as e:
                self.logger.error(f"Failed to initialize laser: {e}")
                self._laser = None
        else:
            self.logger.warning("Laser control disabled")
            self._laser = None

    def _init_web_dashboard(self) -> None:
        """Initialize web dashboard."""
        if self.config.get("system.web_dashboard_enabled", True):
            self.logger.info("Initializing web dashboard")
            try:
                self._web = WebDashboard(self.config, self)
                self._web.start()
            except Exception as e:
                self.logger.error(f"Failed to start web dashboard: {e}")
                self._web = None
        else:
            self.logger.info("Web dashboard disabled")
            self._web = None

    def _signal_handler(self, signum, frame):
        """Handle shutdown signals gracefully."""
        self.logger.info(f"Received signal {signum}, shutting down...")
        self._running = False

    def run(self) -> None:
        """Main processing loop."""
        self.logger.info("Starting main processing loop")
        self._running = True

        # Warm up camera. For a live camera this flushes stale auto-exposure
        # frames; for a video file it would consume real footage, so skip it.
        if not self._is_video_file:
            self.logger.info("Warming up camera...")
            for _ in range(10):
                self._cap.read()
        else:
            self.logger.info("Video file source - skipping camera warmup")

        frame_time = 1.0 / self.config.get("system.fps", 30)

        while self._running:
            loop_start = time.time()

            if not self._paused:
                ret, frame = self._cap.read()
                if not ret:
                    if self._is_video_file:
                        # End of file is a normal exit for a file source,
                        # not an error.
                        self.logger.info("End of video file - stopping")
                        self._running = False
                        break
                    self.logger.warning("Failed to capture frame")
                    time.sleep(0.01)
                    continue

                self._frame_count += 1

                # Process frame
                if self._frame_count % self._process_every_n == 0:
                    self._process_frame(frame)

                # Calculate FPS
                current_time = time.time()
                dt = current_time - self._last_fps_time
                if dt > 0:
                    self._fps_history.append(1.0 / dt)
                    self._fps = sum(self._fps_history) / len(self._fps_history)
                self._last_fps_time = current_time

                # Update web dashboard
                if self._web and self._web.is_running:
                    self._update_web(frame)

            # Frame rate limiting
            elapsed = time.time() - loop_start
            sleep_time = frame_time - elapsed
            if sleep_time > 0:
                time.sleep(sleep_time)

        self.shutdown()

    def _process_frame(self, frame: np.ndarray) -> None:
        """Process a single frame through the pipeline.

        Args:
            frame: BGR image from camera.
        """
        h, w = frame.shape[:2]

        # Resize for detection (faster)
        det_w = self._detection_width
        det_h = self._detection_height
        det_frame = cv2.resize(frame, (det_w, det_h))

        # 1. Detection
        if self._detector:
            self._current_detections = self._detector.detect(det_frame)
            self._total_detections += len(self._current_detections)

            # Scale detections back to full frame
            scale_x = w / det_w
            scale_y = h / det_h
            for det in self._current_detections:
                det.x = int(det.x * scale_x)
                det.y = int(det.y * scale_y)
                det.width = int(det.width * scale_x)
                det.height = int(det.height * scale_y)
                det.center_x *= scale_x
                det.center_y *= scale_y

        # 2. Tracking
        if self._tracker:
            self._current_tracks = self._tracker.update(self._current_detections)

        # 3. Select target and aim
        self._target_track = self._select_target()

        if self._target_track and self._servo:
            # Predict ahead for aiming
            pred_x, pred_y = self._tracker.predict_ahead(self._target_track)
            self._servo.aim_at(pred_x, pred_y)

        # 4. Laser firing
        if self._laser and self._target_track:
            # Refresh/expire the species evidence BEFORE the fire attempt, so
            # the gate always sees current data. Without this the evidence
            # would never age and a stale reading could authorise a shot.
            self._update_wingbeat_evidence()
            self._try_fire_laser()

        # 5. Check continuous fire timeout
        if self._laser and self._laser.safety.check_continuous_timeout():
            self._laser.stop()
            self.logger.warning("Laser continuous fire timeout — stopped")

    def _select_target(self) -> Optional[Track]:
        """Select the best target to aim at.

        Priority: confirmed tracks closest to center.

        Returns:
            Best target track, or None.
        """
        if not self._current_tracks:
            return None

        confirmed = [t for t in self._current_tracks if t.confirmed]
        if not confirmed:
            return None

        # Find track closest to center
        h, w = 480, 640  # Default, will be updated
        cx, cy = w // 2, h // 2

        best_track = None
        best_distance = float("inf")

        for track in confirmed:
            dx = track.last_x - cx
            dy = track.last_y - cy
            dist = (dx ** 2 + dy ** 2) ** 0.5
            if dist < best_distance:
                best_distance = dist
                best_track = track

        return best_track

    def _update_wingbeat_evidence(self) -> None:
        """Feed the species gate from the photodiode channel.

        Two modes:

        * **Live** - if a photodiode burst is available (``self._wingbeat_burst``
          is a numpy array), classify it with ``detection/wingbeat.py`` and pass
          the verdict to the interlock. This is the real path on hardware.
        * **Bench** - with no photodiode fitted, no evidence is supplied, so the
          gate correctly stays closed and the laser will not fire. That is the
          intended fail-safe behaviour, not a bug.

        The evidence is aged every frame so a reading from an insect that has
        already left the curtain cannot authorise a later shot.
        """
        if self._laser is None:
            return

        safety = self._laser.safety

        burst = getattr(self, "_wingbeat_burst", None)
        if burst is not None and len(burst) > 0:
            try:
                from wingbeat import analyse_burst
            except ImportError:
                # detection/ is not on sys.path; add it and retry once.
                import sys as _sys
                from pathlib import Path as _Path
                _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent
                                        / "detection"))
                from wingbeat import analyse_burst

            result = analyse_burst(burst)
            safety.set_wingbeat_evidence(
                ok=result.should_fire,
                confidence=result.confidence,
                freq_hz=result.frequency_hz or 0.0,
                reason=result.reason,
            )
            self._wingbeat_burst = None
        else:
            # No new burst: age whatever we have. If it is older than the
            # validity window the gate closes on its own.
            safety._age_wingbeat_evidence(1.0 / max(self._fps or 30.0, 1.0))

    def _try_fire_laser(self) -> None:
        """Attempt to fire the laser at the current target."""
        if not self._target_track or not self._laser:
            return

        h, w = 480, 640
        cx, cy = w // 2, h // 2

        # Use predicted position for firing
        if self._tracker:
            pred_x, pred_y = self._tracker.predict_ahead(self._target_track)
        else:
            pred_x, pred_y = self._target_track.last_x, self._target_track.last_y

        fired, reason = self._laser.try_fire(
            self._target_track, pred_x, pred_y, cx, cy
        )

        if fired:
            self._total_fires += 1
            self.logger.info(
                f"FIRED at track #{self._target_track.track_id} "
                f"({pred_x:.0f}, {pred_y:.0f})"
            )
        elif reason not in ("TARGET_COOLDOWN", "SHOT_COOLDOWN"):
            # Log non-cooldown rejections at debug level
            self.logger.debug(f"Laser not fired: {reason}")

    def _update_web(self, frame: np.ndarray) -> None:
        """Update the web dashboard with current frame and stats."""
        if not self._web:
            return

        # Render overlays
        servo_status = self._servo.get_status() if self._servo else {}
        laser_status = self._laser.safety.get_status() if self._laser else {}

        overlay_frame = self.overlay.render(
            frame.copy(),
            self._current_detections,
            self._current_tracks,
            servo_status,
            laser_status,
            self._fps,
        )

        # Encode to JPEG
        quality = self.config.get("web_dashboard.stream_quality", 80)
        encode_params = [cv2.IMWRITE_JPEG_QUALITY, quality]
        _, buffer = cv2.imencode(".jpg", overlay_frame, encode_params)
        frame_bytes = buffer.tobytes()

        self._web.update_frame(frame_bytes)
        self._web.update_stats(
            fps=self._fps,
            detections=len(self._current_detections),
            tracks=len(self._current_tracks),
            confirmed=sum(1 for t in self._current_tracks if t.confirmed),
            fires=self._total_fires,
        )

    def shutdown(self) -> None:
        """Gracefully shut down all subsystems."""
        self.logger.info("Shutting down system...")

        self._running = False

        # Stop laser
        if self._laser:
            self._laser.cleanup()
            self.logger.info("Laser controller stopped")

        # Stop servos
        if self._servo:
            self._servo.cleanup()
            self.logger.info("Servo controller stopped")

        # Stop web dashboard
        if self._web:
            self._web.stop()
            self.logger.info("Web dashboard stopped")

        # Release camera
        if hasattr(self, "_cap") and self._cap:
            self._cap.release()
            self.logger.info("Camera released")

        # Print summary
        uptime = time.time() - self._start_time
        self.logger.info("=" * 60)
        self.logger.info("Session Summary:")
        self.logger.info(f"  Uptime: {uptime:.1f}s")
        self.logger.info(f"  Frames processed: {self._frame_count}")
        self.logger.info(f"  Total detections: {self._total_detections}")
        self.logger.info(f"  Total shots fired: {self._total_fires}")
        self.logger.info(f"  Average FPS: {self._frame_count / max(uptime, 1):.1f}")
        self.logger.info("=" * 60)
        self.logger.info("System shutdown complete")

    def pause(self) -> None:
        """Pause processing."""
        self._paused = True
        self.logger.info("System paused")

    def resume(self) -> None:
        """Resume processing."""
        self._paused = False
        self.logger.info("System resumed")

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def fps(self) -> float:
        return self._fps

    @property
    def frame_count(self) -> int:
        return self._frame_count

    @property
    def total_fires(self) -> int:
        return self._total_fires


# ============================================================
#  Entry Point
# ============================================================

def main():
    """Parse arguments and start the system."""
    parser = argparse.ArgumentParser(
        description="Mosquito Laser Zapper — Full Integrated System"
    )
    parser.add_argument(
        "--config", "-c",
        default=None,
        help="Path to configuration file (default: config.yaml)"
    )
    parser.add_argument(
        "--no-web",
        action="store_true",
        help="Disable web dashboard"
    )
    parser.add_argument(
        "--mock",
        action="store_true",
        help="Use mock hardware (no GPIO required)"
    )
    parser.add_argument(
        "--detect-only",
        action="store_true",
        help="Only run detection (no tracking, servo, or laser)"
    )

    args = parser.parse_args()

    try:
        system = MosquitoZapperSystem(
            config_path=args.config,
            no_web=args.no_web,
            mock=args.mock,
        )

        if args.detect_only:
            # Disable everything except detection
            system.config._data["system"]["tracking_enabled"] = False
            system.config._data["system"]["servo_enabled"] = False
            system.config._data["system"]["laser_enabled"] = False
            system._servo = None
            system._laser = None
            system._tracker = None

        system.run()

    except KeyboardInterrupt:
        print("\nInterrupted by user")
    except Exception as e:
        print(f"Fatal error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
