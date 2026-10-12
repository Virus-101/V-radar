"""
Target Tracking Module.

Implements a Kalman filter-based tracker for mosquito targets.
Each track maintains a state estimate (position + velocity) and
is associated with detections across frames using a simple
nearest-neighbor association with a distance threshold.
"""

import numpy as np
import cv2
from typing import List, Optional, Dict, Tuple
from dataclasses import dataclass, field
from collections import deque
import time


@dataclass
class Track:
    """A single tracked target."""
    track_id: int
    # Kalman filter state: [x, y, vx, vy]
    state: np.ndarray = field(default_factory=lambda: np.zeros(4))
    covariance: np.ndarray = field(default_factory=lambda: np.eye(4) * 100)

    # Track metadata
    hits: int = 0                    # Total detections matched
    misses: int = 0                  # Consecutive frames without match
    age: int = 0                     # Total frames since creation
    confirmed: bool = False          # Track is confirmed (min_hits met)
    created_at: float = 0.0          # Timestamp

    # History for visualization
    trail: deque = field(default_factory=lambda: deque(maxlen=30))

    # Last known position (for aiming)
    last_x: float = 0.0
    last_y: float = 0.0

    # Velocity estimate
    vx: float = 0.0
    vy: float = 0.0

    # Firing cooldown tracking
    last_fire_time: float = 0.0
    fire_count: int = 0


class KalmanTracker:
    """Kalman filter tracker for mosquito targets.

    State vector: [x, y, vx, vy]
    Measurement:  [x, y]

    Uses a constant-velocity motion model with process noise Q
    and measurement noise R configurable from the config file.
    """

    def __init__(self, config):
        self.config = config
        self._tracks: Dict[int, Track] = {}
        self._next_id: int = 0

        # Kalman filter parameters
        self._process_noise = config.get("tracking.process_noise", 0.01)
        self._measurement_noise = config.get("tracking.measurement_noise", 1.0)

        # Track management
        self._max_missing = config.get("tracking.max_missing_frames", 10)
        self._min_hits = config.get("tracking.min_hits_to_confirm", 3)
        self._distance_threshold = config.get("tracking.distance_threshold", 50)

        # Prediction
        self._use_prediction = config.get("tracking.use_prediction", True)
        self._prediction_frames = config.get("tracking.prediction_frames", 5)

        # Build Kalman filter matrices (constant velocity model)
        # State transition: x' = x + vx, y' = y + vy, vx' = vx, vy' = vy
        self._F = np.array([
            [1, 0, 1, 0],
            [0, 1, 0, 1],
            [0, 0, 1, 0],
            [0, 0, 0, 1],
        ], dtype=np.float32)

        # Measurement matrix: we only measure position
        self._H = np.array([
            [1, 0, 0, 0],
            [0, 1, 0, 0],
        ], dtype=np.float32)

        # Process noise covariance
        self._Q = np.eye(4, dtype=np.float32) * self._process_noise

        # Measurement noise covariance
        self._R = np.eye(2, dtype=np.float32) * self._measurement_noise

        # Initial covariance
        self._P_init = np.eye(4, dtype=np.float32) * 100.0

    def update(self, detections: List) -> List[Track]:
        """Update tracks with new detections.

        Args:
            detections: List of Detection objects from the detector.

        Returns:
            List of active Track objects.
        """
        current_time = time.time()

        # Predict all existing tracks
        for track in self._tracks.values():
            self._predict(track)

        # Associate detections to tracks
        matched_tracks, unmatched_detections = self._associate(detections)

        # Update matched tracks
        for track_id, detection in matched_tracks:
            track = self._tracks[track_id]
            self._update_track(track, detection, current_time)

        # Create new tracks for unmatched detections
        for detection in unmatched_detections:
            self._create_track(detection, current_time)

        # Remove stale tracks
        self._remove_stale_tracks()

        # Update trail history
        for track in self._tracks.values():
            track.trail.append((track.last_x, track.last_y))
            track.age += 1

        return list(self._tracks.values())

    def _predict(self, track: Track) -> None:
        """Predict step: project state forward."""
        track.state = self._F @ track.state
        track.covariance = self._F @ track.covariance @ self._F.T + self._Q
        track.misses += 1

    def _update_track(self, track: Track, detection, current_time: float) -> None:
        """Update a track with a matched detection."""
        measurement = np.array(
            [detection.center_x, detection.center_y], dtype=np.float32
        )

        # Kalman gain
        S = self._H @ track.covariance @ self._H.T + self._R
        K = track.covariance @ self._H.T @ np.linalg.inv(S)

        # Update state
        innovation = measurement - self._H @ track.state
        track.state = track.state + K @ innovation

        # Update covariance
        I = np.eye(4, dtype=np.float32)
        track.covariance = (I - K @ self._H) @ track.covariance

        # Update metadata
        track.hits += 1
        track.misses = 0
        track.last_x = float(track.state[0])
        track.last_y = float(track.state[1])
        track.vx = float(track.state[2])
        track.vy = float(track.state[3])

        if track.hits >= self._min_hits:
            track.confirmed = True

    def _create_track(self, detection, current_time: float) -> None:
        """Initialize a new track from a detection."""
        track_id = self._next_id
        self._next_id += 1

        state = np.array([
            detection.center_x,
            detection.center_y,
            0.0,  # initial vx
            0.0,  # initial vy
        ], dtype=np.float32)

        track = Track(
            track_id=track_id,
            state=state,
            covariance=self._P_init.copy(),
            hits=1,
            misses=0,
            age=0,
            confirmed=False,
            created_at=current_time,
            last_x=detection.center_x,
            last_y=detection.center_y,
        )
        track.trail.append((detection.center_x, detection.center_y))

        self._tracks[track_id] = track

    def _associate(self, detections: List) -> Tuple[List[Tuple[int, object]], List]:
        """Associate detections to existing tracks using nearest neighbor.

        Returns:
            (matched_pairs, unmatched_detections)
            where matched_pairs is a list of (track_id, detection) tuples.
        """
        if not self._tracks or not detections:
            return [], list(detections)

        track_ids = list(self._tracks.keys())
        track_positions = np.array([
            [self._tracks[tid].last_x, self._tracks[tid].last_y]
            for tid in track_ids
        ])

        det_positions = np.array([
            [d.center_x, d.center_y] for d in detections
        ])

        # Compute distance matrix
        # Shape: (num_tracks, num_detections)
        diff = track_positions[:, np.newaxis, :] - det_positions[np.newaxis, :, :]
        distances = np.sqrt(np.sum(diff ** 2, axis=2))

        matched = []
        used_tracks = set()
        used_dets = set()

        # Greedy nearest-neighbor assignment
        while True:
            if distances.size == 0:
                break
            min_idx = np.unravel_index(np.argmin(distances), distances.shape)
            min_dist = distances[min_idx]

            if min_dist > self._distance_threshold:
                break

            track_idx, det_idx = min_idx
            track_id = track_ids[track_idx]

            matched.append((track_id, detections[det_idx]))
            used_tracks.add(track_idx)
            used_dets.add(det_idx)

            # Remove this row and column
            distances[track_idx, :] = np.inf
            distances[:, det_idx] = np.inf

        unmatched_dets = [
            detections[i] for i in range(len(detections)) if i not in used_dets
        ]

        return matched, unmatched_dets

    def _remove_stale_tracks(self) -> None:
        """Remove tracks that have been missing for too long."""
        to_remove = [
            tid for tid, track in self._tracks.items()
            if track.misses > self._max_missing
        ]
        for tid in to_remove:
            del self._tracks[tid]

    def get_confirmed_tracks(self) -> List[Track]:
        """Return only confirmed tracks (min_hits met)."""
        return [t for t in self._tracks.values() if t.confirmed]

    def get_all_tracks(self) -> List[Track]:
        """Return all active tracks."""
        return list(self._tracks.values())

    def predict_ahead(self, track: Track, frames: Optional[int] = None) -> Tuple[float, float]:
        """Predict a track's future position for aiming.

        Args:
            track: The track to predict.
            frames: Number of frames ahead (default from config).

        Returns:
            (predicted_x, predicted_y)
        """
        if frames is None:
            frames = self._prediction_frames

        if not self._use_prediction:
            return track.last_x, track.last_y

        # Simple linear prediction using velocity
        pred_x = track.last_x + track.vx * frames
        pred_y = track.last_y + track.vy * frames

        return pred_x, pred_y

    def reset(self) -> None:
        """Remove all tracks."""
        self._tracks.clear()
        self._next_id = 0

    @property
    def track_count(self) -> int:
        """Number of active tracks."""
        return len(self._tracks)

    @property
    def confirmed_count(self) -> int:
        """Number of confirmed tracks."""
        return len(self.get_confirmed_tracks())
