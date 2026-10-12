#!/usr/bin/env python3
"""
Mosquito Detection Model Training Script
=========================================

Trains a YOLOv8-nano model on custom mosquito detection data.
Expects training data collected by `detect.py --collect-training-data`
in YOLO format (images/ and labels/ directories with a dataset.yaml file).

Usage:
    # Train from collected data
    python train.py --data ./training_data/dataset.yaml --epochs 50

    # Fine-tune from pre-trained weights
    python train.py --data ./training_data/dataset.yaml --epochs 100 --weights yolov8n.pt

    # Resume from checkpoint
    python train.py --data ./training_data/dataset.yaml --resume ./runs/train/weights/last.pt

    # Dry-run (validate data without training)
    python train.py --data ./training_data/dataset.yaml --dry-run

Requirements:
    pip install ultralytics

Author: Mosquito Zapper Project
License: MIT
"""

import argparse
import logging
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Tuple

import yaml

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("mosquito_train")


# ---------------------------------------------------------------------------
# Data Validation
# ---------------------------------------------------------------------------
def validate_dataset(data_dir: str) -> Tuple[bool, List[str]]:
    """
    Validate a YOLO-format dataset directory.

    Returns:
        (is_valid, list_of_issues)
    """
    issues = []
    data_path = Path(data_dir)

    if not data_path.exists():
        issues.append(f"Data directory does not exist: {data_path}")
        return False, issues

    images_dir = data_path / "images"
    labels_dir = data_path / "labels"
    yaml_path = data_path / "dataset.yaml"

    # Check directories
    if not images_dir.exists():
        issues.append(f"Missing images directory: {images_dir}")
    if not labels_dir.exists():
        issues.append(f"Missing labels directory: {labels_dir}")
    if not yaml_path.exists():
        issues.append(f"Missing dataset.yaml: {yaml_path}")

    if issues:
        return False, issues

    # Count files
    image_files = list(images_dir.glob("*.jpg")) + list(images_dir.glob("*.png"))
    label_files = list(labels_dir.glob("*.txt"))

    if len(image_files) == 0:
        issues.append("No image files found in images/ directory")
    if len(label_files) == 0:
        issues.append("No label files found in labels/ directory")

    # Check image-label correspondence
    image_stems = {f.stem for f in image_files}
    label_stems = {f.stem for f in label_files}
    missing_labels = image_stems - label_stems
    orphan_labels = label_stems - image_stems

    if missing_labels:
        issues.append(f"{len(missing_labels)} images have no corresponding label file")
    if orphan_labels:
        issues.append(f"{len(orphan_labels)} label files have no corresponding image")

    # Validate YOLO label format
    invalid_labels = 0
    empty_labels = 0
    total_boxes = 0

    for label_file in label_files:
        with open(label_file, "r") as f:
            lines = f.readlines()

        if not lines:
            empty_labels += 1
            continue

        for line in lines:
            parts = line.strip().split()
            if len(parts) != 5:
                invalid_labels += 1
                continue

            try:
                cls_id = int(parts[0])
                x_center, y_center, width, height = map(float, parts[1:])

                if not (0 <= x_center <= 1 and 0 <= y_center <= 1):
                    invalid_labels += 1
                if not (0 < width <= 1 and 0 < height <= 1):
                    invalid_labels += 1
                if cls_id < 0:
                    invalid_labels += 1

                total_boxes += 1
            except ValueError:
                invalid_labels += 1

    if invalid_labels > 0:
        issues.append(f"{invalid_labels} invalid label entries found")
    if empty_labels > 0:
        logger.warning(f"{empty_labels} label files are empty (background images)")

    # Check dataset.yaml
    try:
        with open(yaml_path, "r") as f:
            config = yaml.safe_load(f)

        required_keys = ["path", "train", "val", "nc", "names"]
        for key in required_keys:
            if key not in config:
                issues.append(f"Missing key in dataset.yaml: {key}")
    except Exception as e:
        issues.append(f"Failed to parse dataset.yaml: {e}")

    is_valid = len(issues) == 0
    logger.info(f"Dataset validation: {'PASSED' if is_valid else 'FAILED'}")
    logger.info(f"  Images: {len(image_files)}, Labels: {len(label_files)}")
    logger.info(f"  Total bounding boxes: {total_boxes}")
    logger.info(f"  Empty background images: {empty_labels}")

    return is_valid, issues


def split_dataset(data_dir: str, train_ratio: float = 0.8, val_ratio: float = 0.2):
    """
    Split dataset into train/val sets if not already split.
    Creates symlinks or copies files into train/ and val/ subdirectories.
    """
    data_path = Path(data_dir)
    images_dir = data_path / "images"
    labels_dir = data_path / "labels"

    if not images_dir.exists():
        logger.error("No images directory found")
        return

    image_files = sorted(
        list(images_dir.glob("*.jpg")) + list(images_dir.glob("*.png"))
    )

    if len(image_files) == 0:
        logger.error("No images found")
        return

    # Shuffle deterministically
    import random
    random.seed(42)
    random.shuffle(image_files)

    n_total = len(image_files)
    n_train = int(n_total * train_ratio)
    n_val = n_total - n_train

    train_images = image_files[:n_train]
    val_images = image_files[n_train:]

    # Create split directories
    for split in ["train", "val"]:
        (data_path / "images" / split).mkdir(parents=True, exist_ok=True)
        (data_path / "labels" / split).mkdir(parents=True, exist_ok=True)

    # Copy/link files
    for img_file in train_images:
        dst = data_path / "images" / "train" / img_file.name
        if not dst.exists():
            shutil.copy2(img_file, dst)
        label_file = labels_dir / f"{img_file.stem}.txt"
        if label_file.exists():
            dst_label = data_path / "labels" / "train" / label_file.name
            if not dst_label.exists():
                shutil.copy2(label_file, dst_label)

    for img_file in val_images:
        dst = data_path / "images" / "val" / img_file.name
        if not dst.exists():
            shutil.copy2(img_file, dst)
        label_file = labels_dir / f"{img_file.stem}.txt"
        if label_file.exists():
            dst_label = data_path / "labels" / "val" / label_file.name
            if not dst_label.exists():
                shutil.copy2(label_file, dst_label)

    logger.info(f"Dataset split: {n_train} train, {n_val} val")

    # Update dataset.yaml
    yaml_path = data_path / "dataset.yaml"
    if yaml_path.exists():
        with open(yaml_path, "r") as f:
            config = yaml.safe_load(f)

        config["train"] = str((data_path / "images" / "train").absolute())
        config["val"] = str((data_path / "images" / "val").absolute())

        with open(yaml_path, "w") as f:
            yaml.dump(config, f, default_flow_style=False)

        logger.info("Updated dataset.yaml with train/val splits")


# ---------------------------------------------------------------------------
# Model Training
# ---------------------------------------------------------------------------
def train_model(
    data_yaml: str,
    epochs: int = 50,
    imgsz: int = 640,
    batch: int = 16,
    weights: Optional[str] = "yolov8n.pt",
    output_dir: str = "./runs/train",
    device: str = "",
    patience: int = 20,
    resume: Optional[str] = None,
    **kwargs,
):
    """
    Train a YOLOv8-nano model on mosquito detection data.

    Args:
        data_yaml: Path to dataset.yaml file
        epochs: Number of training epochs
        imgsz: Input image size
        batch: Batch size
        weights: Pre-trained weights to start from
        output_dir: Output directory for training results
        device: Device to use ('' for auto, '0' for GPU 0, 'cpu' for CPU)
        patience: Early stopping patience
        resume: Path to checkpoint to resume from
        **kwargs: Additional arguments passed to model.train()
    """
    try:
        from ultralytics import YOLO
    except ImportError:
        logger.error("ultralytics package is required. Install: pip install ultralytics")
        sys.exit(1)

    # Load model
    if resume:
        logger.info(f"Resuming training from: {resume}")
        model = YOLO(resume)
    elif weights:
        logger.info(f"Loading pre-trained weights: {weights}")
        model = YOLO(weights)
    else:
        logger.info("Starting from scratch (yolov8n architecture)")
        model = YOLO("yolov8n.yaml")

    # Training arguments
    train_args = {
        "data": data_yaml,
        "epochs": epochs,
        "imgsz": imgsz,
        "batch": batch,
        "project": output_dir,
        "name": f"mosquito_yolov8n_{datetime.now():%Y%m%d_%H%M%S}",
        "patience": patience,
        "device": device,
        "workers": 4,
        "optimizer": "AdamW",
        "lr0": 0.001,
        "lrf": 0.01,
        "momentum": 0.937,
        "weight_decay": 0.0005,
        "warmup_epochs": 3,
        "warmup_momentum": 0.8,
        "warmup_bias_lr": 0.1,
        "box": 7.5,
        "cls": 0.5,
        "dfl": 1.5,
        "label_smoothing": 0.0,
        "nbs": 64,
        "hsv_h": 0.015,
        "hsv_s": 0.7,
        "hsv_v": 0.4,
        "degrees": 0.0,
        "translate": 0.1,
        "scale": 0.5,
        "shear": 0.0,
        "perspective": 0.0,
        "flipud": 0.0,
        "fliplr": 0.5,
        "mosaic": 1.0,
        "mixup": 0.0,
        "copy_paste": 0.0,
        "verbose": True,
        "seed": 42,
        "deterministic": True,
    }

    # Override with user-provided kwargs
    train_args.update(kwargs)

    logger.info("Starting training...")
    logger.info(f"  Epochs: {epochs}, Batch: {batch}, Image size: {imgsz}")
    logger.info(f"  Device: {device or 'auto'}")

    # Run training
    results = model.train(**train_args)

    logger.info("Training complete!")
    logger.info(f"Results saved to: {output_dir}")

    # Run validation
    logger.info("Running validation...")
    metrics = model.val(data=data_yaml, imgsz=imgsz, batch=batch)
    logger.info(f"Validation mAP50: {metrics.box.map50:.4f}")
    logger.info(f"Validation mAP50-95: {metrics.box.map:.4f}")

    return model, results, metrics


def export_model(model_path: str, output_format: str = "onnx", imgsz: int = 640):
    """
    Export a trained model to deployment format.

    Args:
        model_path: Path to trained .pt weights
        output_format: Export format ('onnx', 'torchscript', 'tflite', 'engine')
        imgsz: Input image size for export
    """
    try:
        from ultralytics import YOLO
    except ImportError:
        logger.error("ultralytics package is required")
        sys.exit(1)

    model = YOLO(model_path)

    logger.info(f"Exporting model to {output_format} format...")
    export_path = model.export(format=output_format, imgsz=imgsz, dynamic=True)
    logger.info(f"Model exported to: {export_path}")

    return export_path


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Train YOLOv8-nano for mosquito detection",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Validate dataset
  python train.py --data ./training_data/dataset.yaml --dry-run

  # Train from scratch with pre-trained weights
  python train.py --data ./training_data/dataset.yaml --epochs 50

  # Fine-tune with custom settings
  python train.py --data ./training_data/dataset.yaml --epochs 100 --batch 32 --lr 0.0005

  # Resume training
  python train.py --data ./training_data/dataset.yaml --resume ./runs/train/weights/last.pt

  # Export to ONNX
  python train.py --export ./runs/train/weights/best.pt --format onnx
        """,
    )

    parser.add_argument("--data", type=str, required=True, help="Path to dataset.yaml")
    parser.add_argument("--epochs", type=int, default=50, help="Number of training epochs")
    parser.add_argument("--imgsz", type=int, default=640, help="Input image size")
    parser.add_argument("--batch", type=int, default=16, help="Batch size")
    parser.add_argument("--weights", type=str, default="yolov8n.pt", help="Pre-trained weights")
    parser.add_argument("--output-dir", type=str, default="./runs/train", help="Output directory")
    parser.add_argument("--device", type=str, default="", help="Device ('', '0', 'cpu', 'mps')")
    parser.add_argument("--patience", type=int, default=20, help="Early stopping patience")
    parser.add_argument("--resume", type=str, default=None, help="Resume from checkpoint")
    parser.add_argument("--lr", type=float, default=0.001, help="Initial learning rate")
    parser.add_argument("--dry-run", action="store_true", help="Validate data only, no training")
    parser.add_argument("--split", action="store_true", help="Split dataset into train/val before training")
    parser.add_argument("--export", type=str, default=None, help="Export trained model (provide path to .pt)")
    parser.add_argument("--format", type=str, default="onnx", choices=["onnx", "torchscript", "tflite", "engine"], help="Export format")

    return parser.parse_args()


def main():
    args = parse_args()

    # Export mode
    if args.export:
        export_model(args.export, args.format, args.imgsz)
        return

    # Validate dataset
    is_valid, issues = validate_dataset(str(Path(args.data).parent))

    if not is_valid:
        logger.error("Dataset validation failed:")
        for issue in issues:
            logger.error(f"  - {issue}")
        sys.exit(1)

    if args.dry_run:
        logger.info("Dry-run complete. Dataset is valid.")
        return

    # Split dataset if requested
    if args.split:
        split_dataset(str(Path(args.data).parent))

    # Train
    model, results, metrics = train_model(
        data_yaml=args.data,
        epochs=args.epochs,
        imgsz=args.imgsz,
        batch=args.batch,
        weights=args.weights,
        output_dir=args.output_dir,
        device=args.device,
        patience=args.patience,
        resume=args.resume,
        lr0=args.lr,
    )

    logger.info("Training pipeline complete!")
    logger.info(f"Best model: {args.output_dir}/weights/best.pt")
    logger.info(f"Last model: {args.output_dir}/weights/last.pt")


if __name__ == "__main__":
    main()
