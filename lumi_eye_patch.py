"""
lumi_eye_patch.py — Eye Control Fix for assistant.py
=====================================================
The eye control actions exist in ALLOWED_ACTIONS and execute_intent
but NO rules exist in _make_rules() to route voice to them.
This is the ONLY reason eye control was completely broken.

Just 1 change needed in assistant.py.
"""


# ============================================================
# THE ONLY CHANGE: Add these rules to _make_rules()
#
# Paste this block BEFORE the "# --- Open (catch-all)" comment,
# right after the calendar rules (the last block before catch-all).
# ============================================================

EYE_CONTROL_RULES = r"""
            # --- Eye control ---
            (c(r"(start|enable|turn on|activate)\s+(eye\s+(control|tracking)|gaze\s+(control|tracking))", I),
             lambda m, cmd: Intent(action="eye_control_start")),
            (c(r"(stop|disable|turn off|deactivate)\s+(eye\s+(control|tracking)|gaze\s+(control|tracking))", I),
             lambda m, cmd: Intent(action="eye_control_stop")),
            (c(r"(pause|hold|freeze)\s+(eye\s+(control|tracking)|gaze)", I),
             lambda m, cmd: Intent(action="eye_control_pause")),
            (c(r"(resume|continue|unfreeze)\s+(eye\s+(control|tracking)|gaze)", I),
             lambda m, cmd: Intent(action="eye_control_resume")),
            (c(r"(eye\s+control|gaze)\s+(status|state|on\?|running\?)", I),
             lambda m, cmd: Intent(action="eye_control_status")),
            (c(r"(calibrate|setup|set up)\s+(eye\s+(control|tracking)|gaze)", I),
             lambda m, cmd: Intent(action="eye_control_calibrate")),
"""

# Also add "eye_control_calibrate" to ALLOWED_ACTIONS:
# ALLOWED_ACTIONS.add("eye_control_calibrate")

# And add this handler inside execute_intent(), right after
# the existing eye_control_status block (around line 2233):
CALIBRATE_HANDLER = """
        if a == "eye_control_calibrate":
            if not self._eye.is_running():
                self.speak("Start eye control first, then say calibrate.")
            else:
                self.speak(
                    "Starting 5-point calibration. "
                    "Look at each dot and blink once to confirm. "
                    "Beginning now."
                )
                self._eye.start_calibration()
            return False
"""


# ============================================================
# INSTALL (if not already done)
# ============================================================
INSTALL = """
pip install opencv-python mediapipe
"""


# ============================================================
# CHEAT SHEET
# ============================================================
CHEAT_SHEET = """
Eye control voice commands:
  "Start eye control"
  "Enable eye tracking"
  "Stop eye control"
  "Pause eye control"        ← or long blink (>0.75s)
  "Resume eye control"
  "Eye control status"
  "Calibrate eye control"    ← run for accuracy (recommended)

Blink gestures (while running):
  Normal blink  → left click
  Double blink  → right click
  Long blink    → pause / resume

Optional .env settings:
  EYE_SHOW_PREVIEW=1         ← shows debug webcam window
  EYE_CURSOR_SPEED=1.5       ← faster cursor (default 1.0)
  EYE_DWELL_CLICK=1          ← look at spot for 2s = click
  EYE_DWELL_SECONDS=2.0
  EYE_SMOOTHING=0.08         ← lower = smoother but laggier
"""
