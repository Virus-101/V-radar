"""
Mosquito Detection Module.

Detects mosquito candidates in video frames using background
subtraction (MOG2 or KNN) or frame differencing as a simpler
alternative. Applies morphological operations and contour
filtering to produce a list of detected bounding boxes.
"""

import cv2
import numpy as np
from typing import List, Tuple, Optional
from dataclasses import dataclass


@dataclass
class Detection:
    """A single mosquito detection."""
    x: int              # Top-left X
    y: int              # Top-left Y
    width: int
    height: int
    center_x: float     # Centroid X
    center_y: float     # Centroid Y
    area: float         # Contour area in pixels
    confidence: float   # 0-1 confidence score


class MosquitoDetector:
    """Detect mosquitoes using background subtraction.

    Supports two subtractor backends (MOG2, KNN) and optional
    frame differencing. Morphological opening/closing/dilation
    removes noise and connects nearby blobs.
    """

    def __init__(self, config):
        self.config = config
        self._frame_count = 0
        self._prev_gray: Optional[np.ndarray] = None

        # Build the background subtractor
        subtractor_type = config.get("detection.subtractor", "mog2")
        if subtractor_type == "mog2":
            self._subtractor = cv2.createBackgroundSubtractorMOG2(
                history=config.get("detection.mog2_history", 500),
                varThreshold=config.get("detection.mog2_var_threshold", 16),
                detectShadows=config.get("detection.mog2_detect_shadows", True),
            )
        elif subtractor_type == "knn":
            self._subtractor = cv2.createBackgroundSubtractorKNN(
                history=config.get("detection.knn_history", 500),
                dist2Threshold=config.get("detection.knn_dist_threshold", 400.0),
                detectShadows=config.get("detection.knn_detect_shadows", True),
            )
        else:
            self._subtractor = cv2.createBackgroundSubtractorMOG2(
                history=500, varThreshold=16, detectShadows=True
            )

        # Morphological kernels
        self._open_kernel = np.ones(
            (config.get("detection.open_kernel", 3),
             config.get("detection.open_kernel", 3)), np.uint8
        )
        self._close_kernel = np.ones(
            (config.get("detection.close_kernel", 5),
             config.get("detection.close_kernel", 5)), np.uint8
        )
        self._dilate_kernel = np.ones(
            (config.get("detection.dilate_kernel", 3),
             config.get("detection.dilate_kernel", 3)), np.uint8
        )

    def detect(self, frame: np.ndarray) -> List[Detection]:
        """Run mosquito detection on a frame.

        Args:
            frame: BGR image (H x W x 3)

        Returns:
            List of Detection objects.
        """
        if frame is None or frame.size == 0:
            return []

        self._frame_count += 1

        use_frame_diff = self.config.get("detection.use_frame_diff", False)

        if use_frame_diff:
            mask = self._detect_frame_diff(frame)
        else:
            mask = self._subtractor.apply(frame)

        # Morphological cleanup
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, self._open_kernel)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, self._close_kernel)
        mask = cv2.dilate(
            mask, self._dilate_kernel,
            iterations=self.config.get("detection.dilate_iterations", 2)
        )

        # Find contours
        contours, _ = cv2.findContours(
            mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
        )

        detections: List[Detection] = []
        min_area = self.config.get("detection.min_area", 5)
        max_area = self.config.get("detection.max_area", 200)
        min_ar = self.config.get("detection.min_aspect_ratio", 0.3)
        max_ar = self.config.get("detection.max_aspect_ratio", 3.0)
        min_solidity = self.config.get("detection.min_solidity", 0.5)

        for contour in contours:
            area = cv2.contourArea(contour)
            if area < min_area or area > max_area:
                continue

            x, y, w, h = cv2.boundingRect(contour)

            # Aspect ratio filter
            if h == 0:
                continue
            aspect_ratio = float(w) / float(h)
            if aspect_ratio < min_ar or aspect_ratio > max_ar:
                continue

            # Solidity filter (area / convex hull area)
            hull = cv2.convexHull(contour)
            hull_area = cv2.contourArea(hull)
            if hull_area == 0:
                continue
            solidity = float(area) / float(hull_area)
            if solidity < min_solidity:
                continue

            # Confidence: heuristic based on area and solidity
            confidence = min(1.0, (solidity * 0.6) + (min(area / 100.0, 1.0) * 0.4))

            cx = x + w / 2.0
            cy = y + h / 2.0

            detections.append(Detection(
                x=x, y=y, width=w, height=h,
                center_x=cx, center_y=cy,
                area=area, confidence=confidence,
            ))

        return detections

    def _detect_frame_diff(self, frame: np.ndarray) -> np.ndarray:
        """Simple frame differencing as a lightweight alternative."""
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        blur_kernel = self.config.get("detection.diff_blur_kernel", 5)
        gray = cv2.GaussianBlur(gray, (blur_kernel, blur_kernel), 0)

        if self._prev_gray is None:
            self._prev_gray = gray
            return np.zeros_like(gray)

        diff = cv2.absdiff(self._prev_gray, gray)
        self._prev_gray = gray.copy()

        threshold = self.config.get("detection.diff_threshold", 25)
        _, mask = cv2.threshold(diff, threshold, 255, cv2.THRESH_BINARY)
        return mask

    def reset(self) -> None:
        """Reset the detector state (background model, frame counter)."""
        self._frame_count = 0
        self._prev_gray = None
        # Re-create subtractor
        subtractor_type = self.config.get("detection.subtractor", "mog2")
        if subtractor_type == "mog2":
            self._subtractor = cv2.createBackgroundSubtractorMOG2(
                history=self.config.get("detection.mog2_history", 500),
                varThreshold=self.config.get("detection.mog2_var_threshold", 16),
                detectShadows=self.config.get("detection.mog2_detect_shadows", True),
            )
        else:
            self._subtractor = cv2.createBackgroundSubtractorKNN(
                history=self.config.get("detection.knn_history", 500),
                dist2Threshold=self.config.get("detection.knn_dist_threshold", 400.0),
                detectShadows=self.config.get("config.knn_detect_shadows", True),
            )

    @property
    def frame_count(self) -> int:
        """Number of frames processed so far."""
        return self._frame_count
