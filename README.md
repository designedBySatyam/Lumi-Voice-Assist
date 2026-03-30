# Lumi Voice Assistant v2.0

Lumi is a local Windows voice assistant with:

- Voice input + voice replies (female voice by default)
- Weather forecast (no API key needed)
- Known website shortcuts (`yt`, `github`, `claude`, etc.)
- Screen-aware commands (`read my screen`)
- Playback + scroll automation (`play this video`, `scroll down`)
- **Timers** — `set a timer for 5 minutes`
- **Notes** — `make a note: buy milk`, `read my notes`
- **Math calculator** — `calculate 20 percent of 850`
- **Wikipedia lookups** — `what is machine learning`
- **System info** — `battery status`, `CPU usage`, `RAM`
- **Screenshot save** — `take a screenshot` → saves to Desktop
- **Clipboard** — `read my clipboard`, `copy last response`
- **Folder shortcuts** — `open downloads`, `open desktop`
- **Media controls** — `next track`, `previous track`
- **Window controls** — `minimize window`, `close window`
- **Eye control** — gaze cursor + blink gestures (`start eye control`)
- **IP address** — `what is my IP`
- **Jokes** — `tell me a joke`
- **Self-learning** from corrections (`no, I meant ...`)
- Desktop UI interface

---

## 1. Install

```powershell
py -3.13 -m venv .venv
.venv\Scripts\activate
python --version
pip install -r requirements.txt
```

Use Python `3.13.x` for `PyAudio` wheel compatibility.

---

## 2. Configure

Copy `.env.example` to `.env` and edit values:

```powershell
Copy-Item .env.example .env
notepad .env
```

Important fields:

| Variable | Default | Description |
|---|---|---|
| `ASSISTANT_NAME` | `Lumi` | Name of your assistant |
| `ASSISTANT_VOICE` | `female` | `female` or `male` |
| `AI_PROVIDER` | `openai` | `openai` or `gemini` |
| `SCREEN_READ_MODE` | `auto` | `auto`, `cloud`, or `offline` |
| `LEARNING_ENABLED` | `1` | `1` enables self-learning |
| `LEARNING_FILE` | `lumi_learning.json` | Learned command mappings |
| `NOTES_FILE` | `lumi_notes.txt` | Where notes are stored |
| `OPENAI_API_KEY` | — | Required when `AI_PROVIDER=openai` |
| `GEMINI_API_KEY` | — | Required when `AI_PROVIDER=gemini` |
| `USE_AI_INTENT` | `0` | `1` enables AI intent routing |
| `VISION_MODEL` | `gpt-4o-mini` | Model for screen reading |
| `GEMINI_MODEL` | `gemini-2.0-flash` | Gemini text model |
| `GEMINI_VISION_MODEL` | `gemini-2.0-flash` | Gemini vision model |
| `TESSERACT_CMD` | — | Path to `tesseract.exe` (offline OCR) |
| `ENABLE_UI` | `1` | `0` for terminal-only mode |
| `WEATHER_DEFAULT_CITY` | — | Default city for weather |
| `ALLOW_POWER_ACTIONS` | `0` | `1` to allow shutdown/restart |
| `WAKE_WORD` | — | Optional wake word (e.g. `hey lumi`) |
| `VOICE_RATE` | `180` | Speech rate in words per minute |

---

## 3. Run

Preferred production mode (system tray + background service):

```powershell
python lumi_tray.py
```

Tray mode options:

```powershell
python lumi_tray.py --no-tray      # headless service mode
python lumi_tray.py --install      # auto-start at login
python lumi_tray.py --uninstall    # remove auto-start
```

Classic direct assistant mode (still supported):

```powershell
python assistant.py
python assistant.py --no-ui
```

---

## Supported Commands

### Basics
- `what is your name` / `who are you`
- `what time is it` / `current time`
- `what day is it` / `today's date`
- `help` / `what can you do`
- `repeat` / `say that again`
- `tell me a joke`
- `stop lumi` / `exit`

### Tray Service (Phase 5)
- `mute lumi` / `stop listening`
- `unmute lumi` / `start listening again`
- `restart lumi`

### Web & Apps
- `open yt` / `open youtube`
- `open github`
- `open my portfolio`
- `open downloads` / `open desktop` / `open documents`
- `open notepad` / `open calculator` / `open vscode`
- `open chrome` / `open edge`
- `search python decorators`
- `google best pizza recipes`

### File Search & Calendar (Phase 4)
- `find my resume`
- `search for invoice pdf`
- `show its folder`
- `what's on my calendar today`
- `what do i have tomorrow`
- `what is my next meeting`
- `add standup tomorrow at 9am`

### YouTube & Media
- `play lo-fi beats on youtube`
- `play video of shruti rajput`
- `play this video` / `pause this video`
- `stop the video`
- `next video` / `previous video`
- `fullscreen` / `mute video`
- `forward video` / `rewind video`
- `play the first video`
- `next track` / `previous track`
- `play music`

### Scroll & Window
- `scroll down` / `scroll up`
- `scroll down 1200`
- `scroll top` / `scroll bottom`
- `minimize window` / `maximize window`
- `close window`

### Timers & Notes
- `set a timer for 5 minutes`
- `set a timer for 1 hour 30 minutes` *(say as two commands)*
- `2 minute timer`
- `remind me in 10 minutes`
- `cancel timer`
- `make a note: buy groceries`
- `take a note: meeting at 3pm`
- `read my notes`
- `clear notes`

### Math & Knowledge
- `calculate 15 times 8`
- `what is 20 percent of 850`
- `solve 144 divided by 12`
- `what is machine learning`
- `who is Nikola Tesla`
- `explain quantum computing`
- `define recursion`

### System
- `battery status` / `battery`
- `cpu usage`
- `ram usage` / `memory`
- `disk usage` / `storage`
- `system info`
- `what is my IP` / `my IP address`
- `take a screenshot`
- `read my clipboard`
- `copy last response` / `copy that`

### Weather
- `weather in mumbai`
- `weather forecast for delhi tomorrow`
- `temperature in london`

### Volume
- `volume up` / `volume down`
- `volume up 8`
- `mute`

### Type text
- `type hello world`
- `write my name is Satyam`

### Eye Control (optional)
- `start eye control` / `enable eye tracking`
- `stop eye control`
- `pause eye control` / `resume eye control`
- `eye control status`
- Gestures while running:
  - Single blink -> left click
  - Double blink -> right click
  - Long blink -> pause/resume

### Power (disabled by default)
- `shutdown` (requires `ALLOW_POWER_ACTIONS=1`)
- `restart`

### Learning
- `learn that X means Y` — teach Lumi a new mapping
- `no, I meant Y` — correct the last command
- `forget learned commands` — reset all learned mappings

---

## Notes

- `read my screen` uses `SCREEN_READ_MODE`:
  - `auto`: tries cloud first, falls back to offline OCR
  - `cloud`: uses only AI API provider
  - `offline`: uses local Tesseract OCR (no API key needed)
- System info (`battery`, `cpu`, `ram`, `disk`) requires `psutil` — install with `pip install psutil`
- Screenshots are saved to your Desktop as `lumi_screenshot_YYYYMMDD_HHMMSS.png`
- Notes are stored in `lumi_notes.txt` (configurable via `NOTES_FILE`)
- Timers beep when done (Windows only via `winsound`)
- Self-learning: say `no, I meant ...` after a wrong action and Lumi remembers
- Video toggle uses `K` key (works on YouTube and many players)
- Shutdown/restart are protected behind `ALLOW_POWER_ACTIONS=1`
- Eye control needs webcam + optional packages: `pip install opencv-python mediapipe`

---

## Switch To Gemini

```env
AI_PROVIDER=gemini
GEMINI_API_KEY=your_gemini_key
GEMINI_MODEL=gemini-2.0-flash
GEMINI_VISION_MODEL=gemini-2.0-flash
USE_AI_INTENT=1
```

Then restart Lumi.

---

## Run Without Any API Keys

```env
USE_AI_INTENT=0
AI_PROVIDER=openai
OPENAI_API_KEY=
GEMINI_API_KEY=
SCREEN_READ_MODE=offline
```

For offline `read my screen`, install OCR:

```powershell
pip install --upgrade pytesseract Pillow pyscreeze pyautogui
```

Then install [Tesseract OCR for Windows](https://github.com/UB-Mannheim/tesseract/wiki) and set:

```env
TESSERACT_CMD=C:\Program Files\Tesseract-OCR\tesseract.exe
```

---

## Troubleshooting

**PyAudio install fails:**
```powershell
deactivate
Remove-Item -Recurse -Force .venv
py -3.13 -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

**`read my screen` fails with pyscreeze/screenshot error:**
```powershell
pip install --upgrade Pillow pyscreeze pyautogui
```

**`battery`, `cpu`, `ram` commands not working:**
```powershell
pip install psutil
```

Then restart Lumi.

**`take a screenshot` fails:**
Make sure `Pillow` and `pyscreeze` are installed (included in `requirements.txt`).
