import argparse
import ast
import base64
import difflib
import json
import logging
import operator as _operator
import os
import random
import re
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, List, Optional, Tuple

import pyautogui
import pyttsx3

try:
    import pyaudio  # type: ignore
except Exception:
    try:
        import pyaudiowpatch as pyaudio  # type: ignore
        sys.modules.setdefault("pyaudio", pyaudio)
    except Exception:
        pyaudio = None

import speech_recognition as sr

try:
    from PIL import ImageGrab, ImageOps, ImageFilter
except Exception:
    ImageGrab = ImageOps = ImageFilter = None

try:
    import pytesseract
except Exception:
    pytesseract = None

try:
    import psutil
except Exception:
    psutil = None

try:
    from dotenv import load_dotenv
except Exception:
    load_dotenv = None

try:
    from openai import OpenAI
except Exception:
    OpenAI = None

from lumi_always_on import build_always_on_engine
from lumi_windows_api import (
    NEW_ALLOWED_ACTIONS,
    handle_windows_api_intent,
)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

APP_COMMANDS = {
    "notepad": ["notepad"],
    "calculator": ["calc"],
    "chrome": ["cmd", "/c", "start", "", "chrome"],
    "edge": ["cmd", "/c", "start", "", "msedge"],
    "vscode": ["code"],
    "explorer": ["explorer"],
    "paint": ["mspaint"],
    "word": ["cmd", "/c", "start", "", "winword"],
    "excel": ["cmd", "/c", "start", "", "excel"],
    "task manager": ["taskmgr"],
    "control panel": ["control"],
    "settings": ["cmd", "/c", "start", "", "ms-settings:"],
    "cmd": ["cmd"],
    "terminal": ["wt"],
    "powershell": ["powershell"],
}

APP_ALIASES = {
    "visual studio code": "vscode",
    "vs code": "vscode",
    "file explorer": "explorer",
    "files": "explorer",
    "ms paint": "paint",
    "microsoft word": "word",
    "microsoft excel": "excel",
    "command prompt": "cmd",
    "windows terminal": "terminal",
    "task man": "task manager",
}

KNOWN_WEBSITES = {
    "my portfolio": "https://satyam-pandey27.web.app/",
    "yt": "https://www.youtube.com",
    "youtube": "https://www.youtube.com",
    "google": "https://www.google.com",
    "gmail": "https://mail.google.com",
    "github": "https://github.com",
    "chatgpt": "https://chatgpt.com",
    "openai": "https://openai.com",
    "linkedin": "https://www.linkedin.com",
    "reddit": "https://www.reddit.com",
    "stackoverflow": "https://stackoverflow.com",
    "stack overflow": "https://stackoverflow.com",
    "wikipedia": "https://www.wikipedia.org",
    "amazon": "https://www.amazon.com",
    "netflix": "https://www.netflix.com",
    "x": "https://x.com",
    "twitter": "https://x.com",
    "instagram": "https://www.instagram.com",
    "facebook": "https://www.facebook.com",
    "notion": "https://www.notion.so",
    "figma": "https://www.figma.com",
    "vercel": "https://vercel.com",
    "netlify": "https://netlify.com",
    "anthropic": "https://www.anthropic.com",
    "claude": "https://claude.ai",
    "gemini": "https://gemini.google.com",
    "perplexity": "https://www.perplexity.ai",
    "huggingface": "https://huggingface.co",
}

WEATHER_CODES = {
    0: "clear sky", 1: "mostly clear", 2: "partly cloudy", 3: "overcast",
    45: "foggy", 48: "rime fog", 51: "light drizzle", 53: "drizzle",
    55: "dense drizzle", 61: "light rain", 63: "rain", 65: "heavy rain",
    71: "light snow", 73: "snow", 75: "heavy snow", 80: "light rain showers",
    81: "rain showers", 82: "heavy rain showers", 95: "thunderstorm",
}

FOLDER_MAP = {
    "download": str(Path.home() / "Downloads"),
    "desktop": str(Path.home() / "Desktop"),
    "document": str(Path.home() / "Documents"),
    "picture": str(Path.home() / "Pictures"),
    "music": str(Path.home() / "Music"),
    "video": str(Path.home() / "Videos"),
    "temp": str(Path(os.environ.get("TEMP", "C:/Temp"))),
}

JOKES = [
    "Why do programmers prefer dark mode? Because light attracts bugs!",
    "Why did the developer go broke? Because he used up all his cache.",
    "A SQL query walks into a bar, walks up to two tables and asks: Can I join you?",
    "Why do Python programmers wear glasses? Because they can't C.",
    "What is a computer's favorite snack? Microchips!",
    "Why did the computer go to the doctor? Because it had a virus.",
    "How many programmers does it take to change a light bulb? None. That's a hardware problem.",
    "Why was the JavaScript developer sad? Because he didn't know how to null his feelings.",
    "What do you call a programmer from Finland? Nerdic.",
    "I told my computer I needed a break. Now it won't stop sending me Kit-Kat ads.",
    "Why don't scientists trust atoms? Because they make up everything.",
    "I asked my assistant to make me a sandwich. She said 'Sudo make me a sandwich'. Now we're married.",
]

ALLOWED_ACTIONS = {
    "open_app", "open_url", "search_web", "play_youtube", "play_first_video",
    "type_text", "volume_up", "volume_down", "volume_mute", "weather_forecast",
    "describe_screen", "scroll_up", "scroll_down", "scroll_top", "scroll_bottom",
    "video_toggle", "video_next", "video_previous", "video_forward", "video_back",
    "video_fullscreen", "video_mute", "tell_name", "shutdown", "restart",
    "tell_time", "tell_date", "help", "exit", "unknown",
    # New actions
    "take_screenshot", "read_clipboard", "copy_last", "set_timer", "cancel_timer",
    "make_note", "read_notes", "clear_notes", "math_calc", "wikipedia_lookup",
    "system_info", "ip_address", "media_next", "media_prev", "media_play_pause",
    "window_minimize", "window_maximize", "window_close", "open_folder",
    "tell_joke", "clear_learned", "repeat_last",
}
ALLOWED_ACTIONS.update(NEW_ALLOWED_ACTIONS)


# ---------------------------------------------------------------------------
# Safe math evaluator
# ---------------------------------------------------------------------------

_SAFE_MATH_OPS = {
    ast.Add: _operator.add,
    ast.Sub: _operator.sub,
    ast.Mult: _operator.mul,
    ast.Div: _operator.truediv,
    ast.Pow: _operator.pow,
    ast.Mod: _operator.mod,
    ast.FloorDiv: _operator.floordiv,
    ast.USub: _operator.neg,
    ast.UAdd: _operator.pos,
}


def _safe_eval_ast(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.BinOp):
        op_fn = _SAFE_MATH_OPS.get(type(node.op))
        if op_fn is None:
            raise ValueError(f"Unsupported operator: {type(node.op).__name__}")
        left = _safe_eval_ast(node.left)
        right = _safe_eval_ast(node.right)
        if op_fn is _operator.truediv and right == 0:
            raise ZeroDivisionError("division by zero")
        return op_fn(left, right)
    if isinstance(node, ast.UnaryOp):
        op_fn = _SAFE_MATH_OPS.get(type(node.op))
        if op_fn is None:
            raise ValueError(f"Unsupported unary operator")
        return op_fn(_safe_eval_ast(node.operand))
    raise ValueError(f"Unsupported expression node: {type(node).__name__}")


# ---------------------------------------------------------------------------
# Utility functions
# ---------------------------------------------------------------------------

def _env_bool(name: str, default: bool) -> bool:
    val = os.getenv(name)
    if val is None:
        return default
    return val.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    val = os.getenv(name)
    if val is None:
        return default
    try:
        return int(val.strip())
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    val = os.getenv(name)
    if val is None:
        return default
    try:
        return float(val.strip())
    except ValueError:
        return default


def _normalize_url(raw: str) -> str:
    text = raw.strip().rstrip(".,!?")
    if not text:
        return ""
    if not text.startswith(("http://", "https://")):
        text = f"https://{text}"
    return text


def _is_probable_url(value: str) -> bool:
    try:
        p = urllib.parse.urlparse(value)
        return p.scheme in {"http", "https"} and "." in p.netloc
    except Exception:
        return False


def _to_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts = []
        for item in value:
            if isinstance(item, dict):
                t = item.get("text") or item.get("content") or ""
                if t:
                    parts.append(str(t))
            elif item:
                parts.append(str(item))
        return " ".join(parts)
    return str(value)


def _known_site(name: str) -> str:
    cleaned = name.strip().lower()
    cleaned = re.sub(r"^(the\s+)?(site|website|link)\s+", "", cleaned)
    return KNOWN_WEBSITES.get(cleaned, "")


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class Intent:
    action: str
    target: str = ""
    text: str = ""


@dataclass
class AssistantConfig:
    assistant_name: str = "Lumi"
    assistant_voice: str = "female"
    ai_provider: str = "openai"
    screen_read_mode: str = "auto"
    learning_enabled: bool = True
    learning_file: str = "lumi_learning.json"
    notes_file: str = "lumi_notes.txt"
    use_ai_intent: bool = False
    openai_model: str = "gpt-4o-mini"
    vision_model: str = "gpt-4o-mini"
    gemini_model: str = "gemini-2.0-flash"
    gemini_vision_model: str = "gemini-2.0-flash"
    whisper_model: str = "gpt-4o-mini-transcribe"
    stt_mode: str = "google"
    wake_word: str = ""
    porcupine_access_key: str = ""
    porcupine_keyword: str = "computer"
    porcupine_model_path: str = ""
    porcupine_sensitivity: float = 0.5
    lumi_hotkey: str = "ctrl+space"
    allow_power_actions: bool = False
    voice_rate: int = 180
    type_interval: float = 0.03
    max_type_chars: int = 400
    weather_default_city: str = ""
    weather_unit: str = "celsius"
    enable_ui: bool = True
    tesseract_cmd: str = ""
    log_file: str = "assistant.log"

    @classmethod
    def from_env(cls) -> "AssistantConfig":
        unit = os.getenv("WEATHER_UNIT", "celsius").strip().lower()
        if unit not in {"celsius", "fahrenheit"}:
            unit = "celsius"
        provider = os.getenv("AI_PROVIDER", "openai").strip().lower()
        if provider not in {"openai", "gemini"}:
            provider = "openai"
        screen_mode = os.getenv("SCREEN_READ_MODE", "auto").strip().lower()
        if screen_mode not in {"auto", "offline", "cloud"}:
            screen_mode = "auto"
        return cls(
            assistant_name=os.getenv("ASSISTANT_NAME", "Lumi").strip() or "Lumi",
            assistant_voice=os.getenv("ASSISTANT_VOICE", "female").strip().lower() or "female",
            ai_provider=provider,
            screen_read_mode=screen_mode,
            learning_enabled=_env_bool("LEARNING_ENABLED", True),
            learning_file=os.getenv("LEARNING_FILE", "lumi_learning.json").strip() or "lumi_learning.json",
            notes_file=os.getenv("NOTES_FILE", "lumi_notes.txt").strip() or "lumi_notes.txt",
            use_ai_intent=_env_bool("USE_AI_INTENT", False),
            openai_model=os.getenv("OPENAI_MODEL", "gpt-4o-mini").strip(),
            vision_model=os.getenv("VISION_MODEL", "gpt-4o-mini").strip(),
            gemini_model=os.getenv("GEMINI_MODEL", "gemini-2.0-flash").strip(),
            gemini_vision_model=os.getenv("GEMINI_VISION_MODEL", "gemini-2.0-flash").strip(),
            whisper_model=os.getenv("WHISPER_MODEL", "gpt-4o-mini-transcribe").strip(),
            stt_mode=os.getenv("STT_MODE", "google").strip().lower(),
            wake_word=os.getenv("WAKE_WORD", "").strip().lower(),
            porcupine_access_key=os.getenv("PORCUPINE_ACCESS_KEY", "").strip(),
            porcupine_keyword=os.getenv("PORCUPINE_KEYWORD", "computer").strip().lower(),
            porcupine_model_path=os.getenv("PORCUPINE_MODEL_PATH", "").strip(),
            porcupine_sensitivity=_env_float("PORCUPINE_SENSITIVITY", 0.5),
            lumi_hotkey=os.getenv("LUMI_HOTKEY", "ctrl+space").strip().lower(),
            allow_power_actions=_env_bool("ALLOW_POWER_ACTIONS", False),
            voice_rate=_env_int("VOICE_RATE", 180),
            type_interval=max(0.0, _env_float("TYPE_INTERVAL", 0.03)),
            max_type_chars=max(50, _env_int("MAX_TYPE_CHARS", 400)),
            weather_default_city=os.getenv("WEATHER_DEFAULT_CITY", "").strip(),
            weather_unit=unit,
            enable_ui=_env_bool("ENABLE_UI", True),
            tesseract_cmd=os.getenv("TESSERACT_CMD", "").strip(),
            log_file=os.getenv("ASSISTANT_LOG_FILE", "assistant.log").strip() or "assistant.log",
        )


# ---------------------------------------------------------------------------
# Voice Assistant
# ---------------------------------------------------------------------------

class VoiceAssistant:
    def __init__(self, config: AssistantConfig, event_callback: Optional[Callable[[str, str], None]] = None):
        self.config = config
        self.event_callback = event_callback
        self.logger = self._build_logger(config.log_file)

        pyautogui.FAILSAFE = True
        self.engine = pyttsx3.init()
        self.engine.setProperty("rate", self.config.voice_rate)
        self._set_voice(self.config.assistant_voice)

        self.recognizer = sr.Recognizer()
        self.recognizer.dynamic_energy_threshold = True

        openai_required = (
            self.config.stt_mode == "whisper"
            or self.config.use_ai_intent
            or (self.config.ai_provider == "openai" and self.config.screen_read_mode == "cloud")
        )
        self.openai_client = self._build_openai_client(required=openai_required)
        self.gemini_api_key = os.getenv("GEMINI_API_KEY", "").strip()
        if self.config.ai_provider == "gemini" and not self.gemini_api_key:
            self.logger.warning("GEMINI_API_KEY is missing in environment.")
        if pytesseract is not None and self.config.tesseract_cmd:
            try:
                pytesseract.pytesseract.tesseract_cmd = self.config.tesseract_cmd
            except Exception as exc:
                self.logger.warning("Failed to set TESSERACT_CMD: %s", exc)

        self.learning_file = Path(self.config.learning_file)
        self.notes_file = Path(self.config.notes_file)
        self.learned_commands = self._load_learned_commands()
        self._seed_default_learning()

        self._last_user_command = ""
        self._last_intent = Intent(action="unknown")
        self._last_spoken = ""

        # Timer state
        self._timer_threads: List[threading.Thread] = []
        self._cancel_all_timers = False

        self._mic_ready = False
        self._stop_event = threading.Event()
        self._loop_thread: Optional[threading.Thread] = None
        self._speak_lock = threading.Lock()

        # Build intent rules last (needs self fully initialized)
        self._intent_rules = self._make_rules()
        self._always_on = build_always_on_engine(self)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _emit(self, kind: str, text: str) -> None:
        if self.event_callback:
            try:
                self.event_callback(kind, text)
            except Exception:
                pass

    def _build_logger(self, log_file: str) -> logging.Logger:
        logger = logging.getLogger("voice_assistant")
        if logger.handlers:
            return logger
        logger.setLevel(logging.INFO)
        fmt = logging.Formatter("%(asctime)s | %(levelname)s | %(message)s")
        sh = logging.StreamHandler()
        sh.setFormatter(fmt)
        logger.addHandler(sh)
        fh = logging.FileHandler(Path(log_file), encoding="utf-8")
        fh.setFormatter(fmt)
        logger.addHandler(fh)
        return logger

    def _set_voice(self, preference: str) -> None:
        voices = self.engine.getProperty("voices") or []
        if not voices:
            return
        pref = preference.lower()
        if pref == "female":
            keys = ["female", "zira", "hazel", "aria", "susan", "eva", "samantha"]
        elif pref == "male":
            keys = ["male", "david", "mark", "james", "guy"]
        else:
            keys = [pref]
        for voice in voices:
            blob = f"{getattr(voice, 'name', '')} {getattr(voice, 'id', '')}".lower()
            if any(k in blob for k in keys):
                self.engine.setProperty("voice", voice.id)
                self.logger.info("Voice selected: %s", getattr(voice, "name", voice.id))
                return

    def _build_openai_client(self, required: bool = False) -> Optional["OpenAI"]:
        if OpenAI is None:
            if required:
                self.logger.warning("openai package is not installed.")
            return None
        key = os.getenv("OPENAI_API_KEY", "").strip()
        if not key:
            if required:
                self.logger.warning("OPENAI_API_KEY is missing in environment.")
            return None
        return OpenAI(api_key=key)

    def speak(self, text: str) -> None:
        self.logger.info("Assistant: %s", text)
        self._emit("assistant", text)
        self._last_spoken = text
        print(f"{self.config.assistant_name}: {text}")
        with self._speak_lock:
            self.engine.say(text)
            self.engine.runAndWait()

    def _setup_microphone(self) -> bool:
        if self._mic_ready:
            return True
        try:
            self._emit("status", "Calibrating microphone...")
            with sr.Microphone() as source:
                self.recognizer.adjust_for_ambient_noise(source, duration=1)
            self._mic_ready = True
            return True
        except Exception as exc:
            self.logger.error("Mic setup failed: %s", exc)
            self._emit("status", "Microphone setup failed")
            return False

    def _transcribe_whisper(self, audio: sr.AudioData) -> str:
        if not self.openai_client:
            return ""
        path = ""
        try:
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
                tmp.write(audio.get_wav_data())
                path = tmp.name
            with open(path, "rb") as f:
                resp = self.openai_client.audio.transcriptions.create(
                    model=self.config.whisper_model, file=f
                )
            return (resp.text or "").strip().lower()
        except Exception as exc:
            self.logger.error("Whisper error: %s", exc)
            return ""
        finally:
            if path and os.path.exists(path):
                os.remove(path)

    def listen(self) -> str:
        if not self._setup_microphone():
            return ""
        with sr.Microphone() as source:
            self._emit("status", "Listening...")
            try:
                audio = self.recognizer.listen(source, timeout=6, phrase_time_limit=12)
            except sr.WaitTimeoutError:
                return ""
        try:
            if self.config.stt_mode == "whisper":
                return self._transcribe_whisper(audio)
            return self.recognizer.recognize_google(audio).strip().lower()
        except sr.UnknownValueError:
            return ""
        except Exception as exc:
            self.logger.error("Listen error: %s", exc)
            return ""

    def _extract_json(self, text: str) -> dict:
        payload = text.strip()
        if payload.startswith("{") and payload.endswith("}"):
            return json.loads(payload)
        m = re.search(r"\{.*\}", payload, flags=re.S)
        return json.loads(m.group(0)) if m else {}

    def _coerce_steps(self, raw: str, min_v: int, max_v: int, default: int) -> str:
        try:
            n = int(raw.strip()) if raw.strip() else default
        except ValueError:
            n = default
        n = max(min_v, min(max_v, n))
        return str(n)

    def _extract_weather_city(self, cmd: str) -> str:
        m = re.search(r"\b(?:in|for)\s+([a-z][a-z\s.'-]{1,70})", cmd, flags=re.I)
        if not m:
            return ""
        city = m.group(1)
        city = re.sub(r"\b(today|tomorrow|weather|forecast|temperature|please|now)\b", "", city, flags=re.I)
        city = re.sub(r"\s+", " ", city).strip(" ,.!?")
        return city.title()

    # ------------------------------------------------------------------
    # Learning
    # ------------------------------------------------------------------

    def _load_learned_commands(self) -> dict:
        if not self.config.learning_enabled:
            return {}
        if not self.learning_file.exists():
            return {}
        try:
            payload = json.loads(self.learning_file.read_text(encoding="utf-8"))
            items = payload.get("commands", {})
            if isinstance(items, dict):
                return items
        except Exception as exc:
            self.logger.warning("Failed to load learning file %s: %s", self.learning_file, exc)
        return {}

    def _save_learned_commands(self) -> None:
        if not self.config.learning_enabled:
            return
        try:
            payload = {"commands": self.learned_commands}
            self.learning_file.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        except Exception as exc:
            self.logger.warning("Failed to save learning file %s: %s", self.learning_file, exc)

    def _seed_default_learning(self) -> None:
        if not self.config.learning_enabled:
            return
        defaults = {
            "play the firt video": {"action": "play_first_video", "target": "", "text": ""},
            "play first video": {"action": "play_first_video", "target": "", "text": ""},
            "play the first video": {"action": "play_first_video", "target": "", "text": ""},
            "scrool down": {"action": "scroll_down", "target": "700", "text": ""},
            "scrool up": {"action": "scroll_up", "target": "700", "text": ""},
        }
        changed = False
        for phrase, intent_data in defaults.items():
            if phrase not in self.learned_commands:
                self.learned_commands[phrase] = intent_data
                changed = True
        if changed:
            self._save_learned_commands()

    def _learn_mapping(self, phrase: str, intent: Intent) -> None:
        if not self.config.learning_enabled:
            return
        source = phrase.strip().lower()
        if len(source) < 3:
            return
        self.learned_commands[source] = {
            "action": intent.action,
            "target": intent.target,
            "text": intent.text,
        }
        self._save_learned_commands()

    def _intent_from_learning(self, command: str) -> Optional[Intent]:
        if not self.config.learning_enabled or not self.learned_commands:
            return None
        key = command.strip().lower()
        if key in self.learned_commands:
            item = self.learned_commands[key]
            return Intent(
                action=str(item.get("action", "unknown")),
                target=str(item.get("target", "")),
                text=str(item.get("text", "")),
            )
        close = difflib.get_close_matches(key, list(self.learned_commands.keys()), n=1, cutoff=0.91)
        if close:
            item = self.learned_commands[close[0]]
            return Intent(
                action=str(item.get("action", "unknown")),
                target=str(item.get("target", "")),
                text=str(item.get("text", "")),
            )
        return None

    def _extract_correction_target(self, command: str) -> Optional[str]:
        patterns = (
            r"^no[, ]+i meant (.+)$",
            r"^i meant (.+)$",
            r"^actually[, ]+(.+)$",
            r"^not that[, ]+(.+)$",
        )
        for pattern in patterns:
            m = re.match(pattern, command.strip().lower())
            if m:
                return m.group(1).strip()
        return None

    def _extract_explicit_learning(self, command: str) -> Optional[tuple]:
        m = re.match(
            r"^(?:learn|remember)(?: that)?\s+(.+?)\s+(?:means|as|=)\s+(.+)$",
            command.strip().lower(),
        )
        if not m:
            return None
        source = m.group(1).strip()
        target = m.group(2).strip()
        if not source or not target:
            return None
        return source, target

    # ------------------------------------------------------------------
    # AI intent parsing
    # ------------------------------------------------------------------

    def _intent_from_payload(self, data: dict) -> Intent:
        action = str(data.get("action", "unknown")).strip().lower()
        target = str(data.get("target", "")).strip()
        text = str(data.get("text", "")).strip()
        if action not in ALLOWED_ACTIONS:
            return Intent(action="unknown")
        if action in {"volume_up", "volume_down"}:
            target = self._coerce_steps(target, 1, 30, 5)
        if action in {"scroll_up", "scroll_down"}:
            target = self._coerce_steps(target, 150, 3000, 700)
        if action == "open_app":
            target = APP_ALIASES.get(target.lower(), target.lower())
            if target not in APP_COMMANDS:
                return Intent(action="unknown")
        if action == "open_url":
            target = _known_site(target) or _normalize_url(target)
            if not _is_probable_url(target):
                return Intent(action="unknown")
        return Intent(action=action, target=target, text=text)

    def _gemini_generate_content(self, *, model: str, parts: list,
                                  temperature: float = 0.0,
                                  response_mime_type: Optional[str] = None) -> str:
        if not self.gemini_api_key:
            raise RuntimeError("GEMINI_API_KEY is missing.")
        model_name = urllib.parse.quote(model, safe="")
        api_key = urllib.parse.quote(self.gemini_api_key, safe="")
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={api_key}"
        body = {
            "contents": [{"role": "user", "parts": parts}],
            "generationConfig": {"temperature": temperature},
        }
        if response_mime_type:
            body["generationConfig"]["responseMimeType"] = response_mime_type
        payload = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=45) as resp:
                raw = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail_raw = exc.read().decode("utf-8", errors="ignore")
                detail_json = json.loads(detail_raw)
                detail = ((detail_json.get("error") or {}).get("message") or detail_raw).strip()
            except Exception:
                detail = str(exc)
            raise RuntimeError(f"Gemini API error ({exc.code}): {detail}") from exc
        if raw.get("error"):
            err = raw["error"]
            raise RuntimeError(f"Gemini API error: {err.get('message', err)}")
        candidates = raw.get("candidates") or []
        if not candidates:
            raise RuntimeError(f"Gemini returned no candidates: {raw}")
        content = candidates[0].get("content") or {}
        response_parts = content.get("parts") or []
        return " ".join(str(p["text"]) for p in response_parts if isinstance(p, dict) and p.get("text")).strip()

    def _parse_intent_with_openai(self, command: str, prompt: str) -> Intent:
        if not self.openai_client:
            return Intent(action="unknown")
        resp = self.openai_client.chat.completions.create(
            model=self.config.openai_model,
            temperature=0,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": command},
            ],
        )
        data = self._extract_json(_to_text(resp.choices[0].message.content) or "{}")
        return self._intent_from_payload(data)

    def _parse_intent_with_gemini(self, command: str, prompt: str) -> Intent:
        response_text = self._gemini_generate_content(
            model=self.config.gemini_model,
            parts=[{"text": f"{prompt}\n\nCommand: {command}"}],
            temperature=0.0,
            response_mime_type="application/json",
        )
        data = self._extract_json(response_text or "{}")
        return self._intent_from_payload(data)

    def parse_intent_ai(self, command: str) -> Intent:
        if not self.config.use_ai_intent:
            return Intent(action="unknown")
        prompt = (
            "Convert voice command to JSON with keys action, target, text. "
            "Allowed actions: open_app, open_url, search_web, play_youtube, play_first_video, "
            "type_text, volume_up, volume_down, volume_mute, weather_forecast, describe_screen, "
            "scroll_up, scroll_down, scroll_top, scroll_bottom, video_toggle, video_next, "
            "video_previous, video_forward, video_back, video_fullscreen, video_mute, "
            "tell_name, shutdown, restart, tell_time, tell_date, help, exit, "
            "take_screenshot, read_clipboard, copy_last, set_timer, cancel_timer, "
            "make_note, read_notes, clear_notes, math_calc, wikipedia_lookup, "
            "system_info, ip_address, media_next, media_prev, media_play_pause, "
            "window_minimize, window_maximize, window_close, open_folder, "
            "tell_joke, clear_learned, repeat_last, unknown. "
            "For set_timer: target=seconds as string, text=label. "
            "For math_calc: text=expression. "
            "For wikipedia_lookup: text=query. "
            "For system_info: text=battery|cpu|ram|disk|all. "
            "For open_folder: target=download|desktop|document|picture|music|video. "
            "Return only valid JSON."
        )
        try:
            if self.config.ai_provider == "gemini":
                return self._parse_intent_with_gemini(command, prompt)
            return self._parse_intent_with_openai(command, prompt)
        except Exception as exc:
            self.logger.warning("AI intent parse failed (%s): %s", self.config.ai_provider, exc)
            return Intent(action="unknown")

    # ------------------------------------------------------------------
    # Intent rule helpers
    # ------------------------------------------------------------------

    def _parse_timer_intent(self, cmd: str) -> Intent:
        m = re.search(r"(\d+)\s*(hour|minute|second|min|sec|hr)s?", cmd, re.I)
        if not m:
            return Intent(action="unknown")
        amount = int(m.group(1))
        unit = m.group(2).lower()
        if unit in ("hour", "hr"):
            seconds = amount * 3600
        elif unit in ("minute", "min"):
            seconds = amount * 60
        else:
            seconds = amount
        label = re.sub(r"(set\s+(a\s+)?timer\s+for\s+|\d+\s*(hour|minute|second|min|sec|hr)s?|timer)", "", cmd, flags=re.I).strip()
        return Intent(action="set_timer", target=str(seconds), text=label)

    def _parse_weather_intent(self, cmd: str) -> Intent:
        city = self._extract_weather_city(cmd) or self.config.weather_default_city
        day = "tomorrow" if "tomorrow" in cmd else "today"
        return Intent(action="weather_forecast", target=city, text=day)

    def _parse_open_intent(self, target_phrase: str) -> Intent:
        cleaned = re.sub(r"^(the\s+)?(site|website|link)\s+", "", target_phrase.strip().lower())
        app = APP_ALIASES.get(cleaned, cleaned)
        if app in APP_COMMANDS:
            return Intent(action="open_app", target=app)
        site = _known_site(cleaned)
        if site:
            return Intent(action="open_url", target=site)
        url = _normalize_url(cleaned)
        if _is_probable_url(url):
            return Intent(action="open_url", target=url)
        return Intent(action="unknown")

    # ------------------------------------------------------------------
    # Intent rules builder
    # ------------------------------------------------------------------

    def _make_rules(self) -> List[Tuple]:
        c = re.compile
        I = re.IGNORECASE
        name = re.escape(self.config.assistant_name.lower())

        rules = [
            # --- Exit ---
            (c(rf"^(stop|sotp|exit|quit|goodbye|stop now|stop assistant|stop lumi|stop {name})$", I),
             lambda m, cmd: Intent(action="exit")),

            # --- Identity ---
            (c(r"(what('?s| is) your name|who are you|tell me your name|your name)", I),
             lambda m, cmd: Intent(action="tell_name")),

            # --- Time & Date ---
            (c(r"(what (time|is the time)|current time|time now|what time is it)", I),
             lambda m, cmd: Intent(action="tell_time")),
            (c(r"(what (date|day|is today)|today'?s date|current date|what day is it)", I),
             lambda m, cmd: Intent(action="tell_date")),

            # --- Help ---
            (c(r"^(help|what can you do|show commands|list commands|commands)$", I),
             lambda m, cmd: Intent(action="help")),

            # --- Screen ---
            (c(r"(read (my )?screen|describe screen|what'?s? on (my )?screen|what is on (my )?screen)", I),
             lambda m, cmd: Intent(action="describe_screen",
                                   text=re.sub(r"(read (my )?screen|describe screen|what'?s? on (my )?screen|what is on (my )?screen)", "", cmd, flags=I).strip())),

            # --- Screenshot ---
            (c(r"(take|save|capture|grab) (a )?screenshot", I),
             lambda m, cmd: Intent(action="take_screenshot")),

            # --- Clipboard ---
            (c(r"(read|what'?s? in|show|get) (my )?clipboard", I),
             lambda m, cmd: Intent(action="read_clipboard")),

            # --- Copy last ---
            (c(r"(copy (that|last response|last answer|last|it)|copy to clipboard)", I),
             lambda m, cmd: Intent(action="copy_last")),

            # --- Repeat last ---
            (c(r"(repeat|say that again|what did you say)", I),
             lambda m, cmd: Intent(action="repeat_last")),

            # --- Timer ---
            (c(r"(set|start|create) (a )?timer for \d+", I),
             lambda m, cmd: self._parse_timer_intent(cmd)),
            (c(r"\d+\s*(hour|minute|second|min|sec|hr)s? timer", I),
             lambda m, cmd: self._parse_timer_intent(cmd)),
            (c(r"remind me in \d+", I),
             lambda m, cmd: self._parse_timer_intent(cmd)),
            (c(r"(cancel|stop|clear|kill) (all |the )?timers?", I),
             lambda m, cmd: Intent(action="cancel_timer")),

            # --- Notes ---
            (c(r"(make|take|add|save|create|jot down) (a )?(note|reminder)[:\s]+(.+)", I),
             lambda m, cmd: Intent(action="make_note",
                                   text=re.split(r"(?:make|take|add|save|create|jot down)\s+(?:a\s+)?(?:note|reminder)[:\s]+", cmd, maxsplit=1, flags=I)[-1].strip())),
            (c(r"(read|show|list|tell me|get|view) (my )?(notes?|reminders?)", I),
             lambda m, cmd: Intent(action="read_notes")),
            (c(r"(clear|delete|remove|wipe) (all |my )?(notes?|reminders?)", I),
             lambda m, cmd: Intent(action="clear_notes")),

            # --- Math (explicit keywords) ---
            (c(r"\b(calculate|compute|eval(?:uate)?|solve|math)\b\s*(.+)", I),
             lambda m, cmd: Intent(action="math_calc", text=m.group(2).strip())),

            # --- Math (what is + starts with digit) ---
            (c(r"what(?:'?s| is)\s+(\d+\b.{1,80})", I),
             lambda m, cmd: Intent(action="math_calc", text=m.group(1).strip())),

            # --- Battery (Phase 2 specific) ---
            (c(r"(battery|how much battery|battery status|battery life)", I),
             lambda m, cmd: Intent(action="battery_status")),

            # --- System info ---
            (c(r"\b(battery|cpu|ram|memory|disk|storage)\b\s*(status|usage|info|level|percent)?", I),
             lambda m, cmd: Intent(action="system_info", text=m.group(1).lower())),
            (c(r"(system|pc|computer)\s*(info|status|stats|details)", I),
             lambda m, cmd: Intent(action="system_info", text="all")),

            # --- IP address ---
            (c(r"(my|what'?s? my|get my|show my|find my)\s*(public|local)?\s*i\.?p\.?\s*(address)?", I),
             lambda m, cmd: Intent(action="ip_address")),

            # --- Joke ---
            (c(r"(tell|say|give me)\s+(me\s+)?(a\s+)?(joke|something funny|funny)", I),
             lambda m, cmd: Intent(action="tell_joke")),

            # --- Clear learned ---
            (c(r"(forget|clear|reset) (all\s+)?(learned|learnt|custom) (commands?|phrases?|mappings?)", I),
             lambda m, cmd: Intent(action="clear_learned")),

            # --- Wikipedia / define (catch-all what is) ---
            (c(r"(what is|who is|tell me about|explain|define|what are|who are)\s+(.+)", I),
             lambda m, cmd: Intent(action="wikipedia_lookup",
                                   text=(m.group(2) or "").strip())),

            # --- Media controls ---
            (c(r"\b(next|skip)\s+track\b", I),
             lambda m, cmd: Intent(action="media_next")),
            (c(r"\b(previous|prev|last)\s+track\b", I),
             lambda m, cmd: Intent(action="media_prev")),
            (c(r"\b(play|pause|toggle)\s+(music|track|song|media)\b", I),
             lambda m, cmd: Intent(action="media_play_pause")),

            # --- Window controls ---
            (c(r"\b(minimize|minimise)\b", I),
             lambda m, cmd: Intent(action="window_minimize")),
            (c(r"\b(maximize|maximise|max window)\b", I),
             lambda m, cmd: Intent(action="window_maximize")),
            (c(r"\b(close|kill)\s+(window|this|app|current|tab)\b", I),
             lambda m, cmd: Intent(action="window_close")),

            # --- Open folders ---
            (c(r"(open|go to|show|navigate to)\s+(my\s+)?(downloads?|desktop|documents?|pictures?|music|videos?|temp)", I),
             lambda m, cmd: Intent(action="open_folder",
                                   target=re.sub(r"s$", "", (m.group(3) or "").strip().lower()))),

            # --- First video ---
            (c(r"\b(play|open|start)\b.*\b(fir(?:st|t)|1st)\b.*\b(video|result|youtube)\b", I),
             lambda m, cmd: Intent(action="play_first_video")),
            (c(r"^(play|open)\s+(the\s+)?first(\s+video)?$", I),
             lambda m, cmd: Intent(action="play_first_video")),

            # --- Video controls ---
            (c(r"\bstop\s+(?:the\s+|d\s+)?(?:video|youtube)\b", I),
             lambda m, cmd: Intent(action="video_toggle")),
            (c(r"\b(play this video|pause this video|video on screen)\b", I),
             lambda m, cmd: Intent(action="video_toggle")),
            (c(r"\b(play|pause|resume)\b.*\b(this|current)\b.*\b(video|youtube)\b", I),
             lambda m, cmd: Intent(action="video_toggle")),
            (c(r"\b(next video|skip video|skip to next)\b", I),
             lambda m, cmd: Intent(action="video_next")),
            (c(r"\b(previous video|prev(?:ious)? video|last video|go back video)\b", I),
             lambda m, cmd: Intent(action="video_previous")),
            (c(r"\b(fullscreen|full screen|maximize video)\b", I),
             lambda m, cmd: Intent(action="video_fullscreen")),
            (c(r"\b(video mute|mute video|unmute video)\b", I),
             lambda m, cmd: Intent(action="video_mute")),
            (c(r"\b(forward|skip ahead)\b.*\b(video|youtube)\b", I),
             lambda m, cmd: Intent(action="video_forward")),
            (c(r"\b(back|rewind)\b.*\b(video|youtube)\b", I),
             lambda m, cmd: Intent(action="video_back")),

            # --- Scroll ---
            (c(r"(?:scroll|scrool)\s+down(?:\s+(\d+))?", I),
             lambda m, cmd: Intent(action="scroll_down",
                                   target=self._coerce_steps(m.group(1) or "", 150, 3000, 700))),
            (c(r"(?:scroll|scrool)\s+up(?:\s+(\d+))?", I),
             lambda m, cmd: Intent(action="scroll_up",
                                   target=self._coerce_steps(m.group(1) or "", 150, 3000, 700))),
            (c(r"\b(?:scroll|go)\s+(?:to\s+)?top\b", I),
             lambda m, cmd: Intent(action="scroll_top")),
            (c(r"\b(?:scroll|go)\s+(?:to\s+)?bottom\b", I),
             lambda m, cmd: Intent(action="scroll_bottom")),

            # --- Weather ---
            (c(r"\b(weather|forecast|temperature)\b", I),
             lambda m, cmd: self._parse_weather_intent(cmd)),

            # --- Power ---
            (c(r"\b(shutdown|power off|turn off (my )?pc)\b", I),
             lambda m, cmd: Intent(action="shutdown")),
            (c(r"\b(restart|reboot)\b", I),
             lambda m, cmd: Intent(action="restart")),

            # --- Volume ---
            (c(r"(volume up|increase volume|louder)(?:\s+(\d+))?", I),
             lambda m, cmd: Intent(action="volume_up",
                                   target=self._coerce_steps(m.group(2) or "", 1, 30, 5))),
            (c(r"(volume down|decrease volume|quieter|lower volume)(?:\s+(\d+))?", I),
             lambda m, cmd: Intent(action="volume_down",
                                   target=self._coerce_steps(m.group(2) or "", 1, 30, 5))),
            (c(r"\bmute\b", I),
             lambda m, cmd: Intent(action="volume_mute")),

            # --- Type text ---
            (c(r"^(type|write)\s+(.+)$", I),
             lambda m, cmd: Intent(action="type_text",
                                   text=(m.group(2) or "")[:self.config.max_type_chars])),

            # --- YouTube / play ---
            (c(r"^(?:play|open|start)\s+(?:the\s+)?(?:video|song|track)\s+(?:of|by|from)\s+(.+)$", I),
             lambda m, cmd: Intent(action="play_youtube", text=m.group(1).strip())),
            (c(r"^(?:play|open|start)\s+video\s+(.+)$", I),
             lambda m, cmd: Intent(action="play_youtube", text=m.group(1).strip())
                            if not m.group(1).strip().startswith(("on screen", "here", "now"))
                            else Intent(action="unknown")),
            (c(r"^play\s+(.+?)\s+on\s+youtube$", I),
             lambda m, cmd: Intent(action="play_youtube", text=m.group(1).strip())),
            (c(r"^play\s+(.+)$", I),
             lambda m, cmd: Intent(action="play_youtube", text=m.group(1).strip())),

            # --- Search ---
            (c(r"^(search|google|find)\s+(.+)$", I),
             lambda m, cmd: Intent(action="search_web", text=m.group(2).strip())),

            # --- Brightness ---
            (c(r"(how bright|what('?s| is) the brightness|brightness level)", I),
             lambda m, cmd: Intent(action="get_brightness")),
            (c(r"(set|change) brightness (to\s+)?(\d+)", I),
             lambda m, cmd: Intent(action="set_brightness", target=(m.group(3) or "").strip())),
            (c(r"(increase|raise|turn up) (the\s+)?brightness(\s+(\d+))?", I),
             lambda m, cmd: Intent(action="increase_brightness", target=(m.group(4) or "10").strip())),
            (c(r"(decrease|lower|dim|turn down) (the\s+)?brightness(\s+(\d+))?", I),
             lambda m, cmd: Intent(action="decrease_brightness", target=(m.group(4) or "10").strip())),

            # --- Volume (exact level) ---
            (c(r"set (the\s+)?volume (to\s+)?(\d+)", I),
             lambda m, cmd: Intent(action="set_volume_level", target=(m.group(3) or "").strip())),

            # --- WiFi ---
            (c(r"(wifi|wi-fi|internet|network) (status|connected|connection)", I),
             lambda m, cmd: Intent(action="wifi_status")),
            (c(r"(list|show|what|scan)\s+(wifi|wi-fi|wireless|available)\s*(networks?|connections?)?", I),
             lambda m, cmd: Intent(action="wifi_list")),
            (c(r"(connect|join)\s+(to\s+)?(?:wifi\s+|network\s+)?(.+)", I),
             lambda m, cmd: Intent(
                 action="wifi_connect",
                 target=re.sub(r"^(connect|join)\s+(to\s+)?(wifi\s+|network\s+)?", "", cmd, flags=re.I).strip(),
             )),
            (c(r"(disconnect|leave)\s+(wifi|wi-fi|network|internet)", I),
             lambda m, cmd: Intent(action="wifi_disconnect")),

            # --- Bluetooth ---
            (c(r"(turn on|enable|start)\s+bluetooth", I),
             lambda m, cmd: Intent(action="bluetooth_on")),
            (c(r"(turn off|disable|stop)\s+bluetooth", I),
             lambda m, cmd: Intent(action="bluetooth_off")),
            (c(r"(scan|search|find|list)\s+(for\s+)?(bluetooth|bt)\s*(devices?|gadgets?)?", I),
             lambda m, cmd: Intent(action="bluetooth_scan")),

            # --- Dark / Light mode ---
            (c(r"(turn on|enable|switch to|use)\s+dark\s+mode", I),
             lambda m, cmd: Intent(action="dark_mode_on")),
            (c(r"(turn off|disable|switch to|use)\s+(light|day)\s+mode", I),
             lambda m, cmd: Intent(action="dark_mode_off")),
            (c(r"(toggle|switch)\s+dark\s+mode", I),
             lambda m, cmd: Intent(action="dark_mode_toggle")),

            # --- Night light ---
            (c(r"(turn on|enable)\s+night\s+light", I),
             lambda m, cmd: Intent(action="night_light_on")),
            (c(r"(turn off|disable)\s+night\s+light", I),
             lambda m, cmd: Intent(action="night_light_off")),

            # --- Focus Assist / DND ---
            (c(r"(turn on|enable)\s+(focus|do not disturb|dnd|quiet)", I),
             lambda m, cmd: Intent(action="focus_assist_on")),
            (c(r"(turn off|disable)\s+(focus|do not disturb|dnd|quiet)", I),
             lambda m, cmd: Intent(action="focus_assist_off")),

            # --- Power plan ---
            (c(r"(switch to|use|set)\s+(high\s+)?performance\s+(mode|plan)?", I),
             lambda m, cmd: Intent(action="power_plan", target="performance")),
            (c(r"(switch to|use|set)\s+(power\s+)?saver\s+(mode|plan)?", I),
             lambda m, cmd: Intent(action="power_plan", target="saver")),
            (c(r"(switch to|use|set)\s+balanced\s+(mode|plan)?", I),
             lambda m, cmd: Intent(action="power_plan", target="balanced")),

            # --- Battery (detailed mode) ---
            (c(r"(battery|how much battery|battery status|battery life)", I),
             lambda m, cmd: Intent(action="battery_status")),

            # --- Open (catch-all) ---
            (c(r"^(open|go to)\s+(.+)$", I),
             lambda m, cmd: self._parse_open_intent(m.group(2).strip())),
        ]
        return rules

    # ------------------------------------------------------------------
    # Intent parsing
    # ------------------------------------------------------------------

    def parse_intent_local(self, command: str) -> Intent:
        cmd = command.strip().lower()
        if not cmd:
            return Intent(action="unknown")

        # Check exact known website
        if cmd in KNOWN_WEBSITES:
            return Intent(action="open_url", target=KNOWN_WEBSITES[cmd])

        # Run through ordered rules
        for pattern, factory in self._intent_rules:
            m = pattern.search(cmd)
            if m:
                try:
                    result = factory(m, cmd)
                    if result.action != "unknown":
                        return result
                except Exception as exc:
                    self.logger.debug("Rule error: %s", exc)
                    continue

        # App name in command
        for app in APP_COMMANDS:
            if app in cmd:
                return Intent(action="open_app", target=app)

        return Intent(action="unknown")

    def parse_intent(self, command: str) -> Intent:
        learned = self._intent_from_learning(command)
        if learned and learned.action != "unknown":
            return learned
        ai = self.parse_intent_ai(command)
        if ai.action != "unknown":
            return ai
        return self.parse_intent_local(command)

    def _prepare_command(self, raw: str, require_wake_word: bool = True) -> str:
        cmd = raw.strip().lower()
        if not cmd:
            return ""
        if self.config.wake_word and require_wake_word:
            if self.config.wake_word not in cmd:
                return ""
            cmd = cmd.replace(self.config.wake_word, "", 1).strip()
        elif not self.config.wake_word:
            name = self.config.assistant_name.lower()
            cmd = re.sub(rf"^(hey\s+|hi\s+|ok\s+|okay\s+)?{re.escape(name)}[\s,.:;-]*", "", cmd).strip()
        return cmd

    def _confirm_action(self, prompt: str) -> bool:
        self.speak(prompt + " Say yes to confirm.")
        heard = self.listen().strip().lower()
        return heard in {"yes", "confirm", "do it", "proceed", "go ahead", "yes do it"}

    # ------------------------------------------------------------------
    # Action handlers (original)
    # ------------------------------------------------------------------

    def _open_app(self, app: str) -> None:
        cmd = APP_COMMANDS.get(app)
        if not cmd:
            self.speak("That app is not in my safe list.")
            return
        try:
            subprocess.Popen(cmd)
            self.speak(f"Opening {app}.")
        except Exception:
            self.speak(f"I could not open {app}.")

    def _search_web(self, q: str) -> None:
        if not q:
            self.speak("Tell me what to search for.")
            return
        webbrowser.open("https://www.google.com/search?q=" + urllib.parse.quote_plus(q))
        self.speak(f"Searching Google for {q}.")

    def _play_youtube(self, q: str) -> None:
        if not q:
            self.speak("Tell me what to play.")
            return
        self._switch_from_lumi_window(delay=0.12)
        query = q.strip()
        query = re.sub(r"^(?:the\s+)?(?:video|song|track)\s+(?:of|by|from)\s+", "", query, flags=re.I).strip()
        query = re.sub(r"\s+on\s+youtube$", "", query, flags=re.I).strip()
        results_url = "https://www.youtube.com/results?search_query=" + urllib.parse.quote_plus(query)
        try:
            first_video_url = self._extract_first_video_url(results_url)
        except Exception as exc:
            self.logger.warning("Failed to resolve first YouTube result for '%s': %s", query, exc)
            first_video_url = ""
        if first_video_url:
            webbrowser.open(first_video_url)
            self.speak(f"Playing {query} on YouTube.")
            return
        webbrowser.open(results_url)
        self.speak(f"I could not auto-play yet. Opening YouTube results for {query}.")

    def _get_clipboard_text(self) -> str:
        try:
            import tkinter as tk
            root = tk.Tk()
            root.withdraw()
            try:
                text = root.clipboard_get()
            finally:
                root.destroy()
            return str(text).strip()
        except Exception:
            try:
                result = subprocess.run(
                    ["powershell", "-NoProfile", "-Command", "Get-Clipboard -Raw"],
                    capture_output=True, text=True, timeout=2,
                )
                if result.returncode == 0:
                    return (result.stdout or "").strip()
            except Exception:
                pass
        return ""

    def _get_active_window_title(self) -> str:
        try:
            getter = getattr(pyautogui, "getActiveWindowTitle", None)
            if callable(getter):
                title = getter()
                if title:
                    return str(title).strip()
        except Exception:
            pass
        try:
            getter = getattr(pyautogui, "getActiveWindow", None)
            if callable(getter):
                win = getter()
                title = getattr(win, "title", "") if win else ""
                if title:
                    return str(title).strip()
        except Exception:
            pass
        return ""

    def _switch_from_lumi_window(self, delay: float = 0.2) -> None:
        title = self._get_active_window_title().lower()
        if not title:
            return
        ui_markers = (
            f"{self.config.assistant_name.lower()} assistant",
            "desktop voice + screen assistant",
        )
        if any(marker in title for marker in ui_markers):
            pyautogui.hotkey("alt", "tab")
            time.sleep(delay)

    def _get_active_url_from_browser(self) -> str:
        self._switch_from_lumi_window(delay=0.22)
        pyautogui.hotkey("ctrl", "l")
        time.sleep(0.12)
        pyautogui.hotkey("ctrl", "c")
        time.sleep(0.14)
        url = self._get_clipboard_text()
        pyautogui.press("esc")
        return url

    def _youtube_video_id_from_url(self, url: str) -> str:
        try:
            parsed = urllib.parse.urlparse(url)
        except Exception:
            return ""
        if "youtube.com" in parsed.netloc:
            q = urllib.parse.parse_qs(parsed.query)
            v = (q.get("v") or [""])[0].strip()
            return v if re.fullmatch(r"[a-zA-Z0-9_-]{11}", v) else ""
        if "youtu.be" in parsed.netloc:
            v = parsed.path.strip("/").split("/")[0]
            return v if re.fullmatch(r"[a-zA-Z0-9_-]{11}", v) else ""
        return ""

    def _extract_first_video_url(self, youtube_page_url: str, exclude_video_id: str = "") -> str:
        req = urllib.request.Request(
            youtube_page_url,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Lumi/2.0"},
        )
        with urllib.request.urlopen(req, timeout=6) as r:
            html = r.read().decode("utf-8", errors="ignore")
        ids = re.findall(r'"videoId":"([a-zA-Z0-9_-]{11})"', html)
        if not ids:
            ids = re.findall(r'href="/watch\?v=([a-zA-Z0-9_-]{11})', html)
        seen = set()
        excluded = exclude_video_id.strip()
        for vid in ids:
            if vid in seen:
                continue
            if excluded and vid == excluded:
                continue
            seen.add(vid)
            return f"https://www.youtube.com/watch?v={vid}"
        return ""

    def _play_first_video(self) -> None:
        self._switch_from_lumi_window(delay=0.22)
        try:
            active_url = self._get_active_url_from_browser()
        except Exception:
            active_url = ""
        if "youtube.com" in active_url or "youtu.be" in active_url:
            current_video_id = self._youtube_video_id_from_url(active_url)
            try:
                first_video_url = self._extract_first_video_url(active_url, exclude_video_id=current_video_id)
            except Exception as exc:
                self.logger.warning("Failed to fetch first YouTube video from %s: %s", active_url, exc)
                first_video_url = ""
            if not first_video_url:
                try:
                    first_video_url = self._extract_first_video_url("https://www.youtube.com/feed/trending")
                except Exception as exc:
                    self.logger.warning("Failed to fetch fallback YouTube trending video: %s", exc)
            if first_video_url:
                webbrowser.open(first_video_url)
                self.speak("Playing the first video from your current YouTube page.")
                return
        if "youtube.com/watch" in active_url or "youtu.be/" in active_url:
            pyautogui.press("k")
            self.speak("Toggled the current YouTube video.")
            return
        pyautogui.press("home")
        time.sleep(0.05)
        for _ in range(7):
            pyautogui.press("tab")
            time.sleep(0.03)
        pyautogui.press("enter")
        self.speak("I tried to open the first result on your screen.")

    def _perform_scroll(self, direction: str, amount: int) -> None:
        self._switch_from_lumi_window(delay=0.15)
        pixels = max(150, abs(amount))
        page_steps = max(1, min(7, pixels // 450))
        if direction == "down":
            for _ in range(page_steps):
                pyautogui.press("pagedown")
                time.sleep(0.02)
            pyautogui.scroll(-pixels)
            self.speak("Scrolled down.")
            return
        for _ in range(page_steps):
            pyautogui.press("pageup")
            time.sleep(0.02)
        pyautogui.scroll(pixels)
        self.speak("Scrolled up.")

    def _perform_scroll_edge(self, where: str) -> None:
        self._switch_from_lumi_window(delay=0.15)
        if where == "top":
            pyautogui.hotkey("ctrl", "home")
            self.speak("Scrolled to top.")
            return
        pyautogui.hotkey("ctrl", "end")
        self.speak("Scrolled to bottom.")

    def _send_video_key(self, key, spoken: str, *, hotkey: bool = False) -> None:
        self._switch_from_lumi_window(delay=0.15)
        if hotkey:
            pyautogui.hotkey(*key)
        else:
            pyautogui.press(key)
        time.sleep(0.06)
        self.speak(spoken)

    def _tell_weather(self, city: str, day: str) -> None:
        city = city.strip()
        if not city:
            self.speak("Tell me a city, for example weather in Mumbai. Or set WEATHER_DEFAULT_CITY.")
            return
        try:
            geo_url = "https://geocoding-api.open-meteo.com/v1/search?" + urllib.parse.urlencode(
                {"name": city, "count": 1, "language": "en", "format": "json"}
            )
            with urllib.request.urlopen(geo_url, timeout=10) as r:
                geo = json.loads(r.read().decode("utf-8"))
            results = geo.get("results") or []
            if not results:
                self.speak(f"I could not find location for {city}.")
                return
            place = results[0]
            lat, lon = place.get("latitude"), place.get("longitude")
            f_url = "https://api.open-meteo.com/v1/forecast?" + urllib.parse.urlencode({
                "latitude": lat, "longitude": lon,
                "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max,weather_code",
                "current": "temperature_2m",
                "timezone": "auto", "forecast_days": 3,
                "temperature_unit": self.config.weather_unit,
            })
            with urllib.request.urlopen(f_url, timeout=10) as r:
                forecast = json.loads(r.read().decode("utf-8"))
            daily = forecast.get("daily") or {}
            highs = daily.get("temperature_2m_max") or []
            lows = daily.get("temperature_2m_min") or []
            rains = daily.get("precipitation_probability_max") or []
            codes = daily.get("weather_code") or []
            if not highs or not lows:
                self.speak("I could not read the forecast right now.")
                return
            idx = 1 if day == "tomorrow" and len(highs) > 1 else 0
            desc = WEATHER_CODES.get(int(codes[idx]) if idx < len(codes) else 0, "mixed conditions")
            unit = "Fahrenheit" if self.config.weather_unit == "fahrenheit" else "Celsius"
            place_name = f"{place.get('name', city)}, {place.get('country', '')}".strip(", ")
            parts = [
                f"{'Tomorrow' if idx else 'Today'} in {place_name}: {desc}.",
                f"High {round(highs[idx])} and low {round(lows[idx])} degrees {unit}.",
            ]
            if idx < len(rains):
                parts.append(f"Rain chance up to {int(round(rains[idx]))} percent.")
            cur = (forecast.get("current") or {}).get("temperature_2m")
            if cur is not None and idx == 0:
                parts.append(f"Current temperature is {round(cur)} degrees {unit}.")
            self.speak(" ".join(parts))
        except (urllib.error.URLError, TimeoutError):
            self.speak("I could not reach the weather service right now.")
        except Exception:
            self.speak("I had trouble reading the weather forecast.")

    def _describe_screen_with_openai(self, prompt: str, image_b64: str) -> str:
        if not self.openai_client:
            raise RuntimeError("OPENAI_API_KEY is missing or openai package is unavailable.")
        resp = self.openai_client.chat.completions.create(
            model=self.config.vision_model,
            temperature=0.2,
            messages=[
                {"role": "system", "content": "You are a concise screen reader. Mention what user can click or do next."},
                {"role": "user", "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}},
                ]},
            ],
        )
        return _to_text(resp.choices[0].message.content).strip()

    def _describe_screen_with_gemini(self, prompt: str, image_b64: str) -> str:
        return self._gemini_generate_content(
            model=self.config.gemini_vision_model,
            parts=[{"text": prompt}, {"inline_data": {"mime_type": "image/jpeg", "data": image_b64}}],
            temperature=0.2,
        )

    def _cloud_screen_ready(self) -> bool:
        if self.config.ai_provider == "gemini":
            return bool(self.gemini_api_key)
        return self.openai_client is not None

    def _describe_screen_offline(self, question: str) -> str:
        if pytesseract is None:
            raise RuntimeError("pytesseract is not installed.")
        screenshot = self._capture_screenshot()
        grayscale = screenshot.convert("L")
        if ImageOps is not None:
            grayscale = ImageOps.autocontrast(grayscale)
        if ImageFilter is not None:
            grayscale = grayscale.filter(ImageFilter.SHARPEN)
        binary = grayscale.point(lambda p: 255 if p > 150 else 0)
        text_1 = pytesseract.image_to_string(grayscale)
        text_2 = pytesseract.image_to_string(binary)
        raw_text = text_2 if len(text_2.strip()) >= len(text_1.strip()) else text_1
        summary = self._summarize_ocr_text(raw_text)
        if not summary:
            return "I captured the screen, but I could not read text clearly. Try zooming in or using higher contrast."
        lower_raw = raw_text.lower()
        if ("lumi ui ready" in lower_raw or "quick start stop read screen" in lower_raw
                or "desktop voice + screen assistant" in lower_raw or "activity" in lower_raw):
            return "I mostly see Lumi's own window. Switch to the app you want me to read, then say read my screen again."
        excerpt = summary[:700]
        if question.strip():
            return f"I read this on screen: {excerpt}"
        return f"Screen text summary: {excerpt}"

    def _summarize_ocr_text(self, raw_text: str) -> str:
        noise_tokens = (
            "status: listening", "status: started", "status: calibrating microphone",
            "lumi ui ready", "desktop voice + screen assistant",
            "quick start stop read screen", "play/pause",
        )
        lines = []
        seen = set()
        for line in raw_text.splitlines():
            clean = re.sub(r"\s+", " ", line).strip(" |")
            clean = re.sub(r"^\[\d{2}:\d{2}:\d{2}\]\s*", "", clean)
            if len(clean) < 4:
                continue
            if not re.search(r"[A-Za-z]", clean):
                continue
            low = clean.lower()
            if any(token in low for token in noise_tokens):
                continue
            if low in seen:
                continue
            seen.add(low)
            lines.append(clean)
            if len(lines) >= 8:
                break
        if not lines:
            return ""
        return " | ".join(lines)

    def _describe_screen(self, question: str) -> None:
        self._switch_from_lumi_window(delay=0.28)
        if self.config.screen_read_mode == "offline":
            try:
                self.speak(self._describe_screen_offline(question))
            except Exception as exc:
                self.logger.exception("Offline screen analysis failed: %s", exc)
                message = str(exc).lower()
                if "tesseract" in message:
                    self.speak("Offline OCR needs Tesseract. Install it and set TESSERACT_CMD in .env.")
                elif "pytesseract" in message:
                    self.speak("Offline OCR package missing. Run pip install pytesseract, then restart Lumi.")
                else:
                    self.speak("Offline screen reading failed. Please check assistant.log for details.")
            return
        path = ""
        try:
            with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
                path = tmp.name
            screenshot = self._capture_screenshot()
            screenshot.thumbnail((1280, 720))
            screenshot.convert("RGB").save(path, format="JPEG", quality=72, optimize=True)
            with open(path, "rb") as f:
                img64 = base64.b64encode(f.read()).decode("utf-8")
            prompt = question.strip() or "Describe the visible screen and important controls."
            if not self._cloud_screen_ready():
                if self.config.screen_read_mode == "cloud":
                    raise RuntimeError(f"{self.config.ai_provider.upper()} key/client missing for cloud screen mode.")
                self.speak(self._describe_screen_offline(question))
                return
            if self.config.ai_provider == "gemini":
                text = self._describe_screen_with_gemini(prompt, img64)
            else:
                text = self._describe_screen_with_openai(prompt, img64)
            self.speak(text or "I could not read the screen clearly.")
        except Exception as exc:
            self.logger.exception("Screen analysis failed: %s", exc)
            message = str(exc).lower()
            if "pyscreeze" in message or "screenshot backend" in message or "imagegrab" in message:
                self.speak("Screen capture backend is missing. Install pillow and pyscreeze, then restart Lumi.")
            elif "invalid_api_key" in message or "incorrect api key" in message or "401" in message:
                self.speak(f"Your {self.config.ai_provider.title()} API key seems invalid.")
            elif "insufficient_quota" in message or "rate limit" in message or "429" in message:
                if self.config.screen_read_mode == "auto":
                    try:
                        self.speak("Cloud limit hit. Switching to offline OCR.")
                        self.speak(self._describe_screen_offline(question))
                    except Exception:
                        self.speak(f"{self.config.ai_provider.title()} quota or rate limit was hit.")
                else:
                    self.speak(f"{self.config.ai_provider.title()} quota or rate limit was hit.")
            elif "timed out" in message or "connection" in message or "network" in message:
                self.speak(f"Network issue while contacting {self.config.ai_provider.title()}. Please try again.")
            else:
                self.speak("I could not analyze the screen right now. Please check assistant.log for details.")
        finally:
            if path and os.path.exists(path):
                os.remove(path)

    def _capture_screenshot(self):
        try:
            return pyautogui.screenshot()
        except Exception as exc:
            self.logger.warning("pyautogui screenshot failed: %s", exc)
        if ImageGrab is not None:
            try:
                return ImageGrab.grab(all_screens=True)
            except TypeError:
                return ImageGrab.grab()
            except Exception as exc:
                self.logger.warning("PIL ImageGrab failed: %s", exc)
        raise RuntimeError("No screenshot backend available. Install pillow and pyscreeze.")

    # ------------------------------------------------------------------
    # New action handlers
    # ------------------------------------------------------------------

    def _take_screenshot(self) -> None:
        try:
            desktop = Path.home() / "Desktop"
            if not desktop.exists():
                desktop = Path.home()
            filename = f"lumi_screenshot_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
            path = desktop / filename
            screenshot = self._capture_screenshot()
            screenshot.save(str(path))
            self.speak(f"Screenshot saved to your Desktop as {filename}.")
        except Exception as exc:
            self.logger.error("Screenshot save failed: %s", exc)
            self.speak("I could not save the screenshot.")

    def _copy_to_clipboard(self, text: str) -> None:
        if not text:
            self.speak("There is nothing to copy.")
            return
        try:
            escaped = json.dumps(text)
            subprocess.run(
                ["powershell", "-NoProfile", "-Command", f"Set-Clipboard -Value {escaped}"],
                capture_output=True, timeout=3,
            )
        except Exception as exc:
            self.logger.warning("Clipboard copy failed: %s", exc)

    def _set_timer(self, seconds: int, label: str = "") -> None:
        self._cancel_all_timers = False
        
        # Clean up any dead timer threads to prevent memory leaks over time
        self._timer_threads = [t for t in self._timer_threads if t.is_alive()]

        def _worker():
            start = time.monotonic()
            while time.monotonic() - start < seconds:
                if self._stop_event.is_set() or self._cancel_all_timers:
                    return
                time.sleep(0.4)
            if not self._stop_event.is_set() and not self._cancel_all_timers:
                msg = f"Timer is done!" if not label else f"Timer for {label} is done!"
                self.speak(msg)
                try:
                    import winsound
                    for _ in range(3):
                        winsound.Beep(1000, 300)
                        time.sleep(0.15)
                except Exception:
                    pass

        t = threading.Thread(target=_worker, daemon=True)
        t.start()
        self._timer_threads.append(t)

    def _cancel_timers(self) -> None:
        self._cancel_all_timers = True
        count = len([t for t in self._timer_threads if t.is_alive()])
        self._timer_threads.clear()
        self.speak(f"Cancelled {count} timer{'s' if count != 1 else ''}.")

    def _make_note(self, text: str) -> None:
        if not text:
            self.speak("What should I note down?")
            return
        try:
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
            with open(self.notes_file, "a", encoding="utf-8") as f:
                f.write(f"[{timestamp}] {text}\n")
            self.speak(f"Note saved: {text}.")
            self._emit("note", text)
        except Exception as exc:
            self.logger.error("Note save failed: %s", exc)
            self.speak("I could not save that note.")

    def _read_notes(self) -> None:
        if not self.notes_file.exists():
            self.speak("You have no notes yet. Say make a note to create one.")
            return
        content = self.notes_file.read_text(encoding="utf-8").strip()
        if not content:
            self.speak("Your notes are empty.")
            return
        lines = content.splitlines()
        recent = lines[-5:]
        summaries = [re.sub(r"^\[.*?\]\s*", "", line) for line in recent]
        self.speak(f"Here are your last {len(recent)} note{'s' if len(recent) != 1 else ''}: "
                   + ". ".join(summaries) + ".")

    def _clear_notes(self) -> None:
        if self.notes_file.exists():
            self.notes_file.write_text("", encoding="utf-8")
        self.speak("All notes cleared.")

    def _math_calc(self, expr_raw: str) -> None:
        expr = expr_raw.strip().lower()
        # Natural language substitutions
        subs = [
            (r"\b(calculate|compute|evaluate|eval|solve|what is|what's|find)\b", ""),
            (r"\btimes\b", "*"),
            (r"\bdivided by\b", "/"),
            (r"\bover\b", "/"),
            (r"\bplus\b", "+"),
            (r"\bminus\b", "-"),
            (r"\bto the power of\b", "**"),
            (r"\bsquared\b", "**2"),
            (r"\bcubed\b", "**3"),
        ]
        for pattern, repl in subs:
            expr = re.sub(pattern, repl, expr, flags=re.I)

        # Handle "X percent of Y"
        expr = re.sub(
            r"(\d+(?:\.\d+)?)\s*percent\s+of\s+(\d+(?:\.\d+)?)",
            lambda m_: str(float(m_.group(1)) / 100 * float(m_.group(2))),
            expr,
        )

        # Strip non-math characters
        expr = re.sub(r"[^0-9+\-*/.() ]", "", expr).strip()

        if not expr:
            self.speak("Please give me a math expression to calculate.")
            return
        try:
            tree = ast.parse(expr, mode="eval")
            result = _safe_eval_ast(tree.body)
            if isinstance(result, float):
                if result.is_integer():
                    result = int(result)
                else:
                    result = round(result, 8)
                    # Clean trailing zeros
                    result = float(f"{result:.6g}")
            self.speak(f"The answer is {result}.")
        except ZeroDivisionError:
            self.speak("That's division by zero, which is undefined.")
        except Exception:
            self.speak("I could not calculate that. Please try a cleaner expression.")

    def _wikipedia_lookup(self, query: str) -> None:
        query = query.strip()
        if not query:
            self.speak("What would you like me to look up?")
            return
        try:
            encoded = urllib.parse.quote(query.replace(" ", "_"))
            url = f"https://en.wikipedia.org/api/rest_v1/page/summary/{encoded}"
            req = urllib.request.Request(url, headers={"User-Agent": "Lumi/2.0 (voice assistant)"})
            with urllib.request.urlopen(req, timeout=8) as r:
                data = json.loads(r.read().decode("utf-8"))
            extract = data.get("extract", "").strip()
            if not extract:
                self.speak(f"I could not find information about {query} on Wikipedia.")
                return
            sentences = re.split(r"(?<=[.!?])\s+", extract)
            summary = " ".join(sentences[:3])
            self.speak(summary[:500])
        except urllib.error.HTTPError as e:
            if e.code == 404:
                # Try a search fallback
                self.speak(f"I could not find a Wikipedia article for {query}. Try searching the web instead.")
            else:
                self.speak("I could not reach Wikipedia right now.")
        except (urllib.error.URLError, TimeoutError):
            self.speak("I could not reach Wikipedia right now. Please check your connection.")
        except Exception as exc:
            self.logger.warning("Wikipedia lookup failed: %s", exc)
            self.speak("Wikipedia lookup failed.")

    def _system_info(self, query: str) -> None:
        if psutil is None:
            self.speak("System info needs psutil. Run pip install psutil, then restart Lumi.")
            return
        q = query.lower().strip()
        try:
            if q in ("battery",):
                batt = psutil.sensors_battery()
                if batt is None:
                    self.speak("No battery detected. You may be on a desktop PC.")
                    return
                status = "charging" if batt.power_plugged else "not charging"
                self.speak(f"Battery is at {int(batt.percent)} percent and {status}.")
            elif q in ("cpu",):
                cpu = psutil.cpu_percent(interval=1)
                count = psutil.cpu_count()
                self.speak(f"CPU usage is {cpu} percent across {count} cores.")
            elif q in ("ram", "memory"):
                mem = psutil.virtual_memory()
                used_gb = mem.used / (1024 ** 3)
                total_gb = mem.total / (1024 ** 3)
                self.speak(f"RAM is {mem.percent} percent used. "
                           f"That's {used_gb:.1f} of {total_gb:.1f} gigabytes.")
            elif q in ("disk", "storage"):
                disk = psutil.disk_usage("/")
                free_gb = disk.free / (1024 ** 3)
                total_gb = disk.total / (1024 ** 3)
                used_pct = disk.percent
                self.speak(f"Disk is {used_pct} percent full. "
                           f"{free_gb:.1f} gigabytes free out of {total_gb:.1f} total.")
            else:
                # Full overview
                cpu = psutil.cpu_percent(interval=0.5)
                mem = psutil.virtual_memory()
                parts = [f"CPU at {cpu} percent.", f"RAM at {mem.percent} percent."]
                batt = psutil.sensors_battery()
                if batt:
                    parts.append(f"Battery at {int(batt.percent)} percent.")
                self.speak(" ".join(parts))
        except Exception as exc:
            self.logger.warning("System info failed: %s", exc)
            self.speak("I could not read system information.")

    def _get_ip_address(self) -> None:
        try:
            with urllib.request.urlopen("https://api.ipify.org", timeout=5) as r:
                ip = r.read().decode("utf-8").strip()
            self.speak(f"Your public IP address is {ip}.")
            return
        except Exception:
            pass
        try:
            hostname = socket.gethostname()
            ip = socket.gethostbyname(hostname)
            self.speak(f"Your local IP address is {ip}.")
        except Exception:
            self.speak("I could not retrieve your IP address.")

    def _open_folder(self, folder_key: str) -> None:
        key = folder_key.lower().strip().rstrip("s")
        path = FOLDER_MAP.get(key)
        if not path:
            # Try partial match
            for k, v in FOLDER_MAP.items():
                if k.startswith(key[:4]):
                    path = v
                    break
        if not path:
            self.speak(f"I don't know how to open the {folder_key} folder.")
            return
        try:
            os.startfile(path)
            self.speak(f"Opening {folder_key}.")
        except Exception as exc:
            self.logger.warning("Open folder failed: %s", exc)
            self.speak(f"I could not open the {folder_key} folder.")

    def _tell_joke(self) -> None:
        self.speak(random.choice(JOKES))

    # ------------------------------------------------------------------
    # Help
    # ------------------------------------------------------------------

    def _show_help(self) -> None:
        self.speak(
            "Here's what I can do: "
            "Open apps and websites. Search and play YouTube. "
            "Control video and scroll on screen. "
            "Check weather and Wikipedia. "
            "Calculate math expressions. "
            "Set timers and take notes. "
            "Read your screen with cloud or offline OCR. "
            "Save screenshots to your Desktop. "
            "Read your clipboard and copy last response. "
            "Check battery, CPU, RAM, and disk usage. "
            "Open folders like Downloads or Desktop. "
            "Control volume and media playback. "
            "Minimize, maximize, or close windows. "
            "Get your IP address. "
            "Tell jokes and more. "
            "I also learn from corrections. Say no I meant, followed by the right command."
        )

    # ------------------------------------------------------------------
    # Execute intent
    # ------------------------------------------------------------------

    def execute_intent(self, intent: Intent) -> bool:
        a = intent.action

        if a == "exit":
            self.speak(f"Stopping {self.config.assistant_name}. Bye!")
            return True
        if a == "tell_name":
            self.speak(f"My name is {self.config.assistant_name}.")
            return False
        if a == "help":
            self._show_help()
            return False
        if a == "tell_time":
            self.speak(f"The current time is {datetime.now().strftime('%I:%M %p')}.")
            return False
        if a == "tell_date":
            self.speak(f"Today is {datetime.now().strftime('%A, %B %d, %Y')}.")
            return False
        if a == "repeat_last":
            if self._last_spoken:
                self.speak(self._last_spoken)
            else:
                self.speak("I have nothing to repeat yet.")
            return False
        if a == "tell_joke":
            self._tell_joke()
            return False
        if a == "clear_learned":
            self.learned_commands.clear()
            self._seed_default_learning()
            self._save_learned_commands()
            self.speak("All custom learned commands have been cleared.")
            return False
        if a == "open_app":
            self._open_app(intent.target)
            return False
        if a == "open_url":
            url = intent.target
            if not _is_probable_url(url):
                self.speak("I could not find a valid website.")
                return False
            webbrowser.open(url)
            self.speak(f"Opening {url}.")
            return False
        if a == "search_web":
            self._search_web(intent.text)
            return False
        if a == "play_youtube":
            self._play_youtube(intent.text)
            return False
        if a == "play_first_video":
            self._play_first_video()
            return False
        if a == "type_text":
            t = intent.text[:self.config.max_type_chars]
            if not t:
                self.speak("There is no text to type.")
            else:
                self.speak("Typing now.")
                pyautogui.write(t, interval=self.config.type_interval)
            return False
        if a == "volume_up":
            for _ in range(int(intent.target or "5")):
                pyautogui.press("volumeup")
            self.speak("Volume increased.")
            return False
        if a == "volume_down":
            for _ in range(int(intent.target or "5")):
                pyautogui.press("volumedown")
            self.speak("Volume decreased.")
            return False
        if a == "volume_mute":
            pyautogui.press("volumemute")
            self.speak("Mute toggled.")
            return False
        if a == "scroll_down":
            self._perform_scroll("down", int(intent.target or "700"))
            return False
        if a == "scroll_up":
            self._perform_scroll("up", int(intent.target or "700"))
            return False
        if a == "scroll_top":
            self._perform_scroll_edge("top")
            return False
        if a == "scroll_bottom":
            self._perform_scroll_edge("bottom")
            return False
        if a == "video_toggle":
            self._send_video_key("k", "Toggled video playback.")
            return False
        if a == "video_next":
            self._send_video_key(("shift", "n"), "Playing next video.", hotkey=True)
            return False
        if a == "video_previous":
            self._send_video_key(("shift", "p"), "Playing previous video.", hotkey=True)
            return False
        if a == "video_forward":
            self._send_video_key("l", "Skipped forward.")
            return False
        if a == "video_back":
            self._send_video_key("j", "Skipped backward.")
            return False
        if a == "video_fullscreen":
            self._send_video_key("f", "Toggled fullscreen.")
            return False
        if a == "video_mute":
            self._send_video_key("m", "Toggled video mute.")
            return False
        if a == "weather_forecast":
            self._tell_weather(
                intent.target or self.config.weather_default_city,
                "tomorrow" if intent.text == "tomorrow" else "today",
            )
            return False
        if a == "describe_screen":
            self._describe_screen(intent.text)
            return False

        # --- New actions ---
        if a == "take_screenshot":
            self._take_screenshot()
            return False
        if a == "read_clipboard":
            text = self._get_clipboard_text()
            if text:
                preview = text[:250]
                self.speak(f"Clipboard contains: {preview}{'...' if len(text) > 250 else ''}.")
            else:
                self.speak("The clipboard is empty or I could not read it.")
            return False
        if a == "copy_last":
            if self._last_spoken:
                self._copy_to_clipboard(self._last_spoken)
                self.speak("Copied my last response to clipboard.")
            else:
                self.speak("I have nothing to copy yet.")
            return False
        if a == "set_timer":
            seconds = int(intent.target or "60")
            self._set_timer(seconds, intent.text)
            mins, secs = divmod(seconds, 60)
            hrs, mins = divmod(mins, 60)
            parts = []
            if hrs:
                parts.append(f"{hrs} hour{'s' if hrs != 1 else ''}")
            if mins:
                parts.append(f"{mins} minute{'s' if mins != 1 else ''}")
            if secs:
                parts.append(f"{secs} second{'s' if secs != 1 else ''}")
            duration_str = " and ".join(parts) if parts else "1 minute"
            label_part = f" for {intent.text}" if intent.text.strip() else ""
            self.speak(f"Timer{label_part} set for {duration_str}.")
            return False
        if a == "cancel_timer":
            self._cancel_timers()
            return False
        if a == "make_note":
            self._make_note(intent.text)
            return False
        if a == "read_notes":
            self._read_notes()
            return False
        if a == "clear_notes":
            self._clear_notes()
            return False
        if a == "math_calc":
            self._math_calc(intent.text)
            return False
        if a == "wikipedia_lookup":
            self._wikipedia_lookup(intent.text)
            return False
        if a == "system_info":
            self._system_info(intent.text or "all")
            return False
        if a == "ip_address":
            self._get_ip_address()
            return False
        if a == "media_next":
            self._switch_from_lumi_window(delay=0.15)
            pyautogui.press("nexttrack")
            self.speak("Next track.")
            return False
        if a == "media_prev":
            self._switch_from_lumi_window(delay=0.15)
            pyautogui.press("prevtrack")
            self.speak("Previous track.")
            return False
        if a == "media_play_pause":
            self._switch_from_lumi_window(delay=0.15)
            pyautogui.press("playpause")
            self.speak("Toggled media playback.")
            return False
        if a == "window_minimize":
            self._switch_from_lumi_window(delay=0.15)
            pyautogui.hotkey("win", "down")
            self.speak("Window minimized.")
            return False
        if a == "window_maximize":
            self._switch_from_lumi_window(delay=0.15)
            pyautogui.hotkey("win", "up")
            self.speak("Window maximized.")
            return False
        if a == "window_close":
            self._switch_from_lumi_window(delay=0.15)
            pyautogui.hotkey("alt", "f4")
            self.speak("Window closed.")
            return False
        if a == "open_folder":
            self._open_folder(intent.target)
            return False

        if a in {"shutdown", "restart"}:
            if not self.config.allow_power_actions:
                self.speak("Power commands are disabled. Set ALLOW_POWER_ACTIONS=1 to enable them.")
                return False
            if not self._confirm_action(f"Should I {a} your PC now?"):
                self.speak("Cancelled.")
                return False
            if a == "shutdown":
                os.system("shutdown /s /t 5")
                self.speak("Shutting down in 5 seconds.")
            else:
                os.system("shutdown /r /t 5")
                self.speak("Restarting in 5 seconds.")
            return False

        # Phase 2: Windows API actions
        if handle_windows_api_intent(self, a, intent):
            return False

        self.speak("Sorry, I did not understand that. Say help for a list of commands.")
        return False

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    def process_command(self, raw_command: str, require_wake_word: bool = True) -> bool:
        cmd = self._prepare_command(raw_command, require_wake_word=require_wake_word)
        if not cmd:
            return False
        self._emit("user", cmd)

        explicit_learning = self._extract_explicit_learning(cmd)
        if explicit_learning:
            source, target = explicit_learning
            target_intent = self.parse_intent(target)
            if target_intent.action == "unknown":
                self.speak("I could not learn that because the target command is still unclear.")
                return False
            self._learn_mapping(source, target_intent)
            self.speak(f"Learned. Next time '{source}' will do that action.")
            self._last_user_command = source
            self._last_intent = target_intent
            return False

        correction_target = self._extract_correction_target(cmd)
        if correction_target and self._last_user_command:
            corrected_intent = self.parse_intent(correction_target)
            if corrected_intent.action != "unknown":
                self._learn_mapping(self._last_user_command, corrected_intent)
                self.speak(f"Got it. I learned that '{self._last_user_command}' means '{correction_target}'.")
                intent = corrected_intent
                cmd = correction_target
            else:
                intent = corrected_intent
        else:
            intent = self.parse_intent(cmd)

        should_exit = self.execute_intent(intent)
        self._last_user_command = cmd
        self._last_intent = intent
        if should_exit:
            self._stop_event.set()
        return should_exit

    def _loop(self) -> None:
        if not self._setup_microphone():
            self.speak("Microphone setup failed. Check permissions and input device.")
            return
        self.speak(f"{self.config.assistant_name} is ready. Say help to know what I can do.")
        while not self._stop_event.is_set():
            command = self.listen()
            if not command:
                continue
            self.process_command(command, require_wake_word=True)
        self._emit("status", "Stopped")

    def _idle_loop(self) -> None:
        if not self._setup_microphone():
            self.speak("Microphone setup failed. Check permissions and input device.")
            return
        self.speak(
            f"{self.config.assistant_name} is ready. "
            f"Say your wake word or press {self.config.lumi_hotkey}."
        )
        self._emit("status", "Idle")
        while not self._stop_event.is_set():
            self._stop_event.wait(timeout=1.0)
        self._emit("status", "Stopped")

    def start_background(self) -> None:
        if self._loop_thread and self._loop_thread.is_alive():
            return
        self._stop_event.clear()
        self._loop_thread = threading.Thread(target=self._idle_loop, daemon=True)
        self._loop_thread.start()
        self._always_on.start()
        self._emit("status", "Started")

    def stop_background(self) -> None:
        self._stop_event.set()
        self._always_on.stop()
        self._emit("status", "Stopping...")

    def run_cli(self) -> None:
        self._stop_event.clear()
        self._loop()


# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------
# JS API bridge  (exposed to JS as window.pywebview.api)
# ---------------------------------------------------------------------------

class _LumiJSAPI:
    def __init__(self, assistant: "VoiceAssistant", ui: "LumiUI"):
        self.assistant = assistant
        self.ui = ui

    def ui_ready(self):
        self.assistant.logger.info("Lumi UI ready.")

    def send_command(self, command: str):
        if not command:
            return
        threading.Thread(
            target=lambda: self.assistant.process_command(command, require_wake_word=False),
            daemon=True,
        ).start()

    def close(self):
        self.assistant.stop_background()
        if self.ui.window:
            self.ui.window.destroy()

    def move_by(self, dx: int, dy: int):
        if not self.ui.window: return
        try:
            self.ui.window.move(self.ui.window.x + int(dx), self.ui.window.y + int(dy))
        except Exception:
            pass

class LumiUI:
    _HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<style>
/* ── Base Reset & Glassmorphism ──────────────────────────────────── */
* { margin: 0; padding: 0; box-sizing: border-box; }

:root {
  --bg: rgba(22, 22, 26, 0.65);
  --glass-border: rgba(255, 255, 255, 0.12);
  --accent: #0A84FF;
  --text: #F5F5F7;
  --subtext: rgba(235, 235, 245, 0.6);
  --pill-h: 68px;
}

html, body {
  background: transparent;
  overflow: hidden;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
  -webkit-user-select: none;
  user-select: none;
  width: 100%;
  height: 100%;
  color: var(--text);
}

/* ── Pill Shell ────────────────────────────────────────────────────── */
.pill {
  position: absolute;
  left: 50%;
  top: 50%;
  transform: translate(-50%, -50%);
  width: 520px;
  height: var(--pill-h);
  background: var(--bg);
  backdrop-filter: blur(20px) saturate(180%);
  -webkit-backdrop-filter: blur(20px) saturate(180%);
  border: 1px solid var(--glass-border);
  border-radius: 34px;
  box-shadow: 0 12px 48px rgba(0,0,0,0.5);
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 12px 0 28px;
  cursor: grab;
  transition: opacity 0.4s, transform 0.4s;
}
.pill:active { cursor: grabbing; }

.pill.hidden {
  opacity: 0;
  transform: translate(-50%, -40%);
  pointer-events: none;
}

/* ── Text Content ────────────────────────────────────────────────── */
.content {
  font-size: 18px;
  font-weight: 500;
  letter-spacing: -0.2px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  flex-grow: 1;
}
.content-user { font-style: italic; color: var(--subtext); }
.content-error { color: #FF453A; }

/* ── Animated Orb ────────────────────────────────────────────────── */
.orb-container {
  position: relative;
  width: 100px;
  height: 100%;
  flex-shrink: 0;
}
.orb {
  position: absolute;
  top: 50%;
  left: 50%;
  width: 48px;
  height: 48px;
  border-radius: 50%;
  transform: translate(-50%, -50%) scale(0);
  background: radial-gradient(circle, #3E97FF, #0050E0);
  opacity: 0;
  transition: transform 0.5s cubic-bezier(0.34, 1.56, 0.64, 1), opacity 0.5s;
}

.pill.listening .orb,
.pill.thinking .orb {
  transform: translate(-50%, -50%) scale(1);
  opacity: 1;
  animation: orb-pulse 2s ease-in-out infinite;
}

@keyframes orb-pulse {
  0%, 100% { filter: brightness(1) saturate(1); }
  50% { filter: brightness(1.4) saturate(1.2); }
}
</style>
</head>
<body>

<div id="pill" class="pill hidden">
  <div id="content" class="content">Lumi is ready</div>
  <div class="orb-container">
    <div class="orb"></div>
  </div>
</div>

<script>
const dom = {
  pill: document.getElementById('pill'),
  content: document.getElementById('content'),
};

function setMode(mode, text = '') {
  dom.pill.classList.remove('hidden', 'listening', 'thinking', 'user-input', 'error');
  if (mode) dom.pill.classList.add(mode);

  if (text) {
    dom.content.innerHTML = text;
    dom.content.className = 'content'; // Reset class
    if (mode === 'user-input') {
        dom.content.classList.add('content-user');
    } else if (mode === 'error') {
        dom.content.classList.add('content-error');
    }
  }
}

// Drag handling
let dragging = false, startX, startY;
dom.pill.addEventListener('mousedown', e => {
    dragging = true;
    startX = e.screenX;
    startY = e.screenY;
});
document.addEventListener('mouseup', () => { dragging = false; });
document.addEventListener('mousemove', e => {
    if (!dragging) return;
    const dx = e.screenX - startX;
    const dy = e.screenY - startY;
    startX = e.screenX;
    startY = e.screenY;
    if (window.pywebview && (dx !== 0 || dy !== 0)) {
        window.pywebview.api.move_by(dx, dy);
    }
});

// Communication with Python
window.addEventListener('pywebviewready', () => {
  window.pywebview.api.ui_ready();
});

function onPythonEvent(kind, text) {
    if (kind === 'status') {
        const s = text.toLowerCase();
        if (s.includes('listening') || s.includes('started')) {
            setMode('listening', 'Listening...');
        } else if (s.includes('process') || s.includes('thinking')) {
            setMode('thinking', 'Thinking...');
        } else if (s.includes('stop') || s.includes('idle')) {
            setMode('hidden');
        }
    } else if (kind === 'user') {
        setMode('user-input', `“${text}”`);
    } else if (kind === 'assistant') {
        setMode('assistant', text);
    } else if (kind === 'error') {
        setMode('error', text);
    }
}

// Initial state
setTimeout(() => setMode('hidden'), 50);
</script>
</body>
</html>"""

    def __init__(self, assistant: VoiceAssistant):
        self.assistant = assistant
        self.window = None
        self._api = _LumiJSAPI(assistant, self)

    def _screen_center_bottom(self):
        try:
            import tkinter as _tk
            r = _tk.Tk()
            r.withdraw()
            sw, sh = r.winfo_screenwidth(), r.winfo_screenheight()
            r.destroy()
            return (sw - 520) // 2, sh - 68 - 52
        except Exception:
            return None, None

    def _event_callback(self, kind: str, text: str):
        if not self.window: return
        try:
            self.window.evaluate_js(f"onPythonEvent({json.dumps(kind)}, {json.dumps(text)})")
        except Exception as e:
            self.assistant.logger.error(f"UI event call failed: {e}")

    def run(self):
        try:
            import webview
        except ImportError:
            raise RuntimeError("pywebview is not installed. Run: pip install pywebview")

        self.assistant.event_callback = self._event_callback
        x, y = self._screen_center_bottom()

        self.window = webview.create_window(
            title=self.assistant.config.assistant_name,
            html=self._HTML,
            width=520,
            height=68,
            x=x,
            y=y,
            frameless=True,
            transparent=True,
            on_top=True,
            js_api=self._api,
            resizable=True, # Fix for cropping bug
            min_size=(400, 68),
            background_color="#00000000",
        )
        self.assistant.start_background()
        try:
            webview.start(debug=False, private_mode=False)
        finally:
            self.assistant.stop_background()

# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    if load_dotenv is not None:
        load_dotenv(override=False)

    parser = argparse.ArgumentParser(description="Lumi Voice Assistant v2.0")
    parser.add_argument("--no-ui", action="store_true", help="Run in terminal mode")
    args = parser.parse_args()

    config = AssistantConfig.from_env()
    assistant = VoiceAssistant(config)

    if config.enable_ui and not args.no_ui:
        try:
            LumiUI(assistant).run()
            return
        except Exception as exc:
            assistant.logger.error("UI failed: %s", exc)
            print("UI failed to launch, running console mode.")

    assistant.run_cli()


if __name__ == "__main__":
    main()
