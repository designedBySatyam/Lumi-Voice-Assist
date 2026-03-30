"""
lumi_tray.py — Phase 5: System Tray + Background Service
=========================================================
Makes Lumi a proper Windows background service:

  1. System tray icon  — pystray-powered icon in the notification area
                         Right-click menu: Status, Mute, Settings, Restart, Quit
                         Icon pulses (swaps) when Lumi is listening/speaking

  2. Single-instance   — prevents two Lumi processes running at once
                         Uses a named Windows mutex

  3. Auto-start        — registers / removes a Windows Task Scheduler entry
                         so Lumi launches silently at login (no console window)

  4. Graceful shutdown — catches SIGINT / SIGTERM / WM_QUERYENDSESSION
                         so Lumi saves state and exits cleanly on reboot/logout

Install
-------
    pip install pystray Pillow

Usage
-----
    python lumi_tray.py              # launch with tray (preferred)
    python lumi_tray.py --install    # register Task Scheduler auto-start
    python lumi_tray.py --uninstall  # remove auto-start entry
    python lumi_tray.py --no-tray    # headless / server mode
"""

from __future__ import annotations

import argparse
import ctypes
import logging
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger("lumi.tray")

# ---------------------------------------------------------------------------
# Optional deps
# ---------------------------------------------------------------------------

try:
    import pystray
    from pystray import MenuItem as TrayItem, Menu as TrayMenu
    _HAS_PYSTRAY = True
except ImportError:
    pystray = TrayItem = TrayMenu = None
    _HAS_PYSTRAY = False

try:
    from PIL import Image, ImageDraw, ImageFont
    _HAS_PIL = True
except ImportError:
    Image = ImageDraw = ImageFont = None
    _HAS_PIL = False

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_HERE       = Path(__file__).parent.resolve()
_ASSISTANT  = _HERE / "assistant.py"
_ICON_IDLE  = _HERE / "lumi_icon_idle.png"
_ICON_ON    = _HERE / "lumi_icon_active.png"
_TASK_NAME  = "LumiVoiceAssistant"
_MUTEX_NAME = "Global\\LumiVoiceAssistantMutex"


# ---------------------------------------------------------------------------
# 1. Single-instance mutex
# ---------------------------------------------------------------------------

class SingleInstance:
    """
    Prevents two Lumi processes from running at the same time.
    Raises SystemExit if another instance is already running.
    """

    def __init__(self):
        self._mutex = None

    def acquire(self) -> bool:
        try:
            self._mutex = ctypes.windll.kernel32.CreateMutexW(
                None, True, _MUTEX_NAME
            )
            last_error = ctypes.windll.kernel32.GetLastError()
            if last_error == 183:   # ERROR_ALREADY_EXISTS
                logger.warning("Another Lumi instance is already running.")
                return False
            return True
        except Exception as exc:
            logger.warning("Mutex creation failed: %s", exc)
            return True  # fail open — don't block startup over this

    def release(self) -> None:
        if self._mutex:
            try:
                ctypes.windll.kernel32.CloseHandle(self._mutex)
            except Exception:
                pass
            self._mutex = None


# ---------------------------------------------------------------------------
# 2. Task Scheduler auto-start
# ---------------------------------------------------------------------------

class StartupManager:
    """Register / remove Lumi in Windows Task Scheduler."""

    @staticmethod
    def install() -> bool:
        """Register a Task Scheduler entry that runs Lumi at login."""
        python_exe = sys.executable
        script     = str(_HERE / "lumi_tray.py")

        # Build the XML task definition
        xml = f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>Lumi Voice Assistant — always-on background service</Description>
  </RegistrationInfo>
  <Triggers>
    <LogonTrigger>
      <Enabled>true</Enabled>
      <Delay>PT10S</Delay>
    </LogonTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <Priority>7</Priority>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{python_exe}</Command>
      <Arguments>"{script}"</Arguments>
      <WorkingDirectory>{str(_HERE)}</WorkingDirectory>
    </Exec>
  </Actions>
</Task>"""

        xml_path = _HERE / "lumi_task.xml"
        try:
            xml_path.write_text(xml, encoding="utf-16")
            result = subprocess.run(
                ["schtasks", "/Create", "/TN", _TASK_NAME,
                 "/XML", str(xml_path), "/F"],
                capture_output=True, text=True, timeout=10,
            )
            xml_path.unlink(missing_ok=True)
            if result.returncode == 0:
                logger.info("Task Scheduler entry created: %s", _TASK_NAME)
                print(f"✓ Lumi will now start automatically at login.")
                return True
            else:
                logger.error("schtasks failed: %s", result.stderr)
                print(f"✗ Failed to register auto-start: {result.stderr}")
                return False
        except Exception as exc:
            logger.error("Auto-start install failed: %s", exc)
            print(f"✗ Error: {exc}")
            return False

    @staticmethod
    def uninstall() -> bool:
        """Remove the Task Scheduler entry."""
        try:
            result = subprocess.run(
                ["schtasks", "/Delete", "/TN", _TASK_NAME, "/F"],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode == 0:
                logger.info("Task Scheduler entry removed: %s", _TASK_NAME)
                print(f"✓ Lumi auto-start removed.")
                return True
            else:
                print(f"✗ Could not remove: {result.stderr.strip()}")
                return False
        except Exception as exc:
            print(f"✗ Error: {exc}")
            return False

    @staticmethod
    def is_installed() -> bool:
        try:
            result = subprocess.run(
                ["schtasks", "/Query", "/TN", _TASK_NAME],
                capture_output=True, timeout=5,
            )
            return result.returncode == 0
        except Exception:
            return False


# ---------------------------------------------------------------------------
# 3. Tray icon image generator
# ---------------------------------------------------------------------------

def _make_icon(active: bool = False, size: int = 64) -> "Image":
    """
    Generate a simple tray icon programmatically.
    Idle  = dark pill with white L
    Active = blue pill with white L
    """
    if not _HAS_PIL:
        raise RuntimeError("Pillow not installed")

    bg     = (30, 30, 35, 255)   if not active else (10, 132, 255, 255)
    border = (80, 80, 90, 200)   if not active else (10, 100, 220, 255)

    img  = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Rounded rect (pill) background
    r = size // 4
    draw.rounded_rectangle([4, 4, size - 4, size - 4], radius=r, fill=bg, outline=border, width=2)

    # Letter "L"
    margin = size // 4
    lw = size // 12  # stroke width
    draw.rectangle(
        [margin, margin, margin + lw, size - margin],
        fill=(255, 255, 255, 230),
    )
    draw.rectangle(
        [margin, size - margin - lw, size - margin, size - margin],
        fill=(255, 255, 255, 230),
    )

    return img


def _save_icons() -> None:
    """Pre-generate and cache icon files if they don't exist."""
    if not _HAS_PIL:
        return
    try:
        if not _ICON_IDLE.exists():
            _make_icon(active=False).save(str(_ICON_IDLE))
        if not _ICON_ON.exists():
            _make_icon(active=True).save(str(_ICON_ON))
    except Exception as exc:
        logger.debug("Icon save failed: %s", exc)


# ---------------------------------------------------------------------------
# 4. LumiTray — the tray icon + menu
# ---------------------------------------------------------------------------

class LumiTray:
    """
    Manages the system tray icon and its right-click menu.
    Runs pystray in a background thread; the main thread stays
    available for the assistant event loop.
    """

    def __init__(self, assistant):
        self.assistant = assistant
        self._icon: Optional["pystray.Icon"] = None
        self._muted   = False
        self._status  = "Idle"
        self._active  = False
        self._tray_thread: Optional[threading.Thread] = None

    # ------------------------------------------------------------------
    # Icon switching
    # ------------------------------------------------------------------

    def _current_image(self):
        try:
            if _HAS_PIL:
                return _make_icon(active=self._active)
        except Exception:
            pass
        # Minimal 1x1 fallback
        if _HAS_PIL:
            return Image.new("RGBA", (1, 1), (0, 0, 0, 0))
        return None

    def _refresh_icon(self) -> None:
        if self._icon:
            try:
                self._icon.icon = self._current_image()
                self._icon.title = f"Lumi — {self._status}"
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Status updates (called by assistant event_callback)
    # ------------------------------------------------------------------

    def on_event(self, kind: str, text: str) -> None:
        if kind == "status":
            self._status = text
            self._active = any(
                w in text.lower()
                for w in ("listening", "thinking", "processing")
            )
            self._refresh_icon()
        elif kind == "assistant":
            self._status = "Speaking"
            self._active = True
            self._refresh_icon()

    # ------------------------------------------------------------------
    # Menu actions
    # ------------------------------------------------------------------

    def _action_status(self) -> None:
        self.assistant.speak(
            f"Lumi is running. Status: {self._status}. "
            f"{'Muted.' if self._muted else 'Microphone active.'}"
        )

    def _action_mute_toggle(self) -> None:
        self._muted = not self._muted
        if self._muted:
            self.assistant.speak("Lumi muted. Say unmute or use the tray to re-enable.")
            self.assistant._stop_event.set()
        else:
            self.assistant._stop_event.clear()
            self.assistant.start_background()
            self.assistant.speak("Lumi unmuted and listening.")
        self._refresh_menu()

    def _action_restart(self) -> None:
        self.assistant.speak("Restarting Lumi.")
        time.sleep(0.5)
        self.assistant.stop_background()
        time.sleep(0.8)
        self.assistant.start_background()

    def _action_open_settings(self) -> None:
        env_path = _HERE / ".env"
        try:
            os.startfile(str(env_path))
        except Exception:
            subprocess.Popen(["notepad", str(env_path)])

    def _action_quit(self) -> None:
        self.assistant.speak("Goodbye!")
        time.sleep(0.6)
        self.assistant.stop_background()
        if self._icon:
            self._icon.stop()

    # ------------------------------------------------------------------
    # Menu builder
    # ------------------------------------------------------------------

    def _build_menu(self):
        if not _HAS_PYSTRAY:
            return None
        mute_label = "Unmute Lumi" if self._muted else "Mute Lumi"
        autostart  = StartupManager.is_installed()
        auto_label = "Remove from startup" if autostart else "Start at login"

        def toggle_autostart():
            if StartupManager.is_installed():
                StartupManager.uninstall()
            else:
                StartupManager.install()

        return TrayMenu(
            TrayItem("Lumi Voice Assistant", None, enabled=False),
            TrayMenu.SEPARATOR,
            TrayItem("Status",          lambda: self._action_status()),
            TrayItem(mute_label,        lambda: self._action_mute_toggle()),
            TrayItem("Restart",         lambda: self._action_restart()),
            TrayMenu.SEPARATOR,
            TrayItem("Open .env settings", lambda: self._action_open_settings()),
            TrayItem(auto_label,        lambda: toggle_autostart()),
            TrayMenu.SEPARATOR,
            TrayItem("Quit Lumi",       lambda: self._action_quit()),
        )

    def _refresh_menu(self) -> None:
        if self._icon:
            try:
                self._icon.menu = self._build_menu()
                self._icon.update_menu()
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def run(self) -> None:
        """
        Start the tray icon. Blocks until the icon is stopped.
        Call this on the main thread (pystray requirement on Windows).
        """
        if not _HAS_PYSTRAY:
            logger.warning(
                "pystray not installed — tray disabled. "
                "Run:  pip install pystray Pillow"
            )
            # Fall back to blocking on the assistant stop event
            try:
                while not self.assistant._stop_event.is_set():
                    time.sleep(1)
            except KeyboardInterrupt:
                pass
            return

        _save_icons()
        img = self._current_image()
        if img is None:
            logger.error("Could not create tray icon image.")
            return

        self._icon = pystray.Icon(
            name="lumi",
            icon=img,
            title="Lumi — Idle",
            menu=self._build_menu(),
        )
        logger.info("System tray icon starting")
        self._icon.run()   # blocks until icon.stop() is called

    def stop(self) -> None:
        if self._icon:
            try:
                self._icon.stop()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# 5. Graceful shutdown hooks
# ---------------------------------------------------------------------------

def _install_shutdown_hooks(assistant, tray: Optional[LumiTray]) -> None:
    """
    Catch Ctrl+C, SIGTERM, and Windows session-end so Lumi
    always saves its state and exits cleanly.
    """
    def _shutdown(signum=None, frame=None):
        logger.info("Shutdown signal received (signum=%s)", signum)
        try:
            assistant.stop_background()
        except Exception:
            pass
        if tray:
            try:
                tray.stop()
            except Exception:
                pass
        sys.exit(0)

    signal.signal(signal.SIGINT,  _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    # Windows console close / session end
    try:
        import win32api
        win32api.SetConsoleCtrlHandler(lambda _: (_shutdown(), True)[1], True)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def run_with_tray(no_tray: bool = False) -> None:
    """
    Full startup sequence:
      1. Check single-instance mutex
      2. Load .env + build assistant
      3. Start background engine
      4. Show tray icon (blocks until quit)
    """
    from pathlib import Path as _Path
    # Load .env
    try:
        from dotenv import load_dotenv
        load_dotenv(override=False)
    except ImportError:
        pass

    # Single-instance guard
    guard = SingleInstance()
    if not guard.acquire():
        print("Lumi is already running. Check the system tray.")
        sys.exit(1)

    # Build assistant (imports from assistant.py in the same folder)
    sys.path.insert(0, str(_HERE))
    from assistant import VoiceAssistant, AssistantConfig

    config    = AssistantConfig.from_env()
    assistant = VoiceAssistant(config)

    tray = None if no_tray else LumiTray(assistant)

    # Wire tray events into assistant callback
    if tray:
        original_cb = assistant.event_callback
        def combined_cb(kind: str, text: str) -> None:
            tray.on_event(kind, text)
            if original_cb:
                try:
                    original_cb(kind, text)
                except Exception:
                    pass
        assistant.event_callback = combined_cb

    _install_shutdown_hooks(assistant, tray)

    # Start the always-on engine
    assistant.start_background()
    logger.info("Lumi started in background mode")

    if no_tray:
        # Headless: block until stop event
        try:
            while not assistant._stop_event.is_set():
                time.sleep(1)
        except KeyboardInterrupt:
            pass
        assistant.stop_background()
        guard.release()
        return

    # Run tray (blocks on main thread — pystray requirement)
    try:
        if tray:
            tray.run()
    except Exception as exc:
        logger.error("Tray crashed: %s", exc)
    finally:
        assistant.stop_background()
        guard.release()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )

    parser = argparse.ArgumentParser(description="Lumi — Background Service")
    parser.add_argument(
        "--install",
        action="store_true",
        help="Register Lumi in Windows Task Scheduler (runs at login)",
    )
    parser.add_argument(
        "--uninstall",
        action="store_true",
        help="Remove Lumi from Windows Task Scheduler",
    )
    parser.add_argument(
        "--no-tray",
        action="store_true",
        help="Run headless (no tray icon) — for servers or debugging",
    )
    args = parser.parse_args()

    if args.install:
        StartupManager.install()
        return

    if args.uninstall:
        StartupManager.uninstall()
        return

    run_with_tray(no_tray=args.no_tray)


if __name__ == "__main__":
    main()
