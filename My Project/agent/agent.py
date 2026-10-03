import os
import sys
import io
import time
import base64
import threading
import random
import json
import ctypes
import socket as _socket
from openai import OpenAI
from dotenv import load_dotenv
from PIL import ImageGrab

# Resolve the folder where the exe (or script) lives
if getattr(sys, 'frozen', False):
    _BASE_DIR = os.path.dirname(sys.executable)
else:
    _BASE_DIR = os.path.dirname(os.path.abspath(__file__))

load_dotenv(os.path.join(_BASE_DIR, '.env'))

# Log file
LOG_FILE = os.path.join(_BASE_DIR, "agent.log")
_log_lock = threading.Lock()

def _write_log(level, msg):
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{level}] [{timestamp}] {msg}\n"
    with _log_lock:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line)

# ANSI Color Codes
COLOR_HEADER  = "\033[95m"
COLOR_INFO    = "\033[94m"
COLOR_SUCCESS = "\033[92m"
COLOR_WARN    = "\033[93m"
COLOR_FAIL    = "\033[91m"
COLOR_BOLD    = "\033[1m"
COLOR_RESET   = "\033[0m"

# ── AI Provider Configuration ──────────────────────────────────────────────────
AI_PROVIDER = os.getenv("AI_PROVIDER", "openai").strip().lower()

if AI_PROVIDER == "gemini":
    API_KEY      = os.getenv("GEMINI_API_KEY", "").strip()
    OPENAI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.6-flash").strip()
    API_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"
else:
    AI_PROVIDER  = "openai"
    API_KEY      = os.getenv("OPENAI_API_KEY", "").strip()
    OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini").strip()
    API_BASE_URL = os.getenv("API_BASE_URL", "https://api.openai.com/v1").strip()

# ── Keyboard Trigger Sequences ────────────────────────────────────────────────
KEY_SCREENSHOT   = os.getenv("KEY_SCREENSHOT",  "qwe").strip()
KEY_START_INPUT  = os.getenv("KEY_START_INPUT", "asd").strip()
KEY_SOLVE        = os.getenv("KEY_SOLVE",       "jkl").strip()
KEY_DEBUG        = os.getenv("KEY_DEBUG",       "debug").strip()
KEY_CLEAR        = os.getenv("KEY_CLEAR",       "clear").strip()
KEY_PAUSE        = os.getenv("KEY_PAUSE",       "<alt>+<space>").strip()
KEY_RESUME       = os.getenv("KEY_RESUME",      "<ctrl>+<space>").strip()
KEY_KILL_HOTKEY  = os.getenv("KEY_KILL_HOTKEY", "<ctrl>+<alt>+a").strip()

try:
    TYPING_WPM = float(os.getenv("TYPING_WPM", "55").strip() or 55)
except ValueError:
    TYPING_WPM = 55.0

# ── Proxy Port ────────────────────────────────────────────────────────────────
try:
    PROXY_PORT = int(os.getenv("PROXY_PORT", "9920").strip())
except ValueError:
    PROXY_PORT = 9920

try:
    MOUSE_INTERCEPTOR_PORT = int(os.getenv("MOUSE_INTERCEPTOR_PORT", "9921").strip())
except ValueError:
    MOUSE_INTERCEPTOR_PORT = 9921

# ── Windows VK constants (used for modifier tracking) ────────────────────────
_VK_LSHIFT   = 0xA0; _VK_RSHIFT   = 0xA1
_VK_LCONTROL = 0xA2; _VK_RCONTROL = 0xA3
_VK_LMENU    = 0xA4; _VK_RMENU    = 0xA5   # Alt
_VK_SHIFT    = 0x10; _VK_CONTROL  = 0x11; _VK_MENU = 0x12
_VK_SPACE    = 0x20; _VK_RETURN   = 0x0D
_VK_BACK     = 0x08; _VK_TAB      = 0x09

# Scan codes for special keys (US layout)
_SC_ENTER     = 28
_SC_BACKSPACE = 14
_SC_TAB       = 15
_SC_SPACE     = 57
_SC_LSHIFT    = 42

_MODIFIER_VK_MAP = {
    _VK_LSHIFT:   "shift", _VK_RSHIFT:   "shift",
    _VK_LCONTROL: "ctrl",  _VK_RCONTROL: "ctrl",
    _VK_LMENU:    "alt",   _VK_RMENU:    "alt",
    _VK_SHIFT:    "shift", _VK_CONTROL:  "ctrl",  _VK_MENU:    "alt",
}

def _parse_hotkey(hk_str: str):
    """Parse a hotkey string like '<ctrl>+<alt>+a' → (frozenset_of_mods, key_str).
    Returns None if the string is empty or unparseable."""
    if not hk_str:
        return None
    parts = hk_str.lower().replace("><", ">+<").split("+")
    mods = set()
    key  = None
    for p in parts:
        p = p.strip().strip("<>")
        if p in ("ctrl", "control"): mods.add("ctrl")
        elif p in ("alt", "menu"):   mods.add("alt")
        elif p == "shift":           mods.add("shift")
        elif p in ("space", "spacebar"): key = "space"
        elif p == "enter":           key = "enter"
        elif len(p) == 1:            key = p
        else:                        key = p   # e.g. "f1"
    return (frozenset(mods), key) if key else None

def _matches_hotkey(vk: int, char: str, held_mods: frozenset, hotkey) -> bool:
    """Return True if the current key + modifiers satisfy *hotkey* (from _parse_hotkey)."""
    if hotkey is None:
        return False
    mods, key = hotkey
    if mods and not mods.issubset(held_mods):
        return False
    if key == "space":  return vk == _VK_SPACE
    if key == "enter":  return vk == _VK_RETURN
    if len(key) == 1:   return char.lower() == key or vk == ord(key.upper())
    return False

_PAUSE_HOTKEY  = _parse_hotkey(KEY_PAUSE)
_RESUME_HOTKEY = _parse_hotkey(KEY_RESUME)
_KILL_HOTKEY   = _parse_hotkey(KEY_KILL_HOTKEY)

# ── State Variables ────────────────────────────────────────────────────────────
is_processing     = False
automation_running = False
is_paused         = False
is_aborted        = False
processing_lock   = threading.Lock()
key_buffer        = []
is_recording_text = False
captured_text     = ""
screenshot_queue  = []

class AbortAutomationException(Exception):
    pass

# ── Logging ────────────────────────────────────────────────────────────────────
def log_info(msg):
    ts = time.strftime("%H:%M:%S")
    print(f"{COLOR_INFO}[*] [{ts}] {msg}{COLOR_RESET}")
    _write_log("INFO", msg)

def log_success(msg):
    ts = time.strftime("%H:%M:%S")
    print(f"{COLOR_SUCCESS}[+] [{ts}] {msg}{COLOR_RESET}")
    _write_log("SUCCESS", msg)

def log_warning(msg):
    ts = time.strftime("%H:%M:%S")
    print(f"{COLOR_WARN}[!] [{ts}] {msg}{COLOR_RESET}")
    _write_log("WARNING", msg)

def log_error(msg):
    ts = time.strftime("%H:%M:%S")
    print(f"{COLOR_FAIL}[ERROR] [{ts}] {msg}{COLOR_RESET}")
    _write_log("ERROR", msg)

# ═══════════════════════════════════════════════════════════════════════════════
#  ProxyClient — TCP connection to the C++ Interception proxy
# ═══════════════════════════════════════════════════════════════════════════════

class ProxyClient:
    """
    Talks to proxy.exe over a local TCP socket.

    Physical key events arrive as lines:  KEY\\t<scan>\\t<vk>\\t<state>\\t<char>
    Synthetic events are sent as lines:
        KEY\\t<scan>\\t<state>              ← keystroke injection
        MOUSE_MOVE\\t<x>\\t<y>             ← absolute pixel move
        MOUSE_CLICK\\t<L|R|M>\\t<0|1>      ← button press/release
    """

    def __init__(self, host: str = "127.0.0.1", port: int = PROXY_PORT):
        self._host       = host
        self._port       = port
        self._sock       = None
        self._send_lock  = threading.Lock()
        self._key_cb     = None    # callable(vk, char, state, held_mods)
        self._user32     = ctypes.windll.user32

    def set_key_callback(self, cb):
        self._key_cb = cb

    # ── Connection ─────────────────────────────────────────────────────────────
    def connect(self):
        """Block until connected to the proxy, then start reader thread."""
        while True:
            try:
                s = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
                s.connect((self._host, self._port))
                self._sock = s
                log_success(f"Connected to Interception proxy on {self._host}:{self._port}.")
                t = threading.Thread(target=self._reader_loop, daemon=True)
                t.start()
                return
            except Exception:
                log_warning(
                    f"proxy.exe not reachable on port {self._port}. "
                    "Start proxy.exe first. Retrying in 2 s..."
                )
                time.sleep(2)

    # ── Internal send ──────────────────────────────────────────────────────────
    def _send_line(self, line: str):
        with self._send_lock:
            if self._sock is None:
                return
            try:
                self._sock.sendall((line + "\n").encode("utf-8"))
            except Exception:
                self._sock = None

    # ── Reader loop (runs in background thread) ───────────────────────────────
    def _reader_loop(self):
        held_mods: set = set()
        buf = b""

        while True:
            try:
                chunk = self._sock.recv(4096)
                if not chunk:
                    break
            except Exception:
                break

            buf += chunk

            while b"\n" in buf:
                raw_line, buf = buf.split(b"\n", 1)
                line = raw_line.decode("utf-8", errors="replace").rstrip("\r")
                if not line:
                    continue
                parts = line.split("\t")
                if len(parts) < 4 or parts[0] != "KEY":
                    continue

                try:
                    # scan = int(parts[1])  # not used by Python logic
                    vk    = int(parts[2])
                    state = int(parts[3])
                    char  = parts[4] if len(parts) > 4 else ""
                except (ValueError, IndexError):
                    continue

                is_down = (state & 0x01) == 0   # bit 0 clear → DOWN

                # Update modifier state
                mod_name = _MODIFIER_VK_MAP.get(vk)
                if mod_name:
                    if is_down:
                        held_mods.add(mod_name)
                    else:
                        held_mods.discard(mod_name)

                if self._key_cb:
                    self._key_cb(vk, char, state, frozenset(held_mods))

        # Disconnected — attempt reconnect
        log_warning("Proxy connection lost. Reconnecting...")
        self._sock = None
        self.connect()

    # ── Synthetic output helpers ──────────────────────────────────────────────
    def send_key(self, scancode: int, state: int):
        """Inject a hardware keystroke. state: 0=down, 1=up, 2=down+E0, 3=up+E0."""
        self._send_line(f"KEY\t{scancode}\t{state}")

    def type_char(self, ch: str):
        """Type a single character through the proxy using Win32 scan codes."""
        # Control / whitespace characters
        if ch == "\n":
            self.send_key(_SC_ENTER,     0); self.send_key(_SC_ENTER,     1); return
        if ch == "\t":
            self.send_key(_SC_TAB,       0); self.send_key(_SC_TAB,       1); return
        if ch == "\b":
            self.send_key(_SC_BACKSPACE, 0); self.send_key(_SC_BACKSPACE, 1); return
        if ch == " ":
            self.send_key(_SC_SPACE,     0); self.send_key(_SC_SPACE,     1); return

        # Printable characters: look up VK and scan code
        vk_full = self._user32.VkKeyScanW(ord(ch))
        if vk_full in (-1, 0xFFFF):
            return   # character not typeable on current layout
        vk        = vk_full & 0xFF
        shift_req = bool((vk_full >> 8) & 0x01)
        scan      = self._user32.MapVirtualKeyW(vk, 0)   # MAPVK_VK_TO_VSC
        if scan == 0:
            return

        if shift_req:
            self.send_key(_SC_LSHIFT, 0)      # Left Shift down
        self.send_key(scan, 0)
        self.send_key(scan, 1)
        if shift_req:
            self.send_key(_SC_LSHIFT, 1)      # Left Shift up

# Singleton proxy client (initialised in __main__)
proxy: ProxyClient = None

# ── Helper ─────────────────────────────────────────────────────────────────────

def clean_code_text(text: str) -> str:
    """Strip markdown fences from AI response."""
    text = text.strip()
    if text.startswith("```"):
        first_nl = text.find("\n")
        text = text[first_nl + 1:] if first_nl != -1 else text[3:]
        if text.endswith("```"):
            text = text[:-3]
    return text.strip()

def check_pause_and_handle():
    """Sleep while paused; raise AbortAutomationException if aborted."""
    global is_paused, automation_running, is_aborted
    if not automation_running:
        return
    if is_paused:
        log_warning(
            f"Automation PAUSED. Press '{KEY_RESUME}' to resume "
            f"or type '{KEY_CLEAR}' to abort..."
        )
        while is_paused:
            time.sleep(0.05)
            if is_aborted:
                raise AbortAutomationException("Automation aborted by user.")
        if not is_aborted:
            log_success("Automation RESUMED.")

def pause_automation():
    global is_paused
    if automation_running and not is_paused:
        is_paused = True
        log_warning(f"PAUSE requested ({KEY_PAUSE}). You have manual control.")

def resume_automation():
    global is_paused
    if automation_running and is_paused:
        is_paused = False
        log_success(f"RESUME requested ({KEY_RESUME}).")

# ── Mouse & Keyboard Automation ────────────────────────────────────────────────

def type_humanlike(text: str):
    """Types text through the proxy at ~TYPING_WPM words per minute."""
    log_info("Starting typing automation in 2 seconds... Focus your cursor now!")
    time.sleep(2.0)
    log_info(
        f"Typing answer (~{int(TYPING_WPM)} WPM)... "
        f"Press '{KEY_PAUSE}' to pause, '{KEY_RESUME}' to resume."
    )

    wpm_scale = 55.0 / max(10.0, TYPING_WPM)

    i = 0
    length = len(text)
    while i < length:
        check_pause_and_handle()

        ch = text[i]
        if ch.isalnum() or ch == "_":
            # Collect a full word and type it
            word = ""
            while i < length and (text[i].isalnum() or text[i] == "_"):
                word += text[i]
                i += 1
            for w_char in word:
                check_pause_and_handle()
                proxy.type_char(w_char)
                time.sleep(random.uniform(0.06, 0.12) * wpm_scale)
        else:
            if ch == "\n":
                proxy.type_char("\n")
                time.sleep(random.uniform(0.6, 1.2) * wpm_scale)
            elif ch in (" ", "\t"):
                proxy.type_char(ch)
                time.sleep(random.uniform(0.18, 0.35) * wpm_scale)
            else:
                proxy.type_char(ch)
                time.sleep(random.uniform(0.12, 0.25) * wpm_scale)
            i += 1

    log_success("Typing completed.")

# ── Capture & Solve ────────────────────────────────────────────────────────────

def solve_text_task(text_input: str, screenshots=None):
    """Sends context to the AI and runs the appropriate automation."""
    global is_processing, automation_running, is_paused

    if not text_input.strip() and not screenshots:
        log_warning(
            f"No input captured. Type '{KEY_SCREENSHOT}' for screenshot, "
            f"'{KEY_START_INPUT}' + '{KEY_SOLVE}' for text input."
        )
        return

    if not API_KEY:
        label = "GEMINI_API_KEY" if AI_PROVIDER == "gemini" else "OPENAI_API_KEY"
        log_error(f"API key missing. Set {label} in the .env file.")
        return

    try:
        log_info(f"Sending to {AI_PROVIDER.upper()} ({OPENAI_MODEL})...")
        client = OpenAI(api_key=API_KEY, base_url=API_BASE_URL)

        system_prompt = (
            "You are an expert AI assistant. Analyse the provided text and respond ONLY with a JSON object. "
            "JSON Format:\n"
            "{\n"
            "  \"type\": \"mcq\" | \"code\" | \"sql\" | \"explain\",\n"
            "  \"answer\": \"A\" | \"B\" | \"C\" | \"D\" | \"code_block_text\" | \"\",\n"
            "  \"explanation\": \"explanation text\"\n"
            "}\n"
            "Rules:\n"
            "- If the text contains an MCQ question: 'type' = 'mcq', 'answer' = the single correct option letter (A, B, C, or D), 'explanation' = brief reason.\n"
            "- If the text contains a coding problem: 'type' = 'code', 'answer' = the exact solution code IN C++ (do NOT wrap in markdown ``` inside the JSON string. Do NOT use newline characters (\\n) in the code), 'explanation' = brief reason.\n"
            "- If the text contains a SQL problem: 'type' = 'sql', 'answer' = the exact SQL solution code, 'explanation' = brief reason.\n"
            "- If the text does NOT contain any MCQ or coding/SQL question: "
            "'type' = 'explain', 'answer' = '', 'explanation' = a clear, detailed explanation."
        )

        content = []
        if text_input.strip():
            content.append({
                "type": "text",
                "text": f"Solve the following problem and return the solution in the specified JSON format:\n\n{text_input}"
            })
        else:
            content.append({
                "type": "text",
                "text": "Solve the problem in the provided images and return the solution in the specified JSON format."
            })

        if screenshots:
            for img_b64 in screenshots:
                content.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"}
                })

        response = client.chat.completions.create(
            model=OPENAI_MODEL,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user",   "content": content},
            ]
        )

        result  = json.loads(response.choices[0].message.content)
        q_type  = result.get("type", "").lower()
        ans     = result.get("answer", "")
        explanation = result.get("explanation", "")

        print(f"\n{COLOR_HEADER}{'='*30} SOLUTION {'='*30}{COLOR_RESET}")
        print(f"{COLOR_BOLD}Question Type:{COLOR_RESET} {q_type}")
        print(f"{COLOR_BOLD}Answer:{COLOR_RESET}\n{ans}\n")
        print(f"{COLOR_BOLD}Explanation:{COLOR_RESET} {explanation}")
        print(f"{COLOR_HEADER}{'='*70}{COLOR_RESET}\n")

        automation_running = True
        is_paused = False

        if q_type == "mcq":
            execute_mcq_automation(ans)
        elif q_type in ("code", "sql"):
            execute_code_automation(clean_code_text(ans))
        elif q_type == "explain":
            execute_code_automation(explanation)
        else:
            log_warning(f"Unsupported question type '{q_type}'. No automation ran.")

    except AbortAutomationException as e:
        log_warning(str(e))
        key_buffer.clear()
    except Exception as e:
        log_error(f"Error solving: {e}")
    finally:
        global is_aborted
        automation_running = False
        is_paused  = False
        is_aborted = False


def execute_mcq_automation(option: str):
    """Send the selected MCQ option to the dedicated mouse interceptor."""
    option = option.strip().upper()
    if option not in ("A", "B", "C", "D"):
        log_error(f"Invalid MCQ option: '{option}'. Expected A/B/C/D.")
        return

    labels = {"A": "Top-Left", "B": "Top-Right", "C": "Bottom-Right", "D": "Bottom-Left"}
    label = labels[option]
    print(f"\n{COLOR_SUCCESS}{COLOR_BOLD}>>> SELECTED OPTION: {option} ({label}) <<<{COLOR_RESET}\n")
    log_info(f"Sending option {option} to the mouse interceptor on port {MOUSE_INTERCEPTOR_PORT}...")
    message = f"OPTION\t{option}\n".encode("ascii")
    try:
        with _socket.create_connection(("127.0.0.1", MOUSE_INTERCEPTOR_PORT), timeout=2.0) as mouse_sock:
            mouse_sock.sendall(message)
            response = mouse_sock.recv(16).decode("ascii", errors="replace").strip()
        if response != "OK":
            log_error(f"Mouse interceptor rejected option {option}.")
            return
        log_success(f"Mouse interceptor received option {option} ({label}).")
    except OSError as exc:
        log_error(
            f"Could not reach mouse interceptor on port {MOUSE_INTERCEPTOR_PORT}: {exc}. "
            "Start MouseCornerInterceptor.exe first."
        )


def execute_code_automation(code_text: str):
    """Auto-type the solution through the proxy into the focused window."""
    type_humanlike(code_text)


def start_solving_thread(text_input: str, screenshots=None):
    global is_processing
    with processing_lock:
        if is_processing:
            log_warning("A solving task is already in progress.")
            return
        is_processing = True

    thread = threading.Thread(target=solve_text_task, args=(text_input, screenshots), daemon=True)
    thread.start()

    def release():
        thread.join()
        global is_processing
        is_processing = False
    threading.Thread(target=release, daemon=True).start()

# ═══════════════════════════════════════════════════════════════════════════════
#  Key Event Handler  (called by ProxyClient._reader_loop for every physical key)
# ═══════════════════════════════════════════════════════════════════════════════

def on_key_event(vk: int, char: str, state: int, held_mods: frozenset):
    """
    Processes every physical keystroke forwarded from the proxy.

    - vk        : Windows virtual key code
    - char      : UTF-8 character (empty for non-printable keys)
    - state     : Interception state flags (0=down, 1=up, 2=down+E0, 3=up+E0)
    - held_mods : frozenset of currently held modifier names ('ctrl','alt','shift')
    """
    global key_buffer, is_paused, automation_running, is_aborted
    global is_recording_text, captured_text, screenshot_queue

    is_down = (state & 0x01) == 0   # bit 0 clear = key-down event

    # ── Hotkey checks (key-down only) ────────────────────────────────────────
    if is_down:
        if _KILL_HOTKEY and _matches_hotkey(vk, char, held_mods, _KILL_HOTKEY):
            _write_log("INFO", "Kill hotkey pressed. Exiting.")
            print(f"\n{COLOR_FAIL}{COLOR_BOLD}[!] Kill hotkey pressed. Shutting down...{COLOR_RESET}")
            os._exit(0)

        if automation_running:
            if _PAUSE_HOTKEY  and _matches_hotkey(vk, char, held_mods, _PAUSE_HOTKEY):
                pause_automation()
                return
            if _RESUME_HOTKEY and _matches_hotkey(vk, char, held_mods, _RESUME_HOTKEY):
                resume_automation()
                return

    # ── Ignore modifier-only keys and key-up events for sequence logic ────────
    # (we only need key-down for trigger detection / text capture)
    if not is_down:
        return
    if vk in _MODIFIER_VK_MAP:
        return

    # ── Map special VKs to character equivalents ──────────────────────────────
    if   vk == _VK_SPACE:  char = " "
    elif vk == _VK_RETURN: char = "\n"
    elif vk == _VK_BACK:   char = "\b"
    elif vk == _VK_TAB:    char = "\t"

    if not char:
        return   # non-printable, non-special → skip

    # ═════════════════════════════════════════════════════════════════════════
    #  When automation is running, only watch for the abort sequence (clear)
    # ═════════════════════════════════════════════════════════════════════════
    if automation_running:
        key_buffer.append(char.lower())
        if len(key_buffer) > len(KEY_CLEAR) + 1:
            key_buffer.pop(0)
        if "".join(key_buffer).endswith(KEY_CLEAR):
            is_aborted = True
            is_paused  = False
            key_buffer.clear()
            is_recording_text = False
            captured_text = ""
            screenshot_queue.clear()
            log_warning(f"ABORT requested ('{KEY_CLEAR}'). Stopping automation.")
        return

    # ═════════════════════════════════════════════════════════════════════════
    #  Normal (idle) mode — not recording text
    # ═════════════════════════════════════════════════════════════════════════
    if not is_recording_text:
        key_buffer.append(char.lower())
        max_buf = max(
            len(KEY_SCREENSHOT), len(KEY_START_INPUT),
            len(KEY_SOLVE), len(KEY_DEBUG), len(KEY_CLEAR)
        ) + 1
        if len(key_buffer) > max_buf:
            key_buffer.pop(0)

        seq = "".join(key_buffer)

        if seq.endswith(KEY_CLEAR):
            key_buffer.clear()
            captured_text = ""
            screenshot_queue.clear()
            log_success("All buffers cleared.")

        elif seq.endswith(KEY_START_INPUT):
            key_buffer.clear()
            is_recording_text = True
            captured_text = ""
            log_success(
                f"Text input mode ON. Type your query, then type '{KEY_SOLVE}' to solve."
            )

        elif seq.endswith(KEY_SCREENSHOT):
            key_buffer.clear()
            if len(screenshot_queue) < 7:
                img = ImageGrab.grab()
                buf = io.BytesIO()
                img.save(buf, format="JPEG")
                screenshot_queue.append(base64.b64encode(buf.getvalue()).decode("utf-8"))
                log_success(f"Screenshot queued. Total: {len(screenshot_queue)}/7")
            else:
                log_warning("Maximum 7 screenshots reached.")

        elif seq.endswith(KEY_SOLVE):
            key_buffer.clear()
            shots = list(screenshot_queue)
            screenshot_queue.clear()
            log_success(f"Solving... Screenshots: {len(shots)}")
            start_solving_thread("", shots)

        elif seq.endswith(KEY_DEBUG):
            key_buffer.clear()
            shots = list(screenshot_queue)
            screenshot_queue.clear()
            debug_prompt = (
                "Please analyse and explain the provided screenshot(s), "
                "highlighting any bugs, errors, or important information. "
                "Ensure your explanation is strictly between 10 to 20 words."
            )
            log_success(f"Debug request. Screenshots: {len(shots)}")
            start_solving_thread(debug_prompt, shots)

    # ═════════════════════════════════════════════════════════════════════════
    #  Text recording mode — accumulate chars until solve/debug/clear trigger
    # ═════════════════════════════════════════════════════════════════════════
    else:
        # Handle editing keys
        if char == "\b":
            captured_text = captured_text[:-1]
        elif char not in ("\n",):         # ignore bare Enter during recording
            captured_text += char

        # Buffer for trigger detection (works on lowercased copy)
        key_buffer.append(char.lower())
        max_buf = max(
            len(KEY_SCREENSHOT), len(KEY_SOLVE),
            len(KEY_DEBUG), len(KEY_CLEAR)
        ) + 1
        if len(key_buffer) > max_buf:
            key_buffer.pop(0)

        seq = "".join(key_buffer)

        if seq.endswith(KEY_CLEAR):
            key_buffer.clear()
            is_recording_text = False
            captured_text = ""
            screenshot_queue.clear()
            log_success("All buffers cleared.")

        elif seq.endswith(KEY_SOLVE):
            key_buffer.clear()
            is_recording_text = False
            final_text = captured_text[: -len(KEY_SOLVE)].strip()
            shots = list(screenshot_queue)
            screenshot_queue.clear()
            log_success(f"Solving...\nQuery: {final_text}\nScreenshots: {len(shots)}")
            start_solving_thread(final_text, shots)

        elif seq.endswith(KEY_DEBUG):
            key_buffer.clear()
            is_recording_text = False
            final_text = captured_text[: -len(KEY_DEBUG)].strip()
            shots = list(screenshot_queue)
            screenshot_queue.clear()
            debug_prompt = (
                final_text + "\n\n"
                "Please analyse and explain the provided screenshot(s), "
                "highlighting any bugs, errors, or important information. "
                "Ensure your explanation is strictly between 10 to 20 words."
            )
            log_success(f"Debug...\nQuery: {final_text}\nScreenshots: {len(shots)}")
            start_solving_thread(debug_prompt.strip(), shots)

        elif seq.endswith(KEY_SCREENSHOT):
            # Screenshot can still be taken mid-recording
            key_buffer.clear()
            captured_text = captured_text[: -len(KEY_SCREENSHOT)]
            if len(screenshot_queue) < 7:
                img = ImageGrab.grab()
                buf = io.BytesIO()
                img.save(buf, format="JPEG")
                screenshot_queue.append(base64.b64encode(buf.getvalue()).decode("utf-8"))
                log_success(f"Screenshot queued. Total: {len(screenshot_queue)}/7")
            else:
                log_warning("Maximum 7 screenshots reached.")


# ═══════════════════════════════════════════════════════════════════════════════
#  Entry Point
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    _write_log(
        "INFO",
        f"=== AGENT STARTED === Provider: {AI_PROVIDER.upper()} | "
        f"Model: {OPENAI_MODEL} | Log: {LOG_FILE}"
    )

    print(f"{COLOR_HEADER}{'='*60}")
    print(f"        {COLOR_BOLD}BACKGROUND SOLVER AGENT v2.0  (Proxy Mode){COLOR_RESET}")
    print(f"{COLOR_HEADER}{'='*60}{COLOR_RESET}")
    log_info(f"AI Provider : {COLOR_BOLD}{AI_PROVIDER.upper()}{COLOR_RESET}")
    log_info(f"Model       : {COLOR_BOLD}{OPENAI_MODEL}{COLOR_RESET}")
    log_info(f"Triggers    : {COLOR_BOLD}{KEY_SCREENSHOT}{COLOR_RESET}=screenshot  "
             f"{COLOR_BOLD}{KEY_START_INPUT}{COLOR_RESET}=text-input  "
             f"{COLOR_BOLD}{KEY_SOLVE}{COLOR_RESET}=solve  "
             f"{COLOR_BOLD}{KEY_DEBUG}{COLOR_RESET}=debug  "
             f"{COLOR_BOLD}{KEY_CLEAR}{COLOR_RESET}=clear")
    log_info(f"Pause/Resume: {COLOR_BOLD}{KEY_PAUSE}{COLOR_RESET} / "
             f"{COLOR_BOLD}{KEY_RESUME}{COLOR_RESET}")
    log_info(f"Kill        : {COLOR_BOLD}{KEY_KILL_HOTKEY}{COLOR_RESET}")
    log_info(f"Proxy port  : {COLOR_BOLD}{PROXY_PORT}{COLOR_RESET}")
    print(f"{COLOR_HEADER}{'-'*60}{COLOR_RESET}")

    # ── Connect to proxy ────────────────────────────────────────────────────
    proxy = ProxyClient(port=PROXY_PORT)
    proxy.set_key_callback(on_key_event)
    proxy.connect()   # blocks until proxy.exe is reachable

    log_info("Listening for key events from proxy. Ready.")

    # ── Keep main thread alive ────────────────────────────────────────────────
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print(f"\n{COLOR_WARN}[!] Interrupted. Exiting.{COLOR_RESET}")
        _write_log("INFO", "=== AGENT STOPPED (KeyboardInterrupt) ===")
