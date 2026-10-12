# Mosquito Laser Zapper

An automated mosquito detection and elimination system using computer vision, Kalman filter tracking, pan-tilt servo control, and laser targeting.

## Architecture

```
┌─────────────┐     ┌──────────────┐     ┌─────────────┐     ┌──────────────┐
│   Camera    │────▶│  Detector    │────▶│  Tracker    │────▶│   Servo      │
│  (OpenCV)   │     │ (Bg Sub/MOG2)│     │(Kalman Filt)│     │(Pan-Tilt)    │
└─────────────┘     └──────────────┘     └─────────────┘     └──────────────┘
                                               │                    │
                                               ▼                    ▼
                                        ┌──────────────┐     ┌──────────────┐
                                        │   Laser      │     │   Web        │
                                        │  Controller  │     │  Dashboard   │
                                        │  + Safety    │     │  (Flask)     │
                                        └──────────────┘     └──────────────┘
```

## Components

| Module | File | Description |
|--------|------|-------------|
| **Config** | `config.py` | YAML-based configuration with dot-notation access |
| **Detector** | `detector.py` | Background subtraction (MOG2/KNN) or frame differencing |
| **Tracker** | `tracker.py` | Kalman filter with nearest-neighbor association |
| **Servo** | `servo_controller.py` | Pan-tilt control with smoothing and rate limiting |
| **Laser** | `laser_controller.py` | Laser control with 10 safety interlocks |
| **Web** | `web_dashboard.py` | Flask dashboard with live MJPEG stream |
| **Main** | `full_system.py` | Integration and main processing loop |

## Quick Start

### Installation

```bash
# Install dependencies
pip install opencv-python numpy pyyaml flask

# For Raspberry Pi hardware control
pip install RPi.GPIO pigpio
```

### Running

```bash
# Full system with web dashboard
python full_system.py

# Mock mode (no hardware required)
python full_system.py --mock

# Detection only (no tracking/servo/laser)
python full_system.py --detect-only

# No web dashboard
python full_system.py --no-web

# Custom config
python full_system.py --config my_config.yaml
```

### Web Dashboard

Open `http://<raspberry-pi-ip>:5000` in your browser.

Features:
- Live camera feed with detection/tracking overlays
- System status and statistics
- Remote arm/disarm controls
- Emergency stop button
- Event log

## Configuration

Edit `config.yaml` to tune:

- **Detection**: Background subtractor type, thresholds, morphological operations
- **Tracking**: Kalman filter noise parameters, track management
- **Servo**: GPIO pins, angle limits, smoothing, pixel-to-degree mapping
- **Laser**: Safety interlocks, power levels, firing zones
- **Web**: Port, authentication, stream quality

## Safety

The laser controller implements **10 safety interlocks**:

1. Manual arming required
2. Active mode required
3. Confirmed track required
4. Target within firing zone
5. Per-target cooldown
6. Max continuous fire time
7. Firing time window
8. Max fires per session
9. Emergency stop
10. Minimum time between shots

**⚠️ WARNING**: This system uses a laser. Always follow laser safety protocols. Never point at people, animals, or reflective surfaces. Use appropriate laser safety goggles.**

## Hardware Requirements

- Raspberry Pi (3B+ or 4 recommended)
- USB camera or Pi Camera Module
- 2x SG90 or MG996R servos (pan-tilt mount)
- Laser module (5mW-50mW recommended)
- Motor driver board (if high-power laser)
- Jumper wires

### Wiring

| Component | GPIO Pin | Notes |
|-----------|----------|-------|
| Pan Servo | GPIO 18 | Hardware PWM |
| Tilt Servo | GPIO 19 | Hardware PWM |
| Laser | GPIO 26 | Via transistor/MOSFET |
| Servo VCC | 5V | External power recommended |
| Servo GND | GND | Common ground |

## Testing

```bash
# Run integration tests
python -m pytest test_system.py -v

# Or run the built-in test
python -c "from full_system import *; print('OK')"
```

## License

MIT License — See LICENSE file for details.

## Disclaimer

This project is for educational purposes. The author is not responsible for any injury, damage, or misuse. Always comply with local regulations regarding laser devices.
