"""
lumi_always_on.py — Phase 1: Always-On Engine
=============================================
Adds two activation paths to Lumi:

  1. Wake word  — "Hey Lumi" (or any built-in / custom Porcupine model)
                  Runs entirely offline on a dedicated thread, ~1% CPU.

  2. Global hotkey — Ctrl+Space (configurable) works from any app,
                     even when Lumi's window is not focused.

Both paths call the same on_trigger() callback, which wakes Lumi,
animates the pill UI, runs one listen-process-respond cycle, then
returns to idle. A configurable cooldown prevents double-fires.

Setup
-----
1. Install deps:
       pip install pvporcupine pyaudio keyboard

2. Get a free Porcupine access key:
       https://console.picovoice.ai  (no credit card required)

3. Add to your .env:
       PORCUPINE_ACCESS_KEY=your_key_here
       PORCUPINE_KEYWORD=computer           # built-in fallback
       # or for a custom "Hey Lumi" model trained on the console:
       PORCUPINE_MODEL_PATH=hey-lumi_en_windows_v3.ppn
       PORCUPINE_SENSITIVITY=0.5            # 0.0 – 1.0
       LUMI_HOTKEY=ctrl+space               # any combo keyboard supports

Built-in keywords (no training needed, free):
    alexa, computer, hey google, hey siri, jarvis, ok google,
    picovoice, porcupine, terminator, bumblebee
"""

import logging
import os
import struct
import threading
import time
from typing import Callable, Optional


# ---------------------------------------------------------------------------
# Optional deps — graceful degradation if not installed
# ---------------------------------------------------------------------------

try:
    import pvporcupine
except ImportError:
    pvporcupine = None

try:
    import pyaudio
except ImportError:
    try:
        import pyaudiowpatch as pyaudio
    except ImportError:
        pyaudio = None

try:
    import keyboard
except ImportError:
    keyboard = None


# ---------------------------------------------------------------------------
# AlwaysOnEngine
# ---------------------------------------------------------------------------

class AlwaysOnEngine:
    """
    Manages wake word detection (Porcupine) and a global hotkey.
    Calls on_trigger(source) whenever Lumi should activate.

    source is either "wake_word" or "hotkey" — useful for
    emitting different UI states or logging.
    """

    def __init__(
        self,
        access_key: str = "",
        keyword: str = "computer",
        keyword_path: str = "",
        hotkey: str = "ctrl+space",
        sensitivity: float = 0.5,
        cooldown_secs: float = 2.5,
        on_trigger: Optional[Callable[[str], None]] = None,
    ):
        self.access_key = access_key.strip()
        self.keyword = keyword.strip().lower()
        self.keyword_path = keyword_path.strip()
        self.hotkey = hotkey.strip()
        self.sensitivity = max(0.0, min(1.0, sensitivity))
        self.cooldown_secs = cooldown_secs
        self.on_trigger = on_trigger or (lambda source: None)

        self.logger = logging.getLogger("lumi.always_on")
        self._stop_event = threading.Event()
        self._cooldown_lock = threading.Lock()
        self._in_cooldown = False
        self._wake_thread: Optional[threading.Thread] = None
        self._hotkey_registered = False

    # ------------------------------------------------------------------
    # Trigger + cooldown
    # ------------------------------------------------------------------

    def _trigger(self, source: str) -> None:
        """
        Called when wake word or hotkey fires.
        Enforces a cooldown so two rapid triggers don't stack.
        """
        with self._cooldown_lock:
            if self._in_cooldown:
                self.logger.debug("Trigger ignored (cooldown active, source=%s)", source)
                return
            self._in_cooldown = True

        self.logger.info("Lumi activated via %s", source)

        # Run callback in its own thread so we don't block the audio loop
        threading.Thread(
            target=self._run_trigger,
            args=(source,),
            daemon=True,
            name=f"lumi-trigger-{source}",
        ).start()

    def _run_trigger(self, source: str) -> None:
        try:
            self.on_trigger(source)
        except Exception as exc:
            self.logger.error("on_trigger callback raised: %s", exc)
        finally:
            # Wait until after the callback completes, THEN start cooldown timer
            def _release():
                time.sleep(self.cooldown_secs)
                with self._cooldown_lock:
                    self._in_cooldown = False
                self.logger.debug("Cooldown released")

            threading.Thread(target=_release, daemon=True).start()

    # ------------------------------------------------------------------
    # Wake word (Porcupine)
    # ------------------------------------------------------------------

    def _build_porcupine(self):
        """
        Create and return a Porcupine instance.
        Prefers custom .ppn file over built-in keyword.
        Raises RuntimeError with a friendly message on failure.
        """
        if pvporcupine is None:
            raise RuntimeError(
                "pvporcupine is not installed. Run:  pip install pvporcupine"
            )
        if pyaudio is None:
            raise RuntimeError(
                "pyaudio is not installed. Run:  pip install pyaudio"
            )
        if not self.access_key:
            raise RuntimeError(
                "PORCUPINE_ACCESS_KEY is missing. "
                "Get a free key at https://console.picovoice.ai"
            )

        if self.keyword_path and os.path.isfile(self.keyword_path):
            self.logger.info("Porcupine: loading custom model '%s'", self.keyword_path)
            return pvporcupine.create(
                access_key=self.access_key,
                keyword_paths=[self.keyword_path],
                sensitivities=[self.sensitivity],
            )

        self.logger.info(
            "Porcupine: using built-in keyword '%s' (sensitivity=%.2f)",
            self.keyword,
            self.sensitivity,
        )
        return pvporcupine.create(
            access_key=self.access_key,
            keywords=[self.keyword],
            sensitivities=[self.sensitivity],
        )

    def _wake_word_loop(self) -> None:
        """
        Runs on a daemon thread. Continuously feeds microphone audio
        to Porcupine and calls _trigger() on a match.
        Auto-restarts on transient audio errors.
        """
        pa = None
        porcupine = None
        stream = None

        try:
            porcupine = self._build_porcupine()
        except RuntimeError as exc:
            self.logger.error("Wake word disabled: %s", exc)
            return
        except Exception as exc:
            self.logger.error("Porcupine init failed: %s", exc)
            return

        try:
            pa = pyaudio.PyAudio()
            stream = pa.open(
                rate=porcupine.sample_rate,
                channels=1,
                format=pyaudio.paInt16,
                input=True,
                frames_per_buffer=porcupine.frame_length,
            )
            self.logger.info(
                "Wake word engine running (sample_rate=%d, frame_length=%d)",
                porcupine.sample_rate,
                porcupine.frame_length,
            )

            consecutive_errors = 0

            while not self._stop_event.is_set():
                try:
                    raw = stream.read(porcupine.frame_length, exception_on_overflow=False)
                    pcm = struct.unpack_from("h" * porcupine.frame_length, raw)
                    result = porcupine.process(pcm)
                    if result >= 0:
                        self.logger.info(
                            "Wake word detected (keyword_index=%d)", result
                        )
                        self._trigger("wake_word")
                    consecutive_errors = 0

                except OSError as exc:
                    consecutive_errors += 1
                    self.logger.warning(
                        "Audio read error #%d: %s", consecutive_errors, exc
                    )
                    time.sleep(0.05)
                    # After 20 consecutive errors (~1 s) try to recover the stream
                    if consecutive_errors >= 20:
                        self.logger.warning("Too many audio errors — restarting stream")
                        break

        except Exception as exc:
            self.logger.error("Wake word loop crashed: %s", exc)

        finally:
            for obj, method in [(stream, "close"), (pa, "terminate"), (porcupine, "delete")]:
                if obj is not None:
                    try:
                        getattr(obj, method)()
                    except Exception:
                        pass

        # Auto-restart after a brief pause unless stop was requested
        if not self._stop_event.is_set():
            self.logger.info("Restarting wake word engine in 3 s …")
            time.sleep(3)
            self._wake_thread = threading.Thread(
                target=self._wake_word_loop, daemon=True, name="lumi-wake-word"
            )
            self._wake_thread.start()

    # ------------------------------------------------------------------
    # Global hotkey
    # ------------------------------------------------------------------

    def _register_hotkey(self) -> None:
        if keyboard is None:
            self.logger.warning(
                "keyboard package not installed — hotkey disabled. "
                "Run:  pip install keyboard"
            )
            return
        try:
            keyboard.add_hotkey(
                self.hotkey,
                lambda: self._trigger("hotkey"),
                suppress=False,
            )
            self._hotkey_registered = True
            self.logger.info("Global hotkey registered: %s", self.hotkey)
        except Exception as exc:
            self.logger.error(
                "Could not register hotkey '%s': %s  "
                "(try running as Administrator for Win+key combos)",
                self.hotkey,
                exc,
            )

    def _unregister_hotkey(self) -> None:
        if not self._hotkey_registered or keyboard is None:
            return
        try:
            keyboard.remove_hotkey(self.hotkey)
            self._hotkey_registered = False
            self.logger.info("Global hotkey unregistered: %s", self.hotkey)
        except Exception as exc:
            self.logger.debug("Hotkey removal: %s", exc)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start wake word thread and register hotkey."""
        self._stop_event.clear()
        self._in_cooldown = False

        # Wake word thread
        self._wake_thread = threading.Thread(
            target=self._wake_word_loop,
            daemon=True,
            name="lumi-wake-word",
        )
        self._wake_thread.start()

        # Global hotkey
        self._register_hotkey()

        self.logger.info(
            "AlwaysOnEngine started  |  hotkey=%s  |  keyword='%s'",
            self.hotkey,
            self.keyword_path or self.keyword,
        )

    def stop(self) -> None:
        """Stop the wake word thread and unregister the hotkey."""
        self._stop_event.set()
        self._unregister_hotkey()
        self.logger.info("AlwaysOnEngine stopped")

    @property
    def wake_word_active(self) -> bool:
        return self._wake_thread is not None and self._wake_thread.is_alive()

    @property
    def hotkey_active(self) -> bool:
        return self._hotkey_registered


# ---------------------------------------------------------------------------
# Helpers — called from the main assistant file
# ---------------------------------------------------------------------------

def build_always_on_engine(
    assistant,  # VoiceAssistant instance
) -> "AlwaysOnEngine":
    """
    Factory that reads config from the assistant's AssistantConfig
    and wires on_trigger back into the assistant's activate_once() method.
    """
    cfg = assistant.config

    def on_trigger(source: str) -> None:
        assistant.logger.info("AlwaysOn trigger received (source=%s)", source)
        assistant._emit("status", "Listening...")

        command = assistant.listen()
        if not command:
            assistant._emit("status", "Idle")
            return

        assistant._emit("user", command)
        should_exit = assistant.process_command(command, require_wake_word=False)
        assistant._emit("status", "Idle")

        if should_exit:
            assistant.stop_background()

    return AlwaysOnEngine(
        access_key=getattr(cfg, "porcupine_access_key", ""),
        keyword=getattr(cfg, "porcupine_keyword", "computer"),
        keyword_path=getattr(cfg, "porcupine_model_path", ""),
        hotkey=getattr(cfg, "lumi_hotkey", "ctrl+space"),
        sensitivity=getattr(cfg, "porcupine_sensitivity", 0.5),
        on_trigger=on_trigger,
    )
