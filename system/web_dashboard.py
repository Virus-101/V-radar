"""
Web Dashboard Module.

Flask-based web interface showing the live camera feed with
detection/tracking overlays, system status, and remote controls
for arming/disarming the laser.
"""

import io
import time
import threading
import base64
from typing import Optional
from PIL import Image, ImageDraw, ImageFont


class WebDashboard:
    """Web dashboard for the mosquito laser zapper.

    Serves a live MJPEG stream with overlays, system status,
    and remote control endpoints.
    """

    def __init__(self, config, system):
        self.config = config
        self.system = system  # Reference to the main system

        self._host = config.get("web_dashboard.host", "0.0.0.0")
        self._port = config.get("web_dashboard.port", 5000)
        self._quality = config.get("web_dashboard.stream_quality", 80)
        self._max_fps = config.get("web_dashboard.stream_max_fps", 15)
        self._overlay = config.get("web_dashboard.overlay_enabled", True)

        self._use_auth = config.get("web_dashboard.use_auth", False)
        self._username = config.get("web_dashboard.username", "admin")
        self._password = config.get("web_dashboard.password", "zapper")
        self._allow_remote_arm = config.get("web_dashboard.allow_remote_arm", True)

        self._app = None
        self._server_thread: Optional[threading.Thread] = None
        self._running = False

        # Latest frame for streaming
        self._latest_frame: Optional[bytes] = None
        self._frame_lock = threading.Lock()
        self._frame_event = threading.Event()

        # Stats
        self._stats = {
            "fps": 0.0,
            "detections": 0,
            "tracks": 0,
            "confirmed_tracks": 0,
            "fires": 0,
            "uptime": 0.0,
            "start_time": time.time(),
        }
        self._stats_lock = threading.Lock()

    def start(self) -> None:
        """Start the web server in a background thread."""
        if self._running:
            return

        try:
            from flask import Flask, Response, request, jsonify, render_template_string
        except ImportError:
            print("WARNING: Flask not installed, web dashboard disabled")
            print("  Install with: pip install flask")
            return

        self._app = Flask(__name__)
        self._setup_routes()
        self._running = True

        self._server_thread = threading.Thread(
            target=self._run_server,
            daemon=True,
            name="web-dashboard"
        )
        self._server_thread.start()
        print(f"Web dashboard started at http://{self._host}:{self._port}")

    def _run_server(self) -> None:
        """Run the Flask server."""
        self._app.run(
            host=self._host,
            port=self._port,
            debug=False,
            threaded=True,
            use_reloader=False,
        )

    def _setup_routes(self) -> None:
        """Set up Flask routes."""
        from flask import Flask, Response, request, jsonify, render_template_string

        app = self._app

        HTML_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>Mosquito Laser Zapper</title>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            background: #1a1a2e;
            color: #eee;
            min-height: 100vh;
        }
        .header {
            background: #16213e;
            padding: 15px 30px;
            display: flex;
            justify-content: space-between;
            align-items: center;
            border-bottom: 2px solid #0f3460;
        }
        .header h1 {
            font-size: 1.5em;
            color: #e94560;
        }
        .status-bar {
            display: flex;
            gap: 20px;
            font-size: 0.9em;
        }
        .status-item {
            display: flex;
            align-items: center;
            gap: 5px;
        }
        .status-dot {
            width: 10px;
            height: 10px;
            border-radius: 50%;
            display: inline-block;
        }
        .status-dot.online { background: #00ff88; box-shadow: 0 0 5px #00ff88; }
        .status-dot.offline { background: #ff4444; }
        .status-dot.warning { background: #ffaa00; }
        .main {
            display: flex;
            padding: 20px;
            gap: 20px;
            flex-wrap: wrap;
        }
        .video-container {
            flex: 1;
            min-width: 400px;
            background: #0f3460;
            border-radius: 10px;
            padding: 10px;
            position: relative;
        }
        .video-container img {
            width: 100%;
            border-radius: 5px;
            display: block;
        }
        .overlay-info {
            position: absolute;
            top: 15px;
            left: 15px;
            background: rgba(0,0,0,0.7);
            padding: 8px 12px;
            border-radius: 5px;
            font-size: 0.85em;
            font-family: monospace;
        }
        .sidebar {
            width: 300px;
            display: flex;
            flex-direction: column;
            gap: 15px;
        }
        .panel {
            background: #16213e;
            border-radius: 10px;
            padding: 15px;
            border: 1px solid #0f3460;
        }
        .panel h3 {
            color: #e94560;
            margin-bottom: 10px;
            font-size: 1em;
            text-transform: uppercase;
            letter-spacing: 1px;
        }
        .stat-row {
            display: flex;
            justify-content: space-between;
            padding: 5px 0;
            border-bottom: 1px solid #0f3460;
            font-size: 0.9em;
        }
        .stat-row:last-child { border-bottom: none; }
        .stat-value { color: #00ff88; font-weight: bold; }
        .btn {
            display: block;
            width: 100%;
            padding: 12px;
            margin: 5px 0;
            border: none;
            border-radius: 5px;
            cursor: pointer;
            font-size: 1em;
            font-weight: bold;
            text-transform: uppercase;
            letter-spacing: 1px;
            transition: all 0.2s;
        }
        .btn-arm { background: #00ff88; color: #1a1a2e; }
        .btn-arm:hover { background: #00cc6a; }
        .btn-disarm { background: #ff4444; color: #fff; }
        .btn-disarm:hover { background: #cc3333; }
        .btn-estop { background: #ff0000; color: #fff; font-size: 1.2em; }
        .btn-estop:hover { background: #cc0000; }
        .btn-active { background: #00ff88; color: #1a1a2e; }
        .btn-inactive { background: #666; color: #fff; }
        .log-container {
            max-height: 200px;
            overflow-y: auto;
            font-family: monospace;
            font-size: 0.8em;
            background: #0a0a1a;
            padding: 10px;
            border-radius: 5px;
        }
        .log-entry { padding: 2px 0; border-bottom: 1px solid #1a1a3e; }
        .log-info { color: #88aaff; }
        .log-warning { color: #ffaa00; }
        .log-error { color: #ff4444; }
        .log-fire { color: #ff6600; font-weight: bold; }
        .mode-indicator {
            text-align: center;
            padding: 10px;
            border-radius: 5px;
            font-weight: bold;
            text-transform: uppercase;
            letter-spacing: 2px;
        }
        .mode-armed { background: #00ff88; color: #1a1a2e; }
        .mode-disarmed { background: #ff4444; color: #fff; }
        .mode-estop { background: #ff0000; color: #fff; animation: pulse 1s infinite; }
        @keyframes pulse {
            0%, 100% { opacity: 1; }
            50% { opacity: 0.5; }
        }
    </style>
</head>
<body>
    <div class="header">
        <h1>🦟 Mosquito Laser Zapper</h1>
        <div class="status-bar">
            <div class="status-item">
                <span class="status-dot" id="system-dot"></span>
                <span id="system-status">Connecting...</span>
            </div>
            <div class="status-item">
                <span>FPS: <span id="fps">0</span></span>
            </div>
            <div class="status-item">
                <span>Uptime: <span id="uptime">0:00</span></span>
            </div>
        </div>
    </div>

    <div class="main">
        <div class="video-container">
            <img src="/video_feed" alt="Camera Feed">
            <div class="overlay-info" id="overlay-info">
                Detections: <span id="det-count">0</span> |
                Tracks: <span id="track-count">0</span> |
                Confirmed: <span id="confirmed-count">0</span>
            </div>
        </div>

        <div class="sidebar">
            <div class="panel">
                <h3>System Mode</h3>
                <div class="mode-indicator mode-disarmed" id="mode-indicator">DISARMED</div>
            </div>

            <div class="panel">
                <h3>Controls</h3>
                <button class="btn btn-arm" onclick="armSystem()">🔒 Arm System</button>
                <button class="btn btn-disarm" onclick="disarmSystem()">🔓 Disarm System</button>
                <button class="btn btn-estop" onclick="emergencyStop()">🚨 EMERGENCY STOP</button>
            </div>

            <div class="panel">
                <h3>Statistics</h3>
                <div class="stat-row">
                    <span>Detections</span>
                    <span class="stat-value" id="stat-detections">0</span>
                </div>
                <div class="stat-row">
                    <span>Active Tracks</span>
                    <span class="stat-value" id="stat-tracks">0</span>
                </div>
                <div class="stat-row">
                    <span>Confirmed</span>
                    <span class="stat-value" id="stat-confirmed">0</span>
                </div>
                <div class="stat-row">
                    <span>Shots Fired</span>
                    <span class="stat-value" id="stat-fires">0</span>
                </div>
                <div class="stat-row">
                    <span>Servo Pan</span>
                    <span class="stat-value" id="stat-pan">0°</span>
                </div>
                <div class="stat-row">
                    <span>Servo Tilt</span>
                    <span class="stat-value" id="stat-tilt">0°</span>
                </div>
            </div>

            <div class="panel">
                <h3>Event Log</h3>
                <div class="log-container" id="log-container"></div>
            </div>
        </div>
    </div>

    <script>
        function updateStatus() {
            fetch('/api/status')
                .then(r => r.json())
                .then(data => {
                    document.getElementById('fps').textContent = data.fps.toFixed(1);
                    document.getElementById('det-count').textContent = data.detections;
                    document.getElementById('track-count').textContent = data.tracks;
                    document.getElementById('confirmed-count').textContent = data.confirmed_tracks;
                    document.getElementById('stat-detections').textContent = data.detections;
                    document.getElementById('stat-tracks').textContent = data.tracks;
                    document.getElementById('stat-confirmed').textContent = data.confirmed_tracks;
                    document.getElementById('stat-fires').textContent = data.fires;
                    document.getElementById('stat-pan').textContent = data.pan_angle.toFixed(1) + '°';
                    document.getElementById('stat-tilt').textContent = data.tilt_angle.toFixed(1) + '°';

                    const uptime = Math.floor(data.uptime);
                    const mins = Math.floor(uptime / 60);
                    const secs = uptime % 60;
                    document.getElementById('uptime').textContent = mins + ':' + secs.toString().padStart(2, '0');

                    const modeEl = document.getElementById('mode-indicator');
                    const dotEl = document.getElementById('system-dot');
                    const statusEl = document.getElementById('system-status');

                    if (data.emergency_stop) {
                        modeEl.className = 'mode-indicator mode-estop';
                        modeEl.textContent = 'EMERGENCY STOP';
                        dotEl.className = 'status-dot offline';
                        statusEl.textContent = 'E-STOP';
                    } else if (data.armed && data.active) {
                        modeEl.className = 'mode-indicator mode-armed';
                        modeEl.textContent = 'ARMED & ACTIVE';
                        dotEl.className = 'status-dot online';
                        statusEl.textContent = 'Online';
                    } else if (data.armed) {
                        modeEl.className = 'mode-indicator mode-armed';
                        modeEl.textContent = 'ARMED (STANDBY)';
                        dotEl.className = 'status-dot warning';
                        statusEl.textContent = 'Standby';
                    } else {
                        modeEl.className = 'mode-indicator mode-disarmed';
                        modeEl.textContent = 'DISARMED';
                        dotEl.className = 'status-dot offline';
                        statusEl.textContent = 'Offline';
                    }
                })
                .catch(() => {
                    document.getElementById('system-status').textContent = 'Disconnected';
                    document.getElementById('system-dot').className = 'status-dot offline';
                });
        }

        function armSystem() {
            fetch('/api/arm', {method: 'POST'})
                .then(r => r.json())
                .then(data => addLog(data.message, data.success ? 'info' : 'error'));
        }

        function disarmSystem() {
            fetch('/api/disarm', {method: 'POST'})
                .then(r => r.json())
                .then(data => addLog(data.message, data.success ? 'info' : 'error'));
        }

        function emergencyStop() {
            if (confirm('EMERGENCY STOP? This will immediately kill the laser.')) {
                fetch('/api/emergency_stop', {method: 'POST'})
                    .then(r => r.json())
                    .then(data => addLog(data.message, 'error'));
            }
        }

        function addLog(message, level = 'info') {
            const container = document.getElementById('log-container');
            const entry = document.createElement('div');
            entry.className = 'log-entry log-' + level;
            const time = new Date().toLocaleTimeString();
            entry.textContent = '[' + time + '] ' + message;
            container.insertBefore(entry, container.firstChild);
            while (container.children.length > 50) {
                container.removeChild(container.lastChild);
            }
        }

        setInterval(updateStatus, 500);
        updateStatus();
    </script>
</body>
</html>
        """

        @app.route("/")
        def index():
            if self._use_auth:
                from flask import request, Response
                auth = request.authorization
                if not auth or not self._check_auth(auth.username, auth.password):
                    return Response(
                        "Authentication required",
                        401,
                        {"WWW-Authenticate": 'Basic realm="Mosquito Zapper"'},
                    )
            return render_template_string(HTML_TEMPLATE)

        @app.route("/video_feed")
        def video_feed():
            return Response(
                self._generate_frames(),
                mimetype="multipart/x-mixed-replace; boundary=frame",
            )

        @app.route("/api/status")
        def api_status():
            with self._stats_lock:
                stats = dict(self._stats)

            # Add servo and safety status
            if hasattr(self.system, "servo"):
                stats.update(self.system.servo.get_status())
            if hasattr(self.system, "laser"):
                stats.update(self.system.laser.safety.get_status())

            return jsonify(stats)

        @app.route("/api/arm", methods=["POST"])
        def api_arm():
            if not self._allow_remote_arm:
                return jsonify({"success": False, "message": "Remote arming disabled"})
            if hasattr(self.system, "laser"):
                self.system.laser.safety.arm()
                self.system.laser.safety.set_active(True)
                return jsonify({"success": True, "message": "System armed and activated"})
            return jsonify({"success": False, "message": "Laser not available"})

        @app.route("/api/disarm", methods=["POST"])
        def api_disarm():
            if hasattr(self.system, "laser"):
                self.system.laser.safety.disarm()
                self.system.laser.safety.set_active(False)
                return jsonify({"success": True, "message": "System disarmed"})
            return jsonify({"success": False, "message": "Laser not available"})

        @app.route("/api/emergency_stop", methods=["POST"])
        def api_emergency_stop():
            if hasattr(self.system, "laser"):
                self.system.laser.emergency_stop()
                return jsonify({"success": True, "message": "EMERGENCY STOP triggered"})
            return jsonify({"success": False, "message": "Laser not available"})

        @app.route("/api/reset_estop", methods=["POST"])
        def api_reset_estop():
            if hasattr(self.system, "laser"):
                self.system.laser.safety.reset_emergency_stop()
                return jsonify({"success": True, "message": "Emergency stop reset"})
            return jsonify({"success": False, "message": "Laser not available"})

    def _check_auth(self, username: str, password: str) -> bool:
        """Check authentication credentials."""
        return username == self._username and password == self._password

    def _generate_frames(self):
        """Generate MJPEG stream frames."""
        while self._running:
            self._frame_event.wait(timeout=1.0)
            self._frame_event.clear()

            with self._frame_lock:
                frame = self._latest_frame

            if frame is not None:
                yield (
                    b"--frame\r\n"
                    b"Content-Type: image/jpeg\r\n\r\n" + frame + b"\r\n"
                )

            # Rate limit
            time.sleep(1.0 / self._max_fps)

    def update_frame(self, frame_bytes: bytes) -> None:
        """Update the latest frame for streaming.

        Args:
            frame_bytes: JPEG-encoded frame.
        """
        with self._frame_lock:
            self._latest_frame = frame_bytes
        self._frame_event.set()

    def update_stats(self, fps: float = 0, detections: int = 0,
                     tracks: int = 0, confirmed: int = 0,
                     fires: int = 0) -> None:
        """Update dashboard statistics."""
        with self._stats_lock:
            self._stats["fps"] = fps
            self._stats["detections"] = detections
            self._stats["tracks"] = tracks
            self._stats["confirmed_tracks"] = confirmed
            self._stats["fires"] = fires
            self._stats["uptime"] = time.time() - self._stats["start_time"]

    def stop(self) -> None:
        """Stop the web server."""
        self._running = False
        if self._server_thread:
            self._server_thread.join(timeout=2.0)

    @property
    def is_running(self) -> bool:
        return self._running
