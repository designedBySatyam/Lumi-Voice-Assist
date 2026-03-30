"""
lumi_os_services.py — Phase 4: OS Services Layer
=================================================
Three native Windows services wired into Lumi:

  1. Toast notifications  — win11toast / Windows Runtime
                            Lumi can push native Windows toasts for
                            timers, reminders, and important alerts.

  2. File search          — Windows Search index + Everything SDK fallback
                            "find my resume" / "search for invoice PDF"
                            Returns results in under 200ms when Everything
                            is installed, falls back to os.walk otherwise.

  3. Calendar & reminders — win32com → Outlook calendar (if installed)
                            + Google Calendar API (if credentials present)
                            + plain .ics file fallback
                            "what's on my calendar today"
                            "add a meeting tomorrow at 3pm"
                            "remind me about standup at 9am"

Install
-------
    pip install win11toast pywin32 google-api-python-client google-auth-httplib2 google-auth-oauthlib

Everything (optional, instant file search):
    https://www.voidtools.com/downloads/  — install and keep running
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from assistant import VoiceAssistant

logger = logging.getLogger("lumi.os_services")

# ---------------------------------------------------------------------------
# Optional deps
# ---------------------------------------------------------------------------

try:
    from win11toast import toast, notify
    _HAS_TOAST = True
except ImportError:
    toast = notify = None
    _HAS_TOAST = False

try:
    import win32com.client as win32com
    _HAS_WIN32COM = True
except ImportError:
    win32com = None
    _HAS_WIN32COM = False

try:
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from google.auth.transport.requests import Request as GRequest
    from googleapiclient.discovery import build as gbuild
    _HAS_GCAL = True
except ImportError:
    _HAS_GCAL = False

# Google Calendar scopes
GCAL_SCOPES = ["https://www.googleapis.com/auth/calendar"]
GCAL_CREDS_FILE = "google_credentials.json"
GCAL_TOKEN_FILE = "google_token.json"


# ---------------------------------------------------------------------------
# 1. Toast Notifications
# ---------------------------------------------------------------------------

class ToastService:
    """
    Push native Windows toast notifications.
    Falls back to a PowerShell BurntToast-style notification
    if win11toast is not installed.
    """

    @staticmethod
    def send(
        title: str,
        body: str = "",
        duration: str = "short",   # "short" (7s) or "long" (25s)
        audio: str = "default",    # "default" | "silent" | "alarm"
    ) -> bool:
        """Push a Windows toast. Returns True on success."""

        # --- win11toast (preferred) ---
        if _HAS_TOAST:
            try:
                audio_map = {
                    "default": "ms-winsoundevent:Notification.Default",
                    "silent":  "silent",
                    "alarm":   "ms-winsoundevent:Notification.Alarm",
                }
                toast(
                    title,
                    body=body or None,
                    duration=duration,
                    audio=audio_map.get(audio, "ms-winsoundevent:Notification.Default"),
                    app_id="Lumi Assistant",
                )
                logger.info("Toast sent: %s", title)
                return True
            except Exception as exc:
                logger.warning("win11toast failed: %s", exc)

        # --- PowerShell fallback ---
        return ToastService._powershell_toast(title, body)

    @staticmethod
    def _powershell_toast(title: str, body: str) -> bool:
        try:
            ps = (
                "[Windows.UI.Notifications.ToastNotificationManager, "
                "Windows.UI.Notifications, ContentType=WindowsRuntime] | Out-Null; "
                "[Windows.Data.Xml.Dom.XmlDocument, "
                "Windows.Data.Xml.Dom.XmlDocument, ContentType=WindowsRuntime] | Out-Null; "
                "$template = [Windows.UI.Notifications.ToastTemplateType]::ToastText02; "
                "$xml = [Windows.UI.Notifications.ToastNotificationManager]"
                "::GetTemplateContent($template); "
                "$xml.GetElementsByTagName('text')[0].AppendChild("
                f"    $xml.CreateTextNode('{title.replace(chr(39), '')}')) | Out-Null; "
                "$xml.GetElementsByTagName('text')[1].AppendChild("
                f"    $xml.CreateTextNode('{body.replace(chr(39), '')}')) | Out-Null; "
                "$toast = [Windows.UI.Notifications.ToastNotification]::new($xml); "
                "$notifier = [Windows.UI.Notifications.ToastNotificationManager]"
                "::CreateToastNotifier('Lumi Assistant'); "
                "$notifier.Show($toast);"
            )
            subprocess.run(
                ["powershell", "-NoProfile", "-Command", ps],
                capture_output=True, timeout=5,
            )
            logger.info("PowerShell toast sent: %s", title)
            return True
        except Exception as exc:
            logger.warning("PowerShell toast failed: %s", exc)
            return False

    @staticmethod
    def timer_done(label: str = "") -> None:
        title = "Timer done!" if not label else f"Timer: {label}"
        ToastService.send(title, "Your Lumi timer has finished.", audio="alarm")

    @staticmethod
    def reminder(text: str) -> None:
        ToastService.send("Lumi Reminder", text, duration="long", audio="alarm")


# ---------------------------------------------------------------------------
# 2. File Search
# ---------------------------------------------------------------------------

_EVERYTHING_PORT = 80        # Everything HTTP server default
_EVERYTHING_EXE_PATHS = [
    r"C:\Program Files\Everything\Everything.exe",
    r"C:\Program Files (x86)\Everything\Everything.exe",
    r"C:\Tools\Everything\Everything.exe",
]

_COMMON_SEARCH_ROOTS = [
    Path.home() / "Desktop",
    Path.home() / "Documents",
    Path.home() / "Downloads",
    Path.home() / "Pictures",
    Path.home() / "OneDrive",
]

_EXTENSION_MAP = {
    "pdf":        [".pdf"],
    "document":   [".docx", ".doc", ".odt", ".txt", ".md"],
    "spreadsheet":[".xlsx", ".xls", ".csv"],
    "image":      [".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"],
    "video":      [".mp4", ".mkv", ".avi", ".mov", ".wmv"],
    "audio":      [".mp3", ".wav", ".flac", ".aac", ".ogg"],
    "code":       [".py", ".js", ".ts", ".html", ".css", ".json", ".java", ".cpp"],
    "zip":        [".zip", ".rar", ".7z", ".tar", ".gz"],
    "presentation":[".pptx", ".ppt"],
}


class FileSearchService:
    """
    Fast file search. Priority order:
      1. Everything HTTP API  (instant, if Everything is running)
      2. Windows Search index via PowerShell
      3. os.walk fallback     (slower but always works)
    """

    @staticmethod
    def search(
        query: str,
        file_type: str = "",   # "pdf" | "document" | "image" | etc.
        max_results: int = 5,
    ) -> list[str]:
        """Return a list of matching file paths."""
        results = (
            FileSearchService._search_everything(query, file_type, max_results)
            or FileSearchService._search_windows_index(query, file_type, max_results)
            or FileSearchService._search_walk(query, file_type, max_results)
        )
        return results[:max_results]

    # ── Everything HTTP API ────────────────────────────────────────────

    @staticmethod
    def _everything_running() -> bool:
        import socket
        try:
            s = socket.create_connection(("localhost", _EVERYTHING_PORT), timeout=0.3)
            s.close()
            return True
        except Exception:
            return False

    @staticmethod
    def _search_everything(
        query: str, file_type: str, max_results: int
    ) -> list[str]:
        if not FileSearchService._everything_running():
            return []
        try:
            import urllib.request, urllib.parse
            ext_filter = ""
            if file_type and file_type in _EXTENSION_MAP:
                exts = "|".join(e.lstrip(".") for e in _EXTENSION_MAP[file_type])
                ext_filter = f" ext:{exts}"
            q = urllib.parse.quote(query + ext_filter)
            url = (
                f"http://localhost:{_EVERYTHING_PORT}/"
                f"?search={q}&json=1&count={max_results}"
            )
            with urllib.request.urlopen(url, timeout=2) as r:
                data = json.loads(r.read().decode("utf-8"))
            results = data.get("results") or []
            return [
                os.path.join(r.get("path", ""), r.get("name", ""))
                for r in results
                if r.get("name")
            ]
        except Exception as exc:
            logger.debug("Everything search failed: %s", exc)
            return []

    # ── Windows Search index (PowerShell) ─────────────────────────────

    @staticmethod
    def _search_windows_index(
        query: str, file_type: str, max_results: int
    ) -> list[str]:
        try:
            ext_where = ""
            if file_type and file_type in _EXTENSION_MAP:
                exts = _EXTENSION_MAP[file_type]
                conditions = " OR ".join(
                    f"System.FileName LIKE '%.{e.lstrip('.')}'%" for e in exts
                )
                ext_where = f" AND ({conditions})"

            ps = (
                f"$con = New-Object -ComObject ADODB.Connection; "
                f"$con.Open('Provider=Search.CollatorDSO;Extended Properties=Application=Windows'); "
                f"$rs = $con.Execute(\"SELECT System.ItemPathDisplay FROM SystemIndex "
                f"WHERE System.FileName LIKE '%{query}%'{ext_where} "
                f"ORDER BY System.DateModified DESC\"); "
                f"$i=0; while(-not $rs.EOF -and $i -lt {max_results}) "
                f"{{ $rs.Fields.Item('System.ItemPathDisplay').Value; $rs.MoveNext(); $i++ }}"
            )
            result = subprocess.run(
                ["powershell", "-NoProfile", "-Command", ps],
                capture_output=True, text=True, timeout=8,
            )
            paths = [
                p.strip() for p in (result.stdout or "").splitlines()
                if p.strip() and os.path.exists(p.strip())
            ]
            logger.debug("Windows index search found %d results", len(paths))
            return paths
        except Exception as exc:
            logger.debug("Windows index search failed: %s", exc)
            return []

    # ── os.walk fallback ──────────────────────────────────────────────

    @staticmethod
    def _search_walk(
        query: str, file_type: str, max_results: int
    ) -> list[str]:
        exts = _EXTENSION_MAP.get(file_type, []) if file_type else []
        query_lower = query.lower()
        found = []
        roots = _COMMON_SEARCH_ROOTS + [Path.home()]
        seen_roots = set()

        for root in roots:
            root = Path(root)
            if not root.exists() or root in seen_roots:
                continue
            seen_roots.add(root)
            try:
                for dirpath, _, filenames in os.walk(root):
                    for fname in filenames:
                        name_lower = fname.lower()
                        if query_lower not in name_lower:
                            continue
                        if exts and not any(
                            name_lower.endswith(e) for e in exts
                        ):
                            continue
                        full = os.path.join(dirpath, fname)
                        found.append(full)
                        if len(found) >= max_results * 3:
                            break
                    if len(found) >= max_results * 3:
                        break
            except PermissionError:
                continue

        # Sort by modification time, newest first
        found.sort(
            key=lambda p: os.path.getmtime(p) if os.path.exists(p) else 0,
            reverse=True,
        )
        return found[:max_results]

    @staticmethod
    def open_file(path: str) -> bool:
        try:
            os.startfile(path)
            return True
        except Exception as exc:
            logger.warning("Open file failed: %s", exc)
            return False

    @staticmethod
    def open_containing_folder(path: str) -> bool:
        try:
            subprocess.Popen(["explorer", "/select,", path])
            return True
        except Exception as exc:
            logger.warning("Open folder failed: %s", exc)
            return False


# ---------------------------------------------------------------------------
# 3. Calendar & Reminders
# ---------------------------------------------------------------------------

class CalendarService:
    """
    Read and write calendar events.
    Priority: Outlook (win32com) → Google Calendar → plain JSON fallback.
    """

    _local_file = Path("lumi_reminders.json")

    # ── Outlook ───────────────────────────────────────────────────────

    @staticmethod
    def _outlook_available() -> bool:
        if not _HAS_WIN32COM:
            return False
        try:
            import pythoncom
            pythoncom.CoInitialize()
            win32com.Dispatch("Outlook.Application")
            return True
        except Exception:
            return False

    @staticmethod
    def _get_outlook_events(
        start: datetime, end: datetime
    ) -> list[dict]:
        try:
            import pythoncom
            pythoncom.CoInitialize()
            outlook = win32com.Dispatch("Outlook.Application")
            ns = outlook.GetNamespace("MAPI")
            cal = ns.GetDefaultFolder(9)  # 9 = olFolderCalendar
            items = cal.Items
            items.IncludeRecurrences = True
            items.Sort("[Start]")
            restriction = (
                f"[Start] >= '{start.strftime('%m/%d/%Y %H:%M')}' "
                f"AND [End] <= '{end.strftime('%m/%d/%Y %H:%M')}'"
            )
            filtered = items.Restrict(restriction)
            events = []
            for item in filtered:
                try:
                    events.append({
                        "title":   item.Subject,
                        "start":   str(item.Start),
                        "end":     str(item.End),
                        "location": getattr(item, "Location", ""),
                        "source":  "outlook",
                    })
                except Exception:
                    continue
            return events
        except Exception as exc:
            logger.warning("Outlook calendar read failed: %s", exc)
            return []

    @staticmethod
    def _add_outlook_event(
        title: str, start: datetime, end: datetime,
        location: str = "", notes: str = ""
    ) -> bool:
        try:
            import pythoncom
            pythoncom.CoInitialize()
            outlook = win32com.Dispatch("Outlook.Application")
            appt = outlook.CreateItem(1)  # 1 = olAppointmentItem
            appt.Subject = title
            appt.Start = start.strftime("%m/%d/%Y %H:%M")
            appt.End = end.strftime("%m/%d/%Y %H:%M")
            if location:
                appt.Location = location
            if notes:
                appt.Body = notes
            appt.ReminderMinutesBeforeStart = 15
            appt.Save()
            logger.info("Outlook event created: %s", title)
            return True
        except Exception as exc:
            logger.warning("Outlook event creation failed: %s", exc)
            return False

    # ── Google Calendar ───────────────────────────────────────────────

    @staticmethod
    def _gcal_service():
        if not _HAS_GCAL:
            return None
        creds = None
        token_path = Path(GCAL_TOKEN_FILE)
        creds_path = Path(GCAL_CREDS_FILE)
        if not creds_path.exists():
            return None
        if token_path.exists():
            creds = Credentials.from_authorized_user_file(str(token_path), GCAL_SCOPES)
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                try:
                    creds.refresh(GRequest())
                except Exception:
                    creds = None
            if not creds:
                try:
                    flow = InstalledAppFlow.from_client_secrets_file(
                        str(creds_path), GCAL_SCOPES
                    )
                    creds = flow.run_local_server(port=0)
                    token_path.write_text(creds.to_json())
                except Exception as exc:
                    logger.warning("Google Calendar auth failed: %s", exc)
                    return None
        try:
            return gbuild("calendar", "v3", credentials=creds)
        except Exception as exc:
            logger.warning("Google Calendar service build failed: %s", exc)
            return None

    @staticmethod
    def _get_gcal_events(
        start: datetime, end: datetime
    ) -> list[dict]:
        svc = CalendarService._gcal_service()
        if not svc:
            return []
        try:
            result = (
                svc.events()
                .list(
                    calendarId="primary",
                    timeMin=start.isoformat() + "Z",
                    timeMax=end.isoformat() + "Z",
                    singleEvents=True,
                    orderBy="startTime",
                )
                .execute()
            )
            events = []
            for e in result.get("items", []):
                start_str = (
                    e["start"].get("dateTime") or e["start"].get("date", "")
                )
                end_str = (
                    e["end"].get("dateTime") or e["end"].get("date", "")
                )
                events.append({
                    "title":    e.get("summary", "Untitled"),
                    "start":    start_str,
                    "end":      end_str,
                    "location": e.get("location", ""),
                    "source":   "google",
                })
            return events
        except Exception as exc:
            logger.warning("Google Calendar read failed: %s", exc)
            return []

    @staticmethod
    def _add_gcal_event(
        title: str, start: datetime, end: datetime,
        location: str = ""
    ) -> bool:
        svc = CalendarService._gcal_service()
        if not svc:
            return False
        try:
            event = {
                "summary": title,
                "start":   {"dateTime": start.isoformat(), "timeZone": "Asia/Kolkata"},
                "end":     {"dateTime": end.isoformat(),   "timeZone": "Asia/Kolkata"},
                "reminders": {"useDefault": True},
            }
            if location:
                event["location"] = location
            svc.events().insert(calendarId="primary", body=event).execute()
            logger.info("Google Calendar event created: %s", title)
            return True
        except Exception as exc:
            logger.warning("Google Calendar event creation failed: %s", exc)
            return False

    # ── Local JSON fallback ───────────────────────────────────────────

    @staticmethod
    def _load_local() -> list[dict]:
        try:
            if CalendarService._local_file.exists():
                return json.loads(
                    CalendarService._local_file.read_text(encoding="utf-8")
                )
        except Exception:
            pass
        return []

    @staticmethod
    def _save_local(events: list[dict]) -> None:
        try:
            CalendarService._local_file.write_text(
                json.dumps(events, indent=2, default=str), encoding="utf-8"
            )
        except Exception as exc:
            logger.warning("Local calendar save failed: %s", exc)

    @staticmethod
    def _get_local_events(start: datetime, end: datetime) -> list[dict]:
        events = CalendarService._load_local()
        result = []
        for e in events:
            try:
                ev_start = datetime.fromisoformat(e["start"])
                if start <= ev_start <= end:
                    result.append(e)
            except Exception:
                continue
        return sorted(result, key=lambda x: x["start"])

    @staticmethod
    def _add_local_event(
        title: str, start: datetime, end: datetime,
        notes: str = ""
    ) -> bool:
        events = CalendarService._load_local()
        events.append({
            "title":  title,
            "start":  start.isoformat(),
            "end":    end.isoformat(),
            "notes":  notes,
            "source": "local",
        })
        CalendarService._save_local(events)
        return True

    # ── Public API ────────────────────────────────────────────────────

    @staticmethod
    def get_events(start: datetime, end: datetime) -> list[dict]:
        """Return events from best available source."""
        if CalendarService._outlook_available():
            events = CalendarService._get_outlook_events(start, end)
            if events:
                return events
        events = CalendarService._get_gcal_events(start, end)
        if events:
            return events
        return CalendarService._get_local_events(start, end)

    @staticmethod
    def add_event(
        title: str, start: datetime, end: datetime,
        location: str = "", notes: str = ""
    ) -> bool:
        """Add event to best available calendar."""
        if CalendarService._outlook_available():
            if CalendarService._add_outlook_event(
                title, start, end, location, notes
            ):
                return True
        if CalendarService._add_gcal_event(title, start, end, location):
            return True
        return CalendarService._add_local_event(title, start, end, notes)

    @staticmethod
    def get_todays_events() -> list[dict]:
        now = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        end = now.replace(hour=23, minute=59, second=59)
        return CalendarService.get_events(now, end)

    @staticmethod
    def get_tomorrows_events() -> list[dict]:
        tomorrow = datetime.now() + timedelta(days=1)
        start = tomorrow.replace(hour=0, minute=0, second=0, microsecond=0)
        end = tomorrow.replace(hour=23, minute=59, second=59)
        return CalendarService.get_events(start, end)


# ---------------------------------------------------------------------------
# Natural language time parser
# ---------------------------------------------------------------------------

def parse_event_time(text: str) -> Optional[datetime]:
    """
    Parse natural language time expressions into datetime.
    Handles: "tomorrow at 3pm", "today at 9:30am", "monday at 2",
             "next friday at 4pm", "in 2 hours", "at 10:30"
    """
    now = datetime.now()
    text = text.strip().lower()

    # "in X hours/minutes"
    m = re.search(r"in\s+(\d+)\s+(hour|minute|min)s?", text)
    if m:
        amount = int(m.group(1))
        unit = m.group(2)
        delta = timedelta(hours=amount) if "hour" in unit else timedelta(minutes=amount)
        return now + delta

    # Base date
    base = now
    if "tomorrow" in text:
        base = now + timedelta(days=1)
    elif "today" in text:
        base = now
    else:
        days = {
            "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
            "friday": 4, "saturday": 5, "sunday": 6,
        }
        for day, idx in days.items():
            if day in text:
                diff = (idx - now.weekday()) % 7 or 7
                base = now + timedelta(days=diff)
                break

    # Time component
    m = re.search(
        r"at\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)?",
        text, re.I
    )
    if m:
        hour = int(m.group(1))
        minute = int(m.group(2) or 0)
        meridiem = (m.group(3) or "").lower()
        if meridiem == "pm" and hour < 12:
            hour += 12
        elif meridiem == "am" and hour == 12:
            hour = 0
        elif not meridiem and hour < 7:
            hour += 12   # assume PM for ambiguous hours like "at 3"
        return base.replace(hour=hour, minute=minute, second=0, microsecond=0)

    # Just a date, no time — default to 9am
    return base.replace(hour=9, minute=0, second=0, microsecond=0)


def format_event_list(events: list[dict], day_label: str = "today") -> str:
    """Convert event list to a spoken string."""
    if not events:
        return f"You have no events {day_label}."
    parts = [f"You have {len(events)} event{'s' if len(events) != 1 else ''} {day_label}."]
    for e in events[:5]:
        title = e.get("title", "Untitled")
        start_raw = e.get("start", "")
        time_str = ""
        try:
            dt = datetime.fromisoformat(start_raw.replace("Z", ""))
            time_str = dt.strftime("%I:%M %p").lstrip("0")
        except Exception:
            time_str = start_raw[:10]
        loc = e.get("location", "")
        entry = f"{title} at {time_str}"
        if loc:
            entry += f" in {loc}"
        parts.append(entry + ".")
    return " ".join(parts)


# ---------------------------------------------------------------------------
# New allowed actions
# ---------------------------------------------------------------------------

NEW_OS_ACTIONS = {
    # Notifications
    "notify_send",
    # File search
    "file_search", "file_open", "file_show_folder",
    # Calendar
    "calendar_today", "calendar_tomorrow", "calendar_add",
    "calendar_next_event",
}


# ---------------------------------------------------------------------------
# Intent handler
# ---------------------------------------------------------------------------

def handle_os_services_intent(
    assistant: "VoiceAssistant",
    action: str,
    intent,
) -> bool:
    speak = assistant.speak

    # ── Toast notification ────────────────────────────────────────────
    if action == "notify_send":
        title = intent.target or "Lumi"
        body  = intent.text or ""
        ok = ToastService.send(title, body)
        if not ok:
            speak("I couldn't send a notification right now.")
        return True

    # ── File search ───────────────────────────────────────────────────
    if action == "file_search":
        query = intent.text.strip()
        file_type = intent.target.strip().lower()
        if not query:
            speak("What file should I search for?")
            return True
        speak(f"Searching for {query}.")
        results = FileSearchService.search(query, file_type=file_type, max_results=5)
        if not results:
            speak(f"I couldn't find any files matching {query}.")
            return True
        names = [Path(p).name for p in results]
        speak(
            f"I found {len(results)} file{'s' if len(results) != 1 else ''}. "
            f"The top result is {names[0]}. "
            + (f"Others include: {', '.join(names[1:3])}." if len(names) > 1 else "")
        )
        # Open the top result
        assistant._last_file_results = results
        FileSearchService.open_file(results[0])
        return True

    if action == "file_open":
        results = getattr(assistant, "_last_file_results", [])
        if not results:
            speak("Search for a file first.")
            return True
        FileSearchService.open_file(results[0])
        speak(f"Opening {Path(results[0]).name}.")
        return True

    if action == "file_show_folder":
        results = getattr(assistant, "_last_file_results", [])
        if not results:
            speak("Search for a file first.")
            return True
        FileSearchService.open_containing_folder(results[0])
        speak(f"Opening the folder containing {Path(results[0]).name}.")
        return True

    # ── Calendar: today ───────────────────────────────────────────────
    if action == "calendar_today":
        events = CalendarService.get_todays_events()
        speak(format_event_list(events, "today"))
        return True

    # ── Calendar: tomorrow ────────────────────────────────────────────
    if action == "calendar_tomorrow":
        events = CalendarService.get_tomorrows_events()
        speak(format_event_list(events, "tomorrow"))
        return True

    # ── Calendar: next event ─────────────────────────────────────────
    if action == "calendar_next_event":
        now = datetime.now()
        end = now + timedelta(hours=12)
        events = CalendarService.get_events(now, end)
        if not events:
            speak("You have no upcoming events in the next 12 hours.")
            return True
        e = events[0]
        title = e.get("title", "Untitled")
        start_raw = e.get("start", "")
        try:
            dt = datetime.fromisoformat(start_raw.replace("Z", ""))
            time_str = dt.strftime("%I:%M %p").lstrip("0")
            delta = dt - now
            mins = int(delta.total_seconds() // 60)
            if mins < 60:
                when = f"in {mins} minutes"
            else:
                hrs = mins // 60
                when = f"in {hrs} hour{'s' if hrs != 1 else ''}"
        except Exception:
            time_str = start_raw
            when = ""
        loc = e.get("location", "")
        msg = f"Your next event is {title} at {time_str}"
        if when:
            msg += f", {when}"
        if loc:
            msg += f", at {loc}"
        speak(msg + ".")
        return True

    # ── Calendar: add event ───────────────────────────────────────────
    if action == "calendar_add":
        raw = intent.text.strip()
        if not raw:
            speak("What event should I add? Say something like: add meeting tomorrow at 3pm.")
            return True

        # Extract title and time from the raw command
        # Pattern: "add/schedule/create [title] [on/at time expression]"
        time_match = re.search(
            r"(tomorrow|today|monday|tuesday|wednesday|thursday|friday|saturday|sunday|"
            r"in\s+\d+\s+(?:hour|minute)|at\s+\d{1,2}(?::\d{2})?\s*(?:am|pm)?)",
            raw, re.I
        )
        if time_match:
            time_str = raw[time_match.start():]
            title = raw[:time_match.start()].strip().rstrip(" on at").strip()
        else:
            title = raw
            time_str = "tomorrow at 9am"

        if not title:
            speak("What should I call the event?")
            return True

        start_dt = parse_event_time(time_str)
        if not start_dt:
            speak("I couldn't understand that time. Try 'tomorrow at 3pm' or 'today at 9:30am'.")
            return True

        end_dt = start_dt + timedelta(hours=1)
        ok = CalendarService.add_event(title.title(), start_dt, end_dt)

        if ok:
            time_spoken = start_dt.strftime("%A, %B %d at %I:%M %p").lstrip("0")
            speak(f"Added {title} to your calendar for {time_spoken}.")
        else:
            speak("I couldn't add that event. Make sure Outlook or Google Calendar is set up.")
        return True

    return False
