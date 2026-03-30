"""
lumi_eye_control.py — Siri-level Eye Tracking & Blink Control
==============================================================
Complete rewrite. Fixes all issues from v1:

  FIXES
  -----
  • Relative iris tracking  — iris position relative to eye socket, not frame.
                              Head movement no longer drifts the cursor.
  • Adaptive EAR threshold  — auto-calibrates to your eyes on startup.
                              No more missed blinks or false positives.
  • Calibration routine     — 5-point screen calibration maps your iris range
                              to actual screen coordinates. Siri-accurate targeting.
  • Startup timeout         — extended to 6 s to allow mediapipe model download.
  • Dwell click             — look at a point for N seconds to left-click hands-free.
  • Velocity + position mix — smooth cursor that accelerates toward gaze target.

  GESTURES
  --------
  • Natural blink   → left click  (deliberate, >80ms)
  • Double blink    → right click (two blinks within 450ms)
  • Long blink      → pause / resume (>750ms)
  • Dwell (2s)      → left click (optional, enabled via EYE_DWELL_CLICK=1)

  CALIBRATION
  -----------
  Say "calibrate eye control" to run a 5-point calibration.
  Each point: look at the dot shown, blink once to confirm.
  After calibration, cursor accuracy matches your actual gaze.

  INSTALL
  -------
  pip install opencv-python mediapipe pyautogui

  .env options
  ------------
  EYE_CAMERA_INDEX=0
  EYE_BLINK_EAR_THRESHOLD=0.0     # 0 = auto-calibrate (recommended)
  EYE_DOUBLE_BLINK_SECONDS=0.45
  EYE_LONG_BLINK_SECONDS=0.75
  EYE_CURSOR_SPEED=1.0            # multiplier (0.5 slow … 2.0 fast)
  EYE_SMOOTHING=0.12              # lower = smoother but laggier
  EYE_DWELL_CLICK=0               # 1 = enable dwell click
  EYE_DWELL_SECONDS=2.0
  EYE_SHOW_PREVIEW=0              # 1 = show debug webcam window
"""

from __future__ import annotations

import logging
import math
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Optional, Tuple

logger = logging.getLogger("lumi.eye_control")

# ---------------------------------------------------------------------------
# Optional deps
# ---------------------------------------------------------------------------

try:
    import cv2
    _HAS_CV2 = True
except ImportError:
    cv2 = None
    _HAS_CV2 = False

try:
    import mediapipe as mp
    _HAS_MEDIAPIPE = True
except ImportError:
    mp = None
    _HAS_MEDIAPIPE = False

try:
    import pyautogui
    pyautogui.FAILSAFE = False   # prevent corner-kill while gaze tracking
    _HAS_PYAUTOGUI = True
except ImportError:
    pyautogui = None
    _HAS_PYAUTOGUI = False

try:
    import ctypes
    _SCREEN_W = ctypes.windll.user32.GetSystemMetrics(0)
    _SCREEN_H = ctypes.windll.user32.GetSystemMetrics(1)
except Exception:
    _SCREEN_W, _SCREEN_H = 1920, 1080

# ---------------------------------------------------------------------------
# MediaPipe landmark indices
# ---------------------------------------------------------------------------

# Eye aspect ratio landmarks (6 pts per eye: p1-p4 horizontal, p2-p6 vertical)
_LEFT_EYE  = (33, 160, 158, 133, 153, 144)
_RIGHT_EYE = (362, 385, 387, 263, 373, 380)

# Iris centre landmarks (refined mesh)
_LEFT_IRIS_CENTER  = 468
_RIGHT_IRIS_CENTER = 473

# Eye socket inner/outer corners for relative tracking
_LEFT_CORNER_INNER  = 133
_LEFT_CORNER_OUTER  = 33
_RIGHT_CORNER_INNER = 362
_RIGHT_CORNER_OUTER = 263

# Eye socket top/bottom for vertical bounds
_LEFT_TOP    = 159
_LEFT_BOTTOM = 145
_RIGHT_TOP   = 386
_RIGHT_BOTTOM = 374


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _dist(a, b) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _ear(pts, idx) -> float:
    """Eye Aspect Ratio — lower = more closed."""
    p1, p2, p3, p4, p5, p6 = (pts[i] for i in idx)
    h = _dist(p1, p4)
    if h < 1e-6:
        return 0.0
    return (_dist(p2, p6) + _dist(p3, p5)) / (2.0 * h)


def _relative_iris(pts, iris_idx, inner_idx, outer_idx, top_idx, bottom_idx) -> Tuple[float, float]:
    """
    Returns iris position as (rx, ry) in range [-1, +1]
    relative to the eye socket bounding box.
    This eliminates head-movement drift.
    """
    iris  = pts[iris_idx]
    inner = pts[inner_idx]
    outer = pts[outer_idx]
    top   = pts[top_idx]
    bot   = pts[bottom_idx]

    w = _dist(inner, outer)
    h = _dist(top, bot)
    if w < 1e-6 or h < 1e-6:
        return 0.0, 0.0

    cx = (inner[0] + outer[0]) / 2.0
    cy = (top[1]   + bot[1])   / 2.0

    rx = (iris[0] - cx) / (w / 2.0)   # -1 = full left, +1 = full right
    ry = (iris[1] - cy) / (h / 2.0)   # -1 = full up,   +1 = full down

    return (
        max(-1.0, min(1.0, rx)),
        max(-1.0, min(1.0, ry)),
    )


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def _eb(name, default):
    v = os.getenv(name)
    return default if v is None else v.strip().lower() in {"1", "true", "yes"}

def _ei(name, default):
    v = os.getenv(name)
    if v is None: return default
    try: return int(v.strip())
    except: return default

def _ef(name, default):
    v = os.getenv(name)
    if v is None: return default
    try: return float(v.strip())
    except: return default


@dataclass
class EyeControlConfig:
    camera_index:          int   = 0
    # 0.0 = auto-calibrate threshold on startup (recommended)
    blink_ear_threshold:   float = 0.0
    min_blink_seconds:     float = 0.08
    double_blink_seconds:  float = 0.45
    long_blink_seconds:    float = 0.75
    # cursor_speed: overall movement multiplier
    cursor_speed:          float = 1.0
    # smoothing: exponential weight for gaze filter (lower = smoother)
    smoothing:             float = 0.12
    # dwell click: look at a spot for dwell_seconds → left click
    dwell_click:           bool  = False
    dwell_seconds:         float = 2.0
    show_preview:          bool  = False

    @classmethod
    def from_env(cls) -> "EyeControlConfig":
        return cls(
            camera_index         = max(0, _ei("EYE_CAMERA_INDEX", 0)),
            blink_ear_threshold  = max(0.0, min(0.40, _ef("EYE_BLINK_EAR_THRESHOLD", 0.0))),
            min_blink_seconds    = max(0.03, min(0.40, _ef("EYE_MIN_BLINK_SECONDS", 0.08))),
            double_blink_seconds = max(0.15, min(1.0,  _ef("EYE_DOUBLE_BLINK_SECONDS", 0.45))),
            long_blink_seconds   = max(0.40, min(2.0,  _ef("EYE_LONG_BLINK_SECONDS", 0.75))),
            cursor_speed         = max(0.2,  min(5.0,  _ef("EYE_CURSOR_SPEED", 1.0))),
            smoothing            = max(0.03, min(0.80, _ef("EYE_SMOOTHING", 0.12))),
            dwell_click          = _eb("EYE_DWELL_CLICK", False),
            dwell_seconds        = max(0.5,  min(5.0,  _ef("EYE_DWELL_SECONDS", 2.0))),
            show_preview         = _eb("EYE_SHOW_PREVIEW", False),
        )


# ---------------------------------------------------------------------------
# Calibration data
# ---------------------------------------------------------------------------

@dataclass
class CalibrationData:
    """
    Maps raw relative iris coords → screen pixel coordinates.
    Collected from 5 calibration points:
      top-left, top-right, centre, bottom-left, bottom-right
    """
    # Iris range observed during calibration
    x_min: float = -0.4
    x_max: float =  0.4
    y_min: float = -0.4
    y_max: float =  0.4
    calibrated: bool = False

    def iris_to_screen(self, rx: float, ry: float) -> Tuple[int, int]:
        """Map relative iris position to screen pixel. Clamps to screen bounds."""
        x_range = max(self.x_max - self.x_min, 0.01)
        y_range = max(self.y_max - self.y_min, 0.01)

        nx = (rx - self.x_min) / x_range          # 0..1
        ny = (ry - self.y_min) / y_range

        sx = int(nx * _SCREEN_W)
        sy = int(ny * _SCREEN_H)

        sx = max(0, min(_SCREEN_W - 1, sx))
        sy = max(0, min(_SCREEN_H - 1, sy))
        return sx, sy


# ---------------------------------------------------------------------------
# EyeControlEngine
# ---------------------------------------------------------------------------

class EyeControlEngine:

    def __init__(
        self,
        logger: Optional[logging.Logger] = None,
        config: Optional[EyeControlConfig] = None,
    ):
        self.logger = logger or logging.getLogger("lumi.eye_control")
        self.config = config or EyeControlConfig.from_env()
        self.calib  = CalibrationData()

        self._stop_event    = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock          = threading.Lock()
        self._paused        = False

        self._startup_done  = threading.Event()
        self._startup_error = ""

        # Gaze state
        self._gaze_x: float = 0.0
        self._gaze_y: float = 0.0
        self._screen_x: int = _SCREEN_W  // 2
        self._screen_y: int = _SCREEN_H  // 2

        # Dwell state
        self._dwell_x:    float = 0.0
        self._dwell_y:    float = 0.0
        self._dwell_since: Optional[float] = None

        # Adaptive EAR calibration
        self._ear_open_samples: list = []
        self._ear_threshold:    float = 0.20

        # Calibration control
        self._calibrating          = False
        self._calib_point_index    = 0
        self._calib_blink_received = threading.Event()
        self._calib_iris_samples:  list = []

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @staticmethod
    def dependency_message() -> str:
        missing = []
        if not _HAS_CV2:        missing.append("opencv-python")
        if not _HAS_MEDIAPIPE:  missing.append("mediapipe")
        if not _HAS_PYAUTOGUI:  missing.append("pyautogui")
        if missing:
            return "Missing: " + ", ".join(missing) + ". Run: pip install " + " ".join(missing)
        return ""

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def is_paused(self) -> bool:
        with self._lock:
            return self._paused

    def pause(self) -> tuple:
        if not self.is_running():
            return False, "Eye control is not running."
        with self._lock:
            self._paused = True
        return True, "Eye control paused."

    def resume(self) -> tuple:
        if not self.is_running():
            return False, "Eye control is not running."
        with self._lock:
            self._paused = False
        return True, "Eye control resumed."

    def status_text(self) -> str:
        if not self.is_running():
            return "Eye control is off."
        if self.is_paused():
            return "Eye control is paused."
        calib = " Calibrated." if self.calib.calibrated else ""
        return f"Eye control is running.{calib}"

    def start(self) -> tuple:
        if self.is_running():
            return True, "Eye control is already running."
        dep = self.dependency_message()
        if dep:
            return False, dep

        self._startup_done.clear()
        self._startup_error = ""
        self._stop_event.clear()
        self._gaze_x = self._gaze_y = 0.0
        self._screen_x = _SCREEN_W // 2
        self._screen_y = _SCREEN_H // 2
        self._dwell_since = None
        self._ear_open_samples.clear()
        with self._lock:
            self._paused = False

        self._thread = threading.Thread(
            target=self._run_loop,
            daemon=True,
            name="lumi-eye-control",
        )
        self._thread.start()

        # Give mediapipe up to 6 s to load its model
        self._startup_done.wait(timeout=6.0)
        if self._startup_error:
            self.stop()
            return False, self._startup_error
        if not self.is_running():
            return False, "Eye control failed to start. Check the webcam and try again."

        return True, (
            "Eye control started. "
            "Blink to left-click, double blink for right-click, long blink to pause. "
            "Say calibrate eye control for better accuracy."
        )

    def stop(self) -> tuple:
        if not self.is_running():
            return True, "Eye control is already stopped."
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=3.0)
        return True, "Eye control stopped."

    def start_calibration(self) -> None:
        """
        Kick off 5-point calibration from a voice command.
        Shows an overlay window guiding the user through each point.
        """
        if not self.is_running():
            return
        threading.Thread(
            target=self._calibration_routine,
            daemon=True,
            name="lumi-eye-calib",
        ).start()

    # ------------------------------------------------------------------
    # Calibration routine
    # ------------------------------------------------------------------

    _CALIB_POINTS = [
        (0.05, 0.05),   # top-left
        (0.95, 0.05),   # top-right
        (0.50, 0.50),   # centre
        (0.05, 0.95),   # bottom-left
        (0.95, 0.95),   # bottom-right
    ]

    def _calibration_routine(self) -> None:
        """
        Show a coloured dot at each calibration point.
        User looks at the dot, then blinks once to register.
        Runs in its own thread — communicates with the main loop
        via self._calibrating and self._calib_blink_received.
        """
        if not _HAS_CV2:
            return

        self._calibrating = True
        self._calib_iris_samples = [None] * len(self._CALIB_POINTS)

        w, h = _SCREEN_W, _SCREEN_H
        canvas = None

        self.logger.info("Calibration started")

        for i, (nx, ny) in enumerate(self._CALIB_POINTS):
            px = int(nx * w)
            py = int(ny * h)

            canvas = __import__("numpy").zeros((h, w, 3), dtype="uint8")
            cv2.circle(canvas, (px, py), 20, (0, 132, 255), -1)
            cv2.circle(canvas, (px, py), 25, (255, 255, 255), 2)
            cv2.putText(
                canvas,
                f"Look at dot {i+1}/5, then blink",
                (w // 2 - 220, h - 40),
                cv2.FONT_HERSHEY_SIMPLEX, 0.9,
                (200, 200, 200), 2, cv2.LINE_AA,
            )
            cv2.namedWindow("Lumi Calibration", cv2.WINDOW_NORMAL)
            cv2.setWindowProperty("Lumi Calibration", cv2.WND_PROP_FULLSCREEN, cv2.WINDOW_FULLSCREEN)
            cv2.imshow("Lumi Calibration", canvas)
            cv2.waitKey(1)

            self._calib_point_index = i
            self._calib_blink_received.clear()
            # Wait up to 10 s per point for a confirming blink
            self._calib_blink_received.wait(timeout=10.0)

        try:
            cv2.destroyWindow("Lumi Calibration")
        except Exception:
            pass

        self._calibrating = False

        # Build calibration model from collected iris samples
        samples = [s for s in self._calib_iris_samples if s is not None]
        if len(samples) >= 3:
            xs = [s[0] for s in samples]
            ys = [s[1] for s in samples]
            self.calib.x_min = min(xs) - 0.02
            self.calib.x_max = max(xs) + 0.02
            self.calib.y_min = min(ys) - 0.02
            self.calib.y_max = max(ys) + 0.02
            self.calib.calibrated = True
            self.logger.info(
                "Calibration complete: x=[%.3f,%.3f] y=[%.3f,%.3f]",
                self.calib.x_min, self.calib.x_max,
                self.calib.y_min, self.calib.y_max,
            )
        else:
            self.logger.warning("Calibration incomplete — not enough points collected")

    # ------------------------------------------------------------------
    # Click helpers
    # ------------------------------------------------------------------

    def _left_click(self) -> None:
        try:
            pyautogui.click(button="left")
            self.logger.debug("Left click at (%d, %d)", self._screen_x, self._screen_y)
        except Exception as exc:
            self.logger.debug("Left click failed: %s", exc)

    def _right_click(self) -> None:
        try:
            pyautogui.click(button="right")
            self.logger.debug("Right click")
        except Exception as exc:
            self.logger.debug("Right click failed: %s", exc)

    def _move_to(self, x: int, y: int) -> None:
        try:
            pyautogui.moveTo(x, y, duration=0)
        except Exception as exc:
            self.logger.debug("Move failed: %s", exc)

    # ------------------------------------------------------------------
    # Main tracking loop
    # ------------------------------------------------------------------

    def _run_loop(self) -> None:
        cam = None

        # Blink state machine
        closed_at:           Optional[float] = None
        long_consumed:       bool            = False
        pending_single_at:   Optional[float] = None

        last_t = time.monotonic()

        try:
            cam = cv2.VideoCapture(
                self.config.camera_index,
                cv2.CAP_DSHOW if os.name == "nt" else 0,
            )
            cam.set(cv2.CAP_PROP_FRAME_WIDTH,  640)
            cam.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
            cam.set(cv2.CAP_PROP_FPS, 30)

            if not cam.isOpened():
                self._startup_error = "Could not open webcam. Check camera permissions."
                self._startup_done.set()
                return

            mp_mesh = mp.solutions.face_mesh
            with mp_mesh.FaceMesh(
                max_num_faces=1,
                refine_landmarks=True,          # enables iris landmarks 468–477
                min_detection_confidence=0.6,
                min_tracking_confidence=0.6,
            ) as mesh:
                self.logger.info("Eye control loop running (camera=%d)", self.config.camera_index)
                self._startup_done.set()

                while not self._stop_event.is_set():
                    ok, frame = cam.read()
                    if not ok or frame is None:
                        time.sleep(0.01)
                        continue

                    now = time.monotonic()
                    dt  = max(0.005, min(0.1, now - last_t))
                    last_t = now

                    fh, fw = frame.shape[:2]

                    # Flip horizontally so left/right match screen
                    frame = cv2.flip(frame, 1)

                    rgb    = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                    result = mesh.process(rgb)

                    # ── No face detected ──────────────────────────────────
                    if not result.multi_face_landmarks:
                        # Fire pending single-click if timeout passed
                        if (pending_single_at is not None
                                and now - pending_single_at > self.config.double_blink_seconds):
                            self._left_click()
                            pending_single_at = None
                        if self.config.show_preview:
                            cv2.imshow("Lumi Eye Control", frame)
                            cv2.waitKey(1)
                        continue

                    pts = [
                        (lm.x * fw, lm.y * fh)
                        for lm in result.multi_face_landmarks[0].landmark
                    ]

                    # ── EAR (blink detection) ─────────────────────────────
                    left_ear  = _ear(pts, _LEFT_EYE)
                    right_ear = _ear(pts, _RIGHT_EYE)
                    avg_ear   = (left_ear + right_ear) / 2.0

                    # Auto-calibrate EAR threshold from open-eye samples
                    if len(self._ear_open_samples) < 120:
                        self._ear_open_samples.append(avg_ear)
                    elif len(self._ear_open_samples) == 120:
                        mean_open = sum(self._ear_open_samples) / len(self._ear_open_samples)
                        # Threshold = 75% of mean open-eye EAR
                        if self.config.blink_ear_threshold == 0.0:
                            self._ear_threshold = mean_open * 0.75
                            self.logger.info(
                                "Auto EAR threshold: %.3f (mean open=%.3f)",
                                self._ear_threshold, mean_open,
                            )
                        else:
                            self._ear_threshold = self.config.blink_ear_threshold
                        self._ear_open_samples.append(None)  # sentinel

                    is_closed = avg_ear < self._ear_threshold

                    # ── Blink state machine ────────────────────────────────
                    if is_closed:
                        if closed_at is None:
                            closed_at      = now
                            long_consumed  = False
                        closed_for = now - closed_at
                        if not long_consumed and closed_for >= self.config.long_blink_seconds:
                            with self._lock:
                                self._paused = not self._paused
                                paused = self._paused
                            long_consumed     = True
                            pending_single_at = None
                            self.logger.info("Long blink: %s", "paused" if paused else "resumed")
                    else:
                        if closed_at is not None:
                            closed_for = now - closed_at
                            if closed_for >= self.config.min_blink_seconds and not long_consumed:
                                if (pending_single_at is not None
                                        and now - pending_single_at <= self.config.double_blink_seconds):
                                    # Double blink → right click
                                    self._right_click()
                                    pending_single_at = None
                                    # Calibration confirm
                                    if self._calibrating:
                                        idx = self._calib_point_index
                                        if self._calib_iris_samples[idx] is None:
                                            rx, ry = self._get_relative_gaze(pts)
                                            self._calib_iris_samples[idx] = (rx, ry)
                                            self._calib_blink_received.set()
                                else:
                                    pending_single_at = now
                                    # Calibration confirm on single blink
                                    if self._calibrating:
                                        idx = self._calib_point_index
                                        if self._calib_iris_samples[idx] is None:
                                            rx, ry = self._get_relative_gaze(pts)
                                            self._calib_iris_samples[idx] = (rx, ry)
                                            self._calib_blink_received.set()
                                            pending_single_at = None   # don't fire a click
                        closed_at     = None
                        long_consumed = False

                    # Fire pending single-click
                    if (pending_single_at is not None
                            and now - pending_single_at > self.config.double_blink_seconds):
                        self._left_click()
                        pending_single_at = None

                    # ── Gaze tracking (cursor movement) ───────────────────
                    if not self.is_paused() and not self._calibrating:
                        rx, ry = self._get_relative_gaze(pts)

                        # Smooth
                        a = self.config.smoothing
                        self._gaze_x = (1.0 - a) * self._gaze_x + a * rx
                        self._gaze_y = (1.0 - a) * self._gaze_y + a * ry

                        if self.calib.calibrated:
                            # Post-calibration: map to absolute screen position
                            tx, ty = self.calib.iris_to_screen(self._gaze_x, self._gaze_y)
                        else:
                            # Pre-calibration: velocity-based relative movement
                            # Scale by speed multiplier and dt
                            BASE = 1800.0
                            vx = math.copysign(abs(self._gaze_x) ** 1.6, self._gaze_x)
                            vy = math.copysign(abs(self._gaze_y) ** 1.6, self._gaze_y)
                            tx = int(max(0, min(_SCREEN_W - 1,
                                self._screen_x + vx * BASE * dt * self.config.cursor_speed)))
                            ty = int(max(0, min(_SCREEN_H - 1,
                                self._screen_y + vy * BASE * dt * self.config.cursor_speed)))

                        if tx != self._screen_x or ty != self._screen_y:
                            self._screen_x = tx
                            self._screen_y = ty
                            self._move_to(tx, ty)

                        # ── Dwell click ───────────────────────────────────
                        if self.config.dwell_click:
                            dwell_threshold = max(30, int(0.015 * max(_SCREEN_W, _SCREEN_H)))
                            moved = math.hypot(
                                self._screen_x - self._dwell_x,
                                self._screen_y - self._dwell_y,
                            )
                            if moved > dwell_threshold:
                                self._dwell_x     = self._screen_x
                                self._dwell_y     = self._screen_y
                                self._dwell_since = now
                            elif (self._dwell_since is not None
                                    and now - self._dwell_since >= self.config.dwell_seconds):
                                self._left_click()
                                self._dwell_since = None

                    # ── Preview window ────────────────────────────────────
                    if self.config.show_preview:
                        self._draw_preview(frame, pts, avg_ear, is_closed)
                        cv2.imshow("Lumi Eye Control (q to close)", frame)
                        if cv2.waitKey(1) & 0xFF == ord("q"):
                            self._stop_event.set()

        except Exception as exc:
            self.logger.exception("Eye control loop crashed: %s", exc)
            if not self._startup_done.is_set():
                self._startup_error = f"Eye control failed: {exc}"
                self._startup_done.set()
        finally:
            if cam is not None:
                try:
                    cam.release()
                except Exception:
                    pass
            if self.config.show_preview and _HAS_CV2:
                try:
                    cv2.destroyAllWindows()
                except Exception:
                    pass
            if not self._startup_done.is_set():
                self._startup_done.set()
            self.logger.info("Eye control loop exited.")

    # ------------------------------------------------------------------
    # Relative gaze extractor
    # ------------------------------------------------------------------

    def _get_relative_gaze(self, pts) -> Tuple[float, float]:
        """
        Average of left and right iris relative positions.
        This is head-movement-invariant.
        """
        lx, ly = _relative_iris(
            pts,
            _LEFT_IRIS_CENTER,
            _LEFT_CORNER_INNER, _LEFT_CORNER_OUTER,
            _LEFT_TOP, _LEFT_BOTTOM,
        )
        rx, ry = _relative_iris(
            pts,
            _RIGHT_IRIS_CENTER,
            _RIGHT_CORNER_INNER, _RIGHT_CORNER_OUTER,
            _RIGHT_TOP, _RIGHT_BOTTOM,
        )
        return (lx + rx) / 2.0, (ly + ry) / 2.0

    # ------------------------------------------------------------------
    # Debug preview
    # ------------------------------------------------------------------

    def _draw_preview(self, frame, pts, ear, is_closed) -> None:
        fh, fw = frame.shape[:2]
        status = "PAUSED" if self.is_paused() else ("BLINK" if is_closed else "TRACKING")
        color  = (40, 180, 255) if self.is_paused() else ((0, 80, 255) if is_closed else (20, 230, 20))

        cv2.putText(
            frame,
            f"{status}  EAR={ear:.3f}  thr={self._ear_threshold:.3f}",
            (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.65, color, 2, cv2.LINE_AA,
        )

        calib_txt = "CALIBRATED" if self.calib.calibrated else "uncalibrated"
        cv2.putText(
            frame,
            f"gaze=({self._gaze_x:+.2f},{self._gaze_y:+.2f})  {calib_txt}",
            (10, 55), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (180, 180, 180), 1, cv2.LINE_AA,
        )

        # Draw iris centres
        for idx, colour in [
            (_LEFT_IRIS_CENTER,  (255, 100,  50)),
            (_RIGHT_IRIS_CENTER, (255, 100,  50)),
        ]:
            if idx < len(pts):
                cx, cy = int(pts[idx][0]), int(pts[idx][1])
                cv2.circle(frame, (cx, cy), 4, colour, -1)