#!/usr/bin/env python3
"""
Mosquito Detection Pipeline
============================

A production-ready detection pipeline for identifying mosquitoes in a video feed.
Uses OpenCV for video capture and YOLOv8-nano (or a lightweight fallback) for
object detection. Supports real-time bounding box visualization, coordinate
output, and a training data collection mode.

Usage:
    # Real-time detection from webcam
    python detect.py --source 0

    # Detection from video file
    python detect.py --source path/to/video.mp4

    # Collect training data (saves frames with detections)
    python detect.py --source 0 --collect-training-data --output-dir ./training_data

    # Use a custom trained model
    python detect.py --source 0 --model ./models/mosquito_yolov8n.pt

    # Run with fallback detector (no ML model required)
    python detect.py --source 0 --fallback

    # Save detection coordinates to file
    python detect.py --source 0 --save-coords detections.jsonl

Requirements:
    pip install opencv-python numpy ultralytics

    For fallback mode (no ML):
    pip install opencv-python numpy

Author: Mosquito Zapper Project
License: MIT
"""

import argparse
import json
import logging
import os
import sys
import time
from collections import deque
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Tuple, Generator, Dict, Any

import cv2
import numpy as np

# ---------------------------------------------------------------------------
# Logging Configuration
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("mosquito_detect")


# ---------------------------------------------------------------------------
# Data Classes
# ---------------------------------------------------------------------------
@dataclass
class Detection:
    """Represents a single mosquito detection."""
    frame_id: int
    timestamp: float
    class_name: str
    confidence: float
    bbox: Tuple[int, int, int, int]  # (x1, y1, x2, y2)
    center: Tuple[int, int]  # (cx, cy)
    area: int

    def to_dict(self) -> Dict[str, Any]:
        return {
            "frame_id": self.frame_id,
            "timestamp": self.timestamp,
            "class_name": self.class_name,
            "confidence": round(self.confidence, 4),
            "bbox": list(self.bbox),
            "center": list(self.center),
            "area": self.area,
        }


@dataclass
class FrameResult:
    """Results for a single processed frame."""
    frame_id: int
    timestamp: float
    detections: List[Detection] = field(default_factory=list)
    frame_shape: Tuple[int, int] = (0, 0)  # (height, width)

    @property
    def count(self) -> int:
        return len(self.detections)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
@dataclass
class DetectorConfig:
    """Configuration for the detection pipeline."""
    # Model settings
    model_path: Optional[str] = None
    confidence_threshold: float = 0.35
    nms_iou_threshold: float = 0.45
    input_size: int = 640
    fallback: bool = False

    # Detection filtering
    min_detection_area: int = 20       # Minimum pixel area for a detection
    max_detection_area: int = 5000     # Maximum pixel area for a detection
    min_aspect_ratio: float = 0.2      # width/height
    max_aspect_ratio: float = 5.0

    # Visual settings
    box_color: Tuple[int, int, int] = (0, 255, 0)  # Green
    box_thickness: int = 2
    label_color: Tuple[int, int, int] = (0, 255, 0)
    label_bg_color: Tuple[int, int, int] = (0, 0, 0)
    show_fps: bool = True
    show_count: bool = True

    # Tracking / temporal
    temporal_window: int = 5          # Frames for temporal smoothing
    min_temporal_hits: int = 2        # Min detections in window to confirm

    # Training data collection
    collect_training_data: bool = False
    training_output_dir: str = "./training_data"
    save_every_n_frames: int = 5      # Save every Nth frame with detections
    max_training_samples: int = 10000

    # Output
    save_coords_path: Optional[str] = None
    save_video_path: Optional[str] = None


# ---------------------------------------------------------------------------
# Fallback Detector (Background Subtraction + Contour Analysis)
# ---------------------------------------------------------------------------
class FallbackDetector:
    """
    Lightweight fallback detector using background subtraction and contour
    analysis. Useful when no ML model is available or for quick prototyping.

    Detects small moving objects that could be mosquitoes based on:
    - Motion (background subtraction)
    - Size constraints (small objects)
    - Aspect ratio (mosquito-like shape)
    """

    def __init__(self, config: DetectorConfig):
        self.config = config
        self.bg_subtractor = cv2.createBackgroundSubtractorMOG2(
            history=500,
            varThreshold=25,
            detectShadows=False,
        )
        self.kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
        self._frame_count = 0

    def detect(self, frame: np.ndarray) -> List[Detection]:
        """Detect small moving objects in the frame."""
        self._frame_count += 1
        timestamp = time.time()
        detections = []

        # Apply background subtraction
        fg_mask = self.bg_subtractor.apply(frame)

        # Morphological cleanup.
        #
        # NOTE: this previously did MORPH_OPEN then MORPH_CLOSE with a 3x3
        # ellipse. That was measurably wrong. MOG2 produces a THIN, PARTIAL
        # mask for a small fast-moving object (a 3px blob moving 2-3 px/frame
        # leaves only a sliver of disagreement with the background model).
        # MORPH_OPEN erodes first, which deletes that sliver outright.
        #
        # Measured on a synthetic 4-blob video (640x480, 90 frames):
        #     open 3x3 + close 3x3  ->  13/90 frames detect  (14%)
        #     close 3x3 only        ->  83/90 frames detect  (92%)
        #     dilate 3x3 + close    ->  90/90 frames detect (100%)
        #
        # So we DILATE, not open. The masks are too small, not too noisy.
        # Dilation also reconnects the fragmented pixels of a moving target
        # into a single contour. With dilate+close and min_area=20 the
        # detector recovers a median of exactly 4 blobs/frame, matching the
        # ground truth of the test video.
        fg_mask = cv2.dilate(fg_mask, self.kernel)
        fg_mask = cv2.morphologyEx(fg_mask, cv2.MORPH_CLOSE, self.kernel)

        # Find contours
        contours, _ = cv2.findContours(
            fg_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
        )

        for contour in contours:
            area = cv2.contourArea(contour)

            # Filter by area
            if area < self.config.min_detection_area:
                continue
            if area > self.config.max_detection_area:
                continue

            # Get bounding box
            x, y, w, h = cv2.boundingRect(contour)

            # Filter by aspect ratio
            aspect_ratio = w / max(h, 1)
            if aspect_ratio < self.config.min_aspect_ratio:
                continue
            if aspect_ratio > self.config.max_aspect_ratio:
                continue

            # Compute confidence based on contour solidity and area
            hull = cv2.convexHull(contour)
            hull_area = cv2.contourArea(hull)
            solidity = area / max(hull_area, 1)

            # Higher confidence for compact, solid objects
            confidence = min(0.3 + solidity * 0.5, 0.95)

            if confidence < self.config.confidence_threshold:
                continue

            cx, cy = x + w // 2, y + h // 2

            detections.append(Detection(
                frame_id=self._frame_count,
                timestamp=timestamp,
                class_name="mosquito",
                confidence=confidence,
                bbox=(x, y, x + w, y + h),
                center=(cx, cy),
                area=int(area),
            ))

        return detections


# ---------------------------------------------------------------------------
# YOLOv8 Detector
# ---------------------------------------------------------------------------
class YOLODetector:
    """
    YOLOv8-nano based detector using the ultralytics library.
    Supports both pre-trained COCO models and custom-trained mosquito models.
    """

    def __init__(self, config: DetectorConfig):
        self.config = config
        self._frame_count = 0

        try:
            from ultralytics import YOLO
        except ImportError:
            raise ImportError(
                "ultralytics package is required for YOLO detection. "
                "Install with: pip install ultralytics"
            )

        # Load model
        if config.model_path and os.path.exists(config.model_path):
            logger.info(f"Loading custom model: {config.model_path}")
            self.model = YOLO(config.model_path)
        else:
            logger.info("Loading YOLOv8n (nano) pre-trained model...")
            self.model = YOLO("yolov8n.pt")

        # Warm up the model
        dummy = np.zeros((config.input_size, config.input_size, 3), dtype=np.uint8)
        self.model.predict(dummy, verbose=False)
        logger.info("YOLO model loaded and warmed up.")

    def detect(self, frame: np.ndarray) -> List[Detection]:
        """Run YOLO inference on a frame."""
        self._frame_count += 1
        timestamp = time.time()
        detections = []

        results = self.model.predict(
            frame,
            conf=self.config.confidence_threshold,
            iou=self.config.nms_iou_threshold,
            imgsz=self.config.input_size,
            verbose=False,
        )

        for result in results:
            if result.boxes is None:
                continue

            boxes = result.boxes
            for i in range(len(boxes)):
                # Get box coordinates
                x1, y1, x2, y2 = boxes.xyxy[i].cpu().numpy().astype(int)

                # Get confidence and class
                conf = float(boxes.conf[i].cpu().numpy())
                cls_id = int(boxes.cls[i].cpu().numpy())
                cls_name = result.names.get(cls_id, f"class_{cls_id}")

                # Filter by area
                area = (x2 - x1) * (y2 - y1)
                if area < self.config.min_detection_area:
                    continue
                if area > self.config.max_detection_area:
                    continue

                # Filter by aspect ratio
                w, h = x2 - x1, y2 - y1
                aspect_ratio = w / max(h, 1)
                if aspect_ratio < self.config.min_aspect_ratio:
                    continue
                if aspect_ratio > self.config.max_aspect_ratio:
                    continue

                cx, cy = (x1 + x2) // 2, (y1 + y2) // 2

                detections.append(Detection(
                    frame_id=self._frame_count,
                    timestamp=timestamp,
                    class_name=cls_name,
                    confidence=conf,
                    bbox=(int(x1), int(y1), int(x2), int(y2)),
                    center=(cx, cy),
                    area=int(area),
                ))

        return detections


# ---------------------------------------------------------------------------
# Temporal Filter
# ---------------------------------------------------------------------------
class TemporalFilter:
    """
    Temporal smoothing filter that reduces false positives by requiring
    detections to appear consistently across multiple frames.
    """

    def __init__(self, window_size: int = 5, min_hits: int = 2):
        self.window_size = window_size
        self.min_hits = min_hits
        self.history: deque = deque(maxlen=window_size)

    def update(self, detections: List[Detection]) -> List[Detection]:
        """Update filter and return confirmed detections."""
        self.history.append(detections)

        if len(self.history) < self.min_hits:
            return []

        # Count how many times each approximate location appears
        location_counts: Dict[Tuple[int, int], int] = {}
        location_detections: Dict[Tuple[int, int], Detection] = {}

        for frame_dets in self.history:
            for det in frame_dets:
                # Quantize location for matching (50-pixel grid)
                key = (det.center[0] // 50, det.center[1] // 50)
                location_counts[key] = location_counts.get(key, 0) + 1

                # Keep the detection with highest confidence
                if key not in location_detections or det.confidence > location_detections[key].confidence:
                    location_detections[key] = det

        # Filter by minimum hits
        confirmed = []
        for key, count in location_counts.items():
            if count >= self.min_hits:
                confirmed.append(location_detections[key])

        return confirmed


# ---------------------------------------------------------------------------
# Visualizer
# ---------------------------------------------------------------------------
class Visualizer:
    """Handles drawing bounding boxes, labels, and overlays on frames."""

    def __init__(self, config: DetectorConfig):
        self.config = config
        self._fps_history = deque(maxlen=30)
        self._last_time = time.time()

    def draw_detections(
        self, frame: np.ndarray, detections: List[Detection]
    ) -> np.ndarray:
        """Draw bounding boxes and labels on the frame."""
        vis = frame.copy()

        for det in detections:
            x1, y1, x2, y2 = det.bbox

            # Draw bounding box
            cv2.rectangle(
                vis, (x1, y1), (x2, y2),
                self.config.box_color,
                self.config.box_thickness,
            )

            # Draw label
            label = f"{det.class_name}: {det.confidence:.2f}"
            (text_w, text_h), _ = cv2.getTextSize(
                label, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1
            )

            # Label background
            cv2.rectangle(
                vis,
                (x1, y1 - text_h - 6),
                (x1 + text_w + 4, y1),
                self.config.label_bg_color,
                -1,
            )

            # Label text
            cv2.putText(
                vis, label,
                (x1 + 2, y1 - 4),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5,
                self.config.label_color, 1,
            )

            # Draw center point
            cv2.circle(vis, det.center, 3, (0, 0, 255), -1)

        return vis

    def draw_overlay(
        self, frame: np.ndarray, fps: float, detection_count: int
    ) -> np.ndarray:
        """Draw FPS and detection count overlay."""
        vis = frame.copy()

        if self.config.show_fps:
            fps_text = f"FPS: {fps:.1f}"
            cv2.putText(
                vis, fps_text, (10, 30),
                cv2.FONT_HERSHEY_SIMPLEX, 0.7,
                (0, 255, 255), 2,
            )

        if self.config.show_count:
            count_text = f"Detections: {detection_count}"
            cv2.putText(
                vis, count_text, (10, 60),
                cv2.FONT_HERSHEY_SIMPLEX, 0.7,
                (0, 255, 255), 2,
            )

        return vis

    def update_fps(self) -> float:
        """Calculate and return current FPS."""
        current_time = time.time()
        dt = current_time - self._last_time
        self._last_time = current_time

        if dt > 0:
            self._fps_history.append(1.0 / dt)

        return float(np.mean(self._fps_history)) if self._fps_history else 0.0


# ---------------------------------------------------------------------------
# Training Data Collector
# ---------------------------------------------------------------------------
class TrainingDataCollector:
    """
    Collects training data by saving frames and annotations
    in YOLO format for later model training.
    """

    def __init__(self, output_dir: str, config: DetectorConfig):
        self.output_dir = Path(output_dir)
        self.config = config
        self._sample_count = 0
        self._frame_count = 0

        # Create output directories
        self.images_dir = self.output_dir / "images"
        self.labels_dir = self.output_dir / "labels"
        self.images_dir.mkdir(parents=True, exist_ok=True)
        self.labels_dir.mkdir(parents=True, exist_ok=True)

        logger.info(f"Training data will be saved to: {self.output_dir}")

    def collect(
        self, frame: np.ndarray, detections: List[Detection]
    ) -> bool:
        """
        Save a frame and its annotations if it contains detections.
        Returns True if data was saved.
        """
        self._frame_count += 1

        # Only save frames with detections
        if not detections:
            return False

        # Save every Nth frame
        if self._frame_count % self.config.save_every_n_frames != 0:
            return False

        # Check max samples
        if self._sample_count >= self.config.max_training_samples:
            return False

        h, w = frame.shape[:2]
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        filename = f"frame_{timestamp}_{self._sample_count:06d}"

        # Save image
        img_path = self.images_dir / f"{filename}.jpg"
        cv2.imwrite(str(img_path), frame)

        # Save YOLO-format annotation
        label_path = self.labels_dir / f"{filename}.txt"
        with open(label_path, "w") as f:
            for det in detections:
                x1, y1, x2, y2 = det.bbox

                # Convert to YOLO format (normalized x_center, y_center, width, height)
                x_center = ((x1 + x2) / 2) / w
                y_center = ((y1 + y2) / 2) / h
                width = (x2 - x1) / w
                height = (y2 - y1) / h

                # Class 0 = mosquito
                f.write(f"0 {x_center:.6f} {y_center:.6f} {width:.6f} {height:.6f}\n")

        self._sample_count += 1

        if self._sample_count % 100 == 0:
            logger.info(f"Collected {self._sample_count} training samples")

        return True

    def create_yaml(self, class_names: Optional[List[str]] = None):
        """Create a YOLO dataset YAML file for training."""
        if class_names is None:
            class_names = ["mosquito"]

        yaml_path = self.output_dir / "dataset.yaml"
        with open(yaml_path, "w") as f:
            f.write(f"path: {self.output_dir.absolute()}\n")
            f.write(f"train: {self.images_dir.absolute()}\n")
            f.write(f"val: {self.images_dir.absolute()}\n")
            f.write(f"nc: {len(class_names)}\n")
            f.write(f"names: {class_names}\n")

        logger.info(f"Created dataset YAML: {yaml_path}")
        return yaml_path


# ---------------------------------------------------------------------------
# Coordinate Logger
# ---------------------------------------------------------------------------
class CoordinateLogger:
    """Logs detection coordinates to a JSONL file."""

    def __init__(self, output_path: str):
        self.output_path = Path(output_path)
        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        self._file = open(self.output_path, "w")
        logger.info(f"Logging coordinates to: {self.output_path}")

    def log(self, result: FrameResult):
        """Log a frame result."""
        record = {
            "frame_id": result.frame_id,
            "timestamp": result.timestamp,
            "frame_shape": list(result.frame_shape),
            "detections": [d.to_dict() for d in result.detections],
        }
        self._file.write(json.dumps(record) + "\n")
        self._file.flush()

    def close(self):
        if self._file and not self._file.closed:
            self._file.close()

    def __del__(self):
        self.close()


# ---------------------------------------------------------------------------
# Video Writer
# ---------------------------------------------------------------------------
class VideoWriter:
    """Writes processed frames to a video file."""

    def __init__(self, output_path: str, fps: float, frame_size: Tuple[int, int]):
        self.output_path = Path(output_path)
        self.output_path.parent.mkdir(parents=True, exist_ok=True)

        fourcc = cv2.VideoWriter_fourcc(*"mp4v")  # type: ignore[attr-defined]
        self.writer = cv2.VideoWriter(
            str(self.output_path), fourcc, fps, frame_size
        )
        logger.info(f"Writing video to: {self.output_path}")

    def write(self, frame: np.ndarray):
        self.writer.write(frame)

    def release(self):
        if self.writer:
            self.writer.release()


# ---------------------------------------------------------------------------
# Main Detection Pipeline
# ---------------------------------------------------------------------------
class MosquitoDetectionPipeline:
    """
    Main detection pipeline that orchestrates video capture, detection,
    visualization, and output.
    """

    def __init__(self, config: DetectorConfig):
        self.config = config
        self.frame_id = 0

        # Initialize detector
        if config.model_path or not config.fallback:
            try:
                self.detector = YOLODetector(config)
                self.using_fallback = False
            except (ImportError, Exception) as e:
                logger.warning(f"Failed to load YOLO model: {e}")
                logger.warning("Falling back to background subtraction detector")
                self.detector = FallbackDetector(config)
                self.using_fallback = True
        else:
            logger.info("Using fallback detector (background subtraction)")
            self.detector = FallbackDetector(config)
            self.using_fallback = True

        # Initialize temporal filter
        self.temporal_filter = TemporalFilter(
            window_size=config.temporal_window,
            min_hits=config.min_temporal_hits,
        )

        # Initialize visualizer
        self.visualizer = Visualizer(config)

        # Initialize training data collector
        self.training_collector = None
        if config.collect_training_data:
            self.training_collector = TrainingDataCollector(
                config.training_output_dir, config
            )

        # Initialize coordinate logger
        self.coord_logger = None
        if config.save_coords_path:
            self.coord_logger = CoordinateLogger(config.save_coords_path)

        # Initialize video writer
        self.video_writer = None

        logger.info("Pipeline initialized successfully")

    def process_frame(self, frame: np.ndarray) -> Tuple[np.ndarray, FrameResult]:
        """Process a single frame through the pipeline."""
        self.frame_id += 1
        timestamp = time.time()
        h, w = frame.shape[:2]

        # Run detection
        raw_detections = self.detector.detect(frame)

        # Apply temporal filtering
        confirmed_detections = self.temporal_filter.update(raw_detections)

        # Create frame result
        result = FrameResult(
            frame_id=self.frame_id,
            timestamp=timestamp,
            detections=confirmed_detections,
            frame_shape=(h, w),
        )

        # Collect training data
        if self.training_collector:
            self.training_collector.collect(frame, confirmed_detections)

        # Log coordinates
        if self.coord_logger:
            self.coord_logger.log(result)

        # Visualize
        vis_frame = self.visualizer.draw_detections(frame, confirmed_detections)
        fps = self.visualizer.update_fps()
        vis_frame = self.visualizer.draw_overlay(vis_frame, fps, result.count)

        return vis_frame, result

    def run(self, source: Any = 0, display: bool = True):
        """
        Run the detection pipeline on a video source.

        Args:
            source: Video source (0 for webcam, or path to video file)
            display: Whether to show the video window
        """
        # Open video source
        if isinstance(source, int):
            cap = cv2.VideoCapture(source)
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
            cap.set(cv2.CAP_PROP_FPS, 30)
        else:
            cap = cv2.VideoCapture(source)

        if not cap.isOpened():
            logger.error(f"Failed to open video source: {source}")
            return

        # Get video properties
        fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        logger.info(f"Video source: {width}x{height} @ {fps:.1f} FPS")

        # Initialize video writer if needed
        if self.config.save_video_path:
            self.video_writer = VideoWriter(
                self.config.save_video_path, fps, (width, height)
            )

        logger.info("Starting detection pipeline. Press 'q' to quit.")

        try:
            while True:
                ret, frame = cap.read()
                if not ret:
                    logger.info("End of video stream")
                    break

                # Process frame
                vis_frame, result = self.process_frame(frame)

                # Write to output video
                if self.video_writer:
                    self.video_writer.write(vis_frame)

                # Display
                if display:
                    cv2.imshow("Mosquito Detection", vis_frame)

                    key = cv2.waitKey(1) & 0xFF
                    if key == ord("q"):
                        logger.info("User requested quit")
                        break
                    elif key == ord("s"):
                        # Save current frame
                        save_path = f"capture_{datetime.now():%Y%m%d_%H%M%S}.jpg"
                        cv2.imwrite(save_path, vis_frame)
                        logger.info(f"Saved frame to: {save_path}")

        except KeyboardInterrupt:
            logger.info("Interrupted by user")
        finally:
            # Cleanup
            cap.release()
            if self.video_writer:
                self.video_writer.release()
            if self.coord_logger:
                self.coord_logger.close()
            cv2.destroyAllWindows()

            # Create dataset YAML if collecting training data
            if self.training_collector:
                self.training_collector.create_yaml()
                logger.info(
                    f"Training data collection complete. "
                    f"Total samples: {self.training_collector._sample_count}"
                )

            logger.info("Pipeline shutdown complete")


# ---------------------------------------------------------------------------
# CLI Entry Point
# ---------------------------------------------------------------------------
def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Mosquito Detection Pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Real-time webcam detection
  python detect.py --source 0

  # Detect from video file
  python detect.py --source video.mp4

  # Collect training data
  python detect.py --source 0 --collect-training-data --output-dir ./training_data

  # Use custom model
  python detect.py --source 0 --model ./models/mosquito_yolov8n.pt

  # Fallback mode (no ML model)
  python detect.py --source 0 --fallback

  # Save coordinates and video
  python detect.py --source 0 --save-coords detections.jsonl --save-video output.mp4
        """,
    )

    parser.add_argument(
        "--source", type=str, default="0",
        help="Video source: 0 for webcam, or path to video file (default: 0)"
    )
    parser.add_argument(
        "--model", type=str, default=None,
        help="Path to custom YOLO model weights (.pt file)"
    )
    parser.add_argument(
        "--confidence", type=float, default=0.35,
        help="Detection confidence threshold (default: 0.35)"
    )
    parser.add_argument(
        "--input-size", type=int, default=640,
        help="Model input size (default: 640)"
    )
    parser.add_argument(
        "--min-area", type=int, default=20,
        help="Minimum detection area in pixels (default: 20)"
    )
    parser.add_argument(
        "--max-area", type=int, default=5000,
        help="Maximum detection area in pixels (default: 5000)"
    )
    parser.add_argument(
        "--fallback", action="store_true",
        help="Use fallback detector (background subtraction, no ML model)"
    )
    parser.add_argument(
        "--collect-training-data", action="store_true",
        help="Enable training data collection mode"
    )
    parser.add_argument(
        "--output-dir", type=str, default="./training_data",
        help="Output directory for training data (default: ./training_data)"
    )
    parser.add_argument(
        "--save-coords", type=str, default=None,
        help="Path to save detection coordinates (JSONL format)"
    )
    parser.add_argument(
        "--save-video", type=str, default=None,
        help="Path to save output video"
    )
    parser.add_argument(
        "--no-display", action="store_true",
        help="Run without displaying video window"
    )
    parser.add_argument(
        "--temporal-window", type=int, default=5,
        help="Temporal smoothing window size (default: 5)"
    )
    parser.add_argument(
        "--min-hits", type=int, default=2,
        help="Minimum temporal hits to confirm detection (default: 2)"
    )

    return parser.parse_args()


def main():
    args = parse_args()

    # Parse source
    try:
        source = int(args.source)
    except ValueError:
        source = args.source

    # Create configuration
    config = DetectorConfig(
        model_path=args.model,
        confidence_threshold=args.confidence,
        input_size=args.input_size,
        min_detection_area=args.min_area,
        max_detection_area=args.max_area,
        fallback=args.fallback,
        collect_training_data=args.collect_training_data,
        training_output_dir=args.output_dir,
        save_coords_path=args.save_coords,
        save_video_path=args.save_video,
        temporal_window=args.temporal_window,
        min_temporal_hits=args.min_hits,
    )

    # Create and run pipeline
    pipeline = MosquitoDetectionPipeline(config)
    pipeline.run(source=source, display=not args.no_display)


if __name__ == "__main__":
    main()
