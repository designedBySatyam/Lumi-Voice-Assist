"""
lumi_context.py — Phase 3: App-Aware Context Engine
====================================================
Gives Lumi real awareness of what's happening on the user's screen:

  • Active window  — app name, window title, process name, PID
  • Selected text  — reads whatever text is currently highlighted
                     (clipboard-swap trick, non-destructive)
  • Browser URL    — current tab URL in Chrome / Edge / Firefox
  • App profile    — per-app behaviour rules (VS Code, browser, Excel, etc.)
  • Context prompt — injects live context into AI screen-read queries
  • Smart commands — "explain this", "summarise this page", "fix this error"
                     all resolve correctly based on what's actually open

Install
-------
    pip install pywinauto pywin32 psutil

All deps are optional — the module degrades gracefully if missing.
"""

from __future__ import annotations

import logging
import re
import subprocess
import threading
import time
import urllib.parse
from dataclasses import dataclass, field
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from assistant import VoiceAssistant

logger = logging.getLogger("lumi.context")

# ---------------------------------------------------------------------------
# Optional deps
# ---------------------------------------------------------------------------

try:
    import win32gui
    import win32process
    import win32con
    import win32api
    _HAS_WIN32 = True
except ImportError:
    win32gui = win32process = win32con = win32api = None
    _HAS_WIN32 = False

try:
    import psutil
    _HAS_PSUTIL = True
except ImportError:
    psutil = None
    _HAS_PSUTIL = False

try:
    import pyperclip
    _HAS_PYPERCLIP = True
except ImportError:
    pyperclip = None
    _HAS_PYPERCLIP = False


# ---------------------------------------------------------------------------
# App profiles — per-app behaviour and command vocabulary
# ---------------------------------------------------------------------------

@dataclass
class AppProfile:
    name: str                          # display name e.g. "VS Code"
    process_names: list                # e.g. ["Code.exe", "code"]
    title_patterns: list               # regex patterns matched against window title
    category: str                      # "editor" | "browser" | "terminal" | "office" | "media" | "other"
    can_explain_selection: bool = True  # "explain this" makes sense
    can_summarise: bool = False         # "summarise this page" makes sense
    can_fix_error: bool = False         # "fix this error" makes sense
    smart_actions: dict = field(default_factory=dict)  # voice phrase → action key


APP_PROFILES: list[AppProfile] = [
    AppProfile(
        name="VS Code",
        process_names=["Code.exe", "code", "code - insiders"],
        title_patterns=[r"visual studio code", r"\.py\b", r"\.js\b", r"\.ts\b",
                        r"\.html\b", r"\.css\b", r"\.json\b", r"\.md\b"],
        category="editor",
        can_explain_selection=True,
        can_fix_error=True,
        smart_actions={
            "explain this": "explain_selection",
            "fix this error": "fix_error",
            "what does this do": "explain_selection",
            "explain this code": "explain_selection",
            "debug this": "fix_error",
            "open terminal": "open_terminal",
            "format code": "format_code",
            "save file": "save_file",
        },
    ),
    AppProfile(
        name="Chrome",
        process_names=["chrome.exe", "chrome"],
        title_patterns=[r"google chrome", r"- chrome$"],
        category="browser",
        can_explain_selection=True,
        can_summarise=True,
        smart_actions={
            "summarise this page": "summarise_page",
            "summarize this page": "summarise_page",
            "explain this": "explain_selection",
            "read this": "summarise_page",
            "what is this page about": "summarise_page",
            "new tab": "new_tab",
            "close tab": "close_tab",
            "go back": "browser_back",
            "go forward": "browser_forward",
            "bookmark this": "bookmark_page",
        },
    ),
    AppProfile(
        name="Edge",
        process_names=["msedge.exe", "msedge"],
        title_patterns=[r"microsoft edge", r"- edge$"],
        category="browser",
        can_explain_selection=True,
        can_summarise=True,
        smart_actions={
            "summarise this page": "summarise_page",
            "summarize this page": "summarise_page",
            "explain this": "explain_selection",
            "read this": "summarise_page",
            "new tab": "new_tab",
            "close tab": "close_tab",
            "go back": "browser_back",
        },
    ),
    AppProfile(
        name="Firefox",
        process_names=["firefox.exe", "firefox"],
        title_patterns=[r"mozilla firefox", r"- firefox$"],
        category="browser",
        can_explain_selection=True,
        can_summarise=True,
        smart_actions={
            "summarise this page": "summarise_page",
            "explain this": "explain_selection",
            "new tab": "new_tab",
            "close tab": "close_tab",
        },
    ),
    AppProfile(
        name="Windows Terminal",
        process_names=["WindowsTerminal.exe", "wt.exe", "cmd.exe", "powershell.exe"],
        title_patterns=[r"windows terminal", r"powershell", r"command prompt", r"cmd"],
        category="terminal",
        can_explain_selection=True,
        can_fix_error=True,
        smart_actions={
            "explain this error": "explain_selection",
            "fix this": "fix_error",
            "what does this mean": "explain_selection",
            "clear terminal": "clear_terminal",
        },
    ),
    AppProfile(
        name="Notepad",
        process_names=["notepad.exe", "notepad++.exe"],
        title_patterns=[r"notepad", r"notepad\+\+"],
        category="editor",
        can_explain_selection=True,
        smart_actions={
            "explain this": "explain_selection",
            "summarise this": "summarise_page",
        },
    ),
    AppProfile(
        name="Excel",
        process_names=["EXCEL.EXE", "excel"],
        title_patterns=[r"microsoft excel", r"\.xlsx?\b"],
        category="office",
        can_explain_selection=True,
        smart_actions={
            "explain this formula": "explain_selection",
            "what does this cell do": "explain_selection",
        },
    ),
    AppProfile(
        name="Word",
        process_names=["WINWORD.EXE", "winword"],
        title_patterns=[r"microsoft word", r"\.docx?\b"],
        category="office",
        can_explain_selection=True,
        can_summarise=True,
        smart_actions={
            "summarise this document": "summarise_page",
            "explain this": "explain_selection",
        },
    ),
    AppProfile(
        name="Spotify",
        process_names=["Spotify.exe", "spotify"],
        title_patterns=[r"spotify"],
        category="media",
        smart_actions={},
    ),
    AppProfile(
        name="File Explorer",
        process_names=["explorer.exe"],
        title_patterns=[r"file explorer", r"this pc", r"documents", r"downloads"],
        category="other",
        smart_actions={},
    ),
]


# ---------------------------------------------------------------------------
# WindowContext — snapshot of current focus
# ---------------------------------------------------------------------------

@dataclass
class WindowContext:
    hwnd: int = 0
    title: str = ""
    process_name: str = ""
    process_id: int = 0
    profile: Optional[AppProfile] = None
    selected_text: str = ""
    browser_url: str = ""
    timestamp: float = 0.0

    @property
    def app_name(self) -> str:
        if self.profile:
            return self.profile.name
        return self.process_name.replace(".exe", "").title() or "Unknown"

    @property
    def is_browser(self) -> bool:
        return self.profile is not None and self.profile.category == "browser"

    @property
    def is_editor(self) -> bool:
        return self.profile is not None and self.profile.category in ("editor", "terminal")

    @property
    def is_fresh(self) -> bool:
        """True if this snapshot is less than 3 seconds old."""
        return (time.monotonic() - self.timestamp) < 3.0

    def summary(self) -> str:
        parts = [f"App: {self.app_name}"]
        if self.title:
            parts.append(f"Window: {self.title[:60]}")
        if self.browser_url:
            parts.append(f"URL: {self.browser_url[:80]}")
        if self.selected_text:
            preview = self.selected_text[:120].replace("\n", " ")
            parts.append(f"Selected: \"{preview}\"")
        return " | ".join(parts)


# ---------------------------------------------------------------------------
# ContextEngine
# ---------------------------------------------------------------------------

class ContextEngine:
    """
    Continuously tracks the active window and provides on-demand
    snapshots of what the user is looking at.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._current: WindowContext = WindowContext()
        self._poll_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

    # ------------------------------------------------------------------
    # Profile matching
    # ------------------------------------------------------------------

    @staticmethod
    def _match_profile(title: str, process_name: str) -> Optional[AppProfile]:
        title_lower = title.lower()
        proc_lower = process_name.lower()
        for profile in APP_PROFILES:
            # Match by process name first (most reliable)
            for pname in profile.process_names:
                if pname.lower() == proc_lower:
                    return profile
            # Match by title pattern
            for pattern in profile.title_patterns:
                if re.search(pattern, title_lower):
                    return profile
        return None

    # ------------------------------------------------------------------
    # Active window detection
    # ------------------------------------------------------------------

    @staticmethod
    def _get_active_window() -> tuple[int, str, str, int]:
        """Returns (hwnd, title, process_name, pid)."""
        if not _HAS_WIN32:
            return 0, "", "", 0
        try:
            hwnd = win32gui.GetForegroundWindow()
            title = win32gui.GetWindowText(hwnd) or ""
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            process_name = ""
            if _HAS_PSUTIL and pid:
                try:
                    process_name = psutil.Process(pid).name()
                except Exception:
                    pass
            return hwnd, title, process_name, pid
        except Exception as exc:
            logger.debug("Active window detection failed: %s", exc)
            return 0, "", "", 0

    # ------------------------------------------------------------------
    # Selected text (clipboard-swap, non-destructive)
    # ------------------------------------------------------------------

    @staticmethod
    def get_selected_text() -> str:
        """
        Reads whatever text is currently selected in the active app.
        Uses a clipboard-swap: saves current clipboard, copies selection,
        reads it, then restores the original clipboard content.
        Non-destructive — the user's clipboard is unchanged after.
        """
        import ctypes
        import subprocess

        original = ""
        selected = ""

        # Save current clipboard
        try:
            result = subprocess.run(
                ["powershell", "-NoProfile", "-Command", "Get-Clipboard -Raw"],
                capture_output=True, text=True, timeout=2,
            )
            original = (result.stdout or "").rstrip("\n")
        except Exception:
            pass

        # Clear clipboard then copy selection
        try:
            subprocess.run(
                ["powershell", "-NoProfile", "-Command", "Set-Clipboard -Value ''"],
                capture_output=True, timeout=2,
            )
            import pyautogui
            pyautogui.hotkey("ctrl", "c")
            time.sleep(0.15)

            result = subprocess.run(
                ["powershell", "-NoProfile", "-Command", "Get-Clipboard -Raw"],
                capture_output=True, text=True, timeout=2,
            )
            selected = (result.stdout or "").rstrip("\n")
        except Exception as exc:
            logger.debug("Selected text read failed: %s", exc)

        # Restore original clipboard
        try:
            escaped = original.replace("'", "''")
            subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 f"Set-Clipboard -Value '{escaped}'"],
                capture_output=True, timeout=2,
            )
        except Exception:
            pass

        return selected.strip()

    # ------------------------------------------------------------------
    # Browser URL
    # ------------------------------------------------------------------

    @staticmethod
    def _get_browser_url(process_name: str) -> str:
        """
        Read the current browser tab URL via accessibility or netsh.
        Tries pywinauto UIA first, falls back to a PowerShell approach.
        """
        proc = process_name.lower()
        if not any(b in proc for b in ("chrome", "msedge", "firefox")):
            return ""
        try:
            from pywinauto import Desktop
            desktop = Desktop(backend="uia")
            wins = desktop.windows(title_re=".*", control_type="Window")
            for win in wins:
                try:
                    pname = win.element_info.process_id
                    # look for address bar
                    bar = win.child_window(control_type="Edit", found_index=0)
                    url = bar.get_value()
                    if url and ("http" in url or "." in url):
                        return url.strip()
                except Exception:
                    continue
        except Exception:
            pass

        # PowerShell UIAutomation fallback for Edge/Chrome
        try:
            ps = (
                "Add-Type -AssemblyName UIAutomationClient; "
                "$ae = [System.Windows.Automation.AutomationElement]; "
                "$root = $ae::RootElement; "
                "$cond = New-Object System.Windows.Automation.PropertyCondition("
                "    $ae::ControlTypeProperty, "
                "    [System.Windows.Automation.ControlType]::Edit); "
                "$bar = $root.FindFirst('Descendants', $cond); "
                "if ($bar) { $bar.GetCurrentPropertyValue($ae::NameProperty) }"
            )
            result = subprocess.run(
                ["powershell", "-NoProfile", "-Command", ps],
                capture_output=True, text=True, timeout=3,
            )
            url = (result.stdout or "").strip()
            if url and "http" in url:
                return url
        except Exception:
            pass

        return ""

    # ------------------------------------------------------------------
    # Snapshot builder
    # ------------------------------------------------------------------

    def snapshot(self, include_selection: bool = False,
                 include_url: bool = True) -> WindowContext:
        """
        Build and return a fresh WindowContext.
        include_selection=True does a clipboard-swap (slight delay).
        """
        hwnd, title, process_name, pid = self._get_active_window()
        profile = self._match_profile(title, process_name)

        ctx = WindowContext(
            hwnd=hwnd,
            title=title,
            process_name=process_name,
            process_id=pid,
            profile=profile,
            timestamp=time.monotonic(),
        )

        if include_url and profile and profile.category == "browser":
            ctx.browser_url = self._get_browser_url(process_name)

        if include_selection:
            ctx.selected_text = self.get_selected_text()

        with self._lock:
            self._current = ctx

        return ctx

    # ------------------------------------------------------------------
    # Background polling (lightweight — just window title + process)
    # ------------------------------------------------------------------

    def _poll(self) -> None:
        while not self._stop_event.is_set():
            try:
                self.snapshot(include_selection=False, include_url=False)
            except Exception as exc:
                logger.debug("Context poll error: %s", exc)
            self._stop_event.wait(timeout=1.5)

    def start(self) -> None:
        self._stop_event.clear()
        self._poll_thread = threading.Thread(
            target=self._poll, daemon=True, name="lumi-context-poll"
        )
        self._poll_thread.start()
        logger.info("ContextEngine started")

    def stop(self) -> None:
        self._stop_event.set()
        logger.info("ContextEngine stopped")

    @property
    def current(self) -> WindowContext:
        with self._lock:
            return self._current

    def current_app(self) -> str:
        return self.current.app_name

    def current_title(self) -> str:
        return self.current.title


# ---------------------------------------------------------------------------
# Smart action handlers — wired into execute_intent
# ---------------------------------------------------------------------------

NEW_CONTEXT_ACTIONS = {
    "context_what_app",       # "what app am I in"
    "context_explain",        # "explain this" / "explain this code"
    "context_fix_error",      # "fix this error"
    "context_summarise",      # "summarise this page"
    "context_smart",          # generic smart action resolved at runtime
    "browser_new_tab",
    "browser_close_tab",
    "browser_back",
    "browser_forward",
    "browser_bookmark",
    "editor_save",
    "editor_format",
    "editor_open_terminal",
    "terminal_clear",
}


def handle_context_intent(
    assistant: "VoiceAssistant",
    action: str,
    intent,
    ctx_engine: "ContextEngine",
) -> bool:
    """
    Called from the bottom of execute_intent().
    Returns True if handled, False if unknown.
    """
    speak = assistant.speak

    # ── What app am I in? ──────────────────────────────────────────────
    if action == "context_what_app":
        ctx = ctx_engine.snapshot()
        parts = [f"You're in {ctx.app_name}."]
        if ctx.title:
            parts.append(f"Window title is: {ctx.title[:60]}.")
        if ctx.is_browser and ctx.browser_url:
            parts.append(f"Current URL: {ctx.browser_url[:60]}.")
        speak(" ".join(parts))
        return True

    # ── Explain selected text ──────────────────────────────────────────
    if action in ("context_explain", "context_fix_error"):
        ctx = ctx_engine.snapshot(include_selection=True)
        selected = ctx.selected_text

        if not selected:
            if action == "context_fix_error":
                speak(
                    f"Select the error text in {ctx.app_name} first, "
                    "then say fix this error."
                )
            else:
                speak(
                    f"Select some text in {ctx.app_name} first, "
                    "then say explain this."
                )
            return True

        verb = "fix" if action == "context_fix_error" else "explain"
        prompt = _build_explain_prompt(verb, selected, ctx)
        speak(f"Asking AI to {verb} that for you.")
        result = _call_ai(assistant, prompt)
        speak(result or "I couldn't get a response from the AI.")
        return True

    # ── Summarise page / document ──────────────────────────────────────
    if action == "context_summarise":
        ctx = ctx_engine.snapshot(include_selection=True, include_url=True)

        if ctx.browser_url:
            prompt = (
                f"The user is viewing this URL in {ctx.app_name}: {ctx.browser_url}\n"
                f"Window title: {ctx.title}\n"
                "Give a concise 2-3 sentence summary of what this page is likely about "
                "based on the URL and title. Be direct."
            )
        elif ctx.selected_text:
            prompt = (
                f"Summarise the following text in 2-3 sentences:\n\n{ctx.selected_text[:800]}"
            )
        else:
            speak(
                f"I can see you're in {ctx.app_name}. "
                "Select some text or navigate to a webpage so I can summarise it."
            )
            return True

        speak("Summarising for you.")
        result = _call_ai(assistant, prompt)
        speak(result or "I couldn't generate a summary.")
        return True

    # ── Smart context action (resolved from profile) ───────────────────
    if action == "context_smart":
        ctx = ctx_engine.snapshot(include_selection=True, include_url=True)
        phrase = intent.text.strip().lower()
        profile = ctx.profile

        if not profile or phrase not in profile.smart_actions:
            speak(
                f"I don't have a smart action for '{phrase}' in {ctx.app_name}. "
                "Try 'explain this' or 'summarise this page'."
            )
            return True

        sub_action = profile.smart_actions[phrase]
        # Re-route to a concrete action
        sub_intent = type(intent)(action=f"context_{sub_action.replace('_', '_')}", text=intent.text)
        # Most smart actions map to explain/fix/summarise
        mapping = {
            "explain_selection": "context_explain",
            "fix_error": "context_fix_error",
            "summarise_page": "context_summarise",
        }
        resolved = mapping.get(sub_action)
        if resolved:
            sub_intent.action = resolved
            return handle_context_intent(assistant, resolved, sub_intent, ctx_engine)

        # Editor / browser shortcut actions
        return _handle_shortcut(assistant, sub_action, ctx)

    # ── Browser shortcuts ─────────────────────────────────────────────
    if action == "browser_new_tab":
        import pyautogui
        pyautogui.hotkey("ctrl", "t")
        speak("New tab opened.")
        return True

    if action == "browser_close_tab":
        import pyautogui
        pyautogui.hotkey("ctrl", "w")
        speak("Tab closed.")
        return True

    if action == "browser_back":
        import pyautogui
        pyautogui.hotkey("alt", "left")
        speak("Going back.")
        return True

    if action == "browser_forward":
        import pyautogui
        pyautogui.hotkey("alt", "right")
        speak("Going forward.")
        return True

    if action == "browser_bookmark":
        import pyautogui
        pyautogui.hotkey("ctrl", "d")
        speak("Page bookmarked.")
        return True

    # ── Editor shortcuts ──────────────────────────────────────────────
    if action == "editor_save":
        import pyautogui
        pyautogui.hotkey("ctrl", "s")
        speak("File saved.")
        return True

    if action == "editor_format":
        import pyautogui
        pyautogui.hotkey("shift", "alt", "f")
        speak("Formatting code.")
        return True

    if action == "editor_open_terminal":
        import pyautogui
        pyautogui.hotkey("ctrl", "`")
        speak("Opening terminal.")
        return True

    if action == "terminal_clear":
        import pyautogui
        pyautogui.typewrite("clear\n", interval=0.03)
        speak("Terminal cleared.")
        return True

    return False  # not handled


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _handle_shortcut(assistant, sub_action: str, ctx: WindowContext) -> bool:
    import pyautogui
    shortcuts = {
        "new_tab":        ("ctrl", "t"),
        "close_tab":      ("ctrl", "w"),
        "browser_back":   ("alt", "left"),
        "browser_forward":("alt", "right"),
        "bookmark_page":  ("ctrl", "d"),
        "save_file":      ("ctrl", "s"),
        "format_code":    ("shift", "alt", "f"),
        "open_terminal":  ("ctrl", "`"),
    }
    labels = {
        "new_tab": "New tab opened.",
        "close_tab": "Tab closed.",
        "browser_back": "Going back.",
        "browser_forward": "Going forward.",
        "bookmark_page": "Page bookmarked.",
        "save_file": "File saved.",
        "format_code": "Formatting code.",
        "open_terminal": "Terminal opened.",
        "clear_terminal": None,
    }
    if sub_action == "clear_terminal":
        pyautogui.typewrite("clear\n", interval=0.03)
        assistant.speak("Terminal cleared.")
        return True
    keys = shortcuts.get(sub_action)
    if keys:
        pyautogui.hotkey(*keys)
        msg = labels.get(sub_action, "Done.")
        if msg:
            assistant.speak(msg)
        return True
    return False


def _build_explain_prompt(verb: str, text: str, ctx: WindowContext) -> str:
    app = ctx.app_name
    category = ctx.profile.category if ctx.profile else "unknown"
    preview = text[:600]

    if verb == "fix":
        return (
            f"The user is in {app} (category: {category}) and selected this text:\n\n"
            f"{preview}\n\n"
            "This appears to be an error or problem. "
            "Diagnose what's wrong and give a clear, concise fix. "
            "If it's code, show the corrected version. "
            "Keep the response under 120 words — the user will hear it via text-to-speech."
        )
    return (
        f"The user is in {app} (category: {category}) and selected this text:\n\n"
        f"{preview}\n\n"
        "Explain what this means clearly and concisely. "
        "Keep the response under 100 words — it will be read aloud via text-to-speech. "
        "Skip markdown formatting."
    )


def _call_ai(assistant: "VoiceAssistant", prompt: str) -> str:
    """Call whichever AI provider is configured. Returns plain text."""
    try:
        if assistant.config.ai_provider == "gemini" and assistant.gemini_api_key:
            return assistant._gemini_generate_content(
                model=assistant.config.gemini_model,
                parts=[{"text": prompt}],
                temperature=0.3,
            )
        if assistant.openai_client:
            resp = assistant.openai_client.chat.completions.create(
                model=assistant.config.openai_model,
                temperature=0.3,
                messages=[
                    {"role": "system", "content": "You are a concise voice assistant. Keep all replies under 120 words. No markdown."},
                    {"role": "user", "content": prompt},
                ],
            )
            from assistant import _to_text
            return _to_text(resp.choices[0].message.content).strip()
    except Exception as exc:
        logger.warning("AI call failed: %s", exc)
    return ""


# ---------------------------------------------------------------------------
# Factory — called from assistant.py
# ---------------------------------------------------------------------------

def build_context_engine() -> ContextEngine:
    engine = ContextEngine()
    engine.start()
    return engine
