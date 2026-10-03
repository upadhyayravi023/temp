<div align="center">

# KeyFlow

**Driver-level keyboard & mouse automation with an AI brain.**  
Type a few keys anywhere on screen — get a solution typed back or the mouse moved to the right MCQ corner.

[![Platform](https://img.shields.io/badge/platform-Windows%2010%2F11-0078D4?style=flat-square&logo=windows)](https://www.microsoft.com/windows)
[![Python](https://img.shields.io/badge/python-3.9%2B-3776AB?style=flat-square&logo=python&logoColor=white)](https://python.org)
[![License](https://img.shields.io/badge/license-MIT-green?style=flat-square)](LICENSE)
[![AI](https://img.shields.io/badge/AI-OpenAI%20%7C%20Gemini-412991?style=flat-square&logo=openai&logoColor=white)](https://openai.com)
[![Project](https://img.shields.io/badge/project-KeyFlow-6C63FF?style=flat-square)](.)

</div>

---

## Overview

KeyFlow intercepts every physical keystroke and mouse event at the **kernel driver level** (via the [Interception](https://github.com/oblitum/Interception) library), passes them through a lightweight C++ proxy, and feeds them to a Python AI agent. The agent watches for trigger sequences, captures screenshots or typed text, sends the context to an AI model, and then either:

- **Types the answer** back into your active window at a realistic WPM, or
- **Moves the cursor** to the screen corner that corresponds to the correct MCQ option.

Everything runs locally on a single Windows machine. No external servers. No UI.

---

## Architecture

```
Physical Keyboard / Mouse
         │
         ▼ (kernel filter driver)
 ┌───────────────────┐
 │  Interception     │
 │  Driver           │
 └────────┬──────────┘
          │ intercept_send / intercept_receive
          ▼
 ┌───────────────────┐   KEY events (TCP :9920)   ┌─────────────────────┐
 │   proxy.exe       │ ─────────────────────────► │                     │
 │   (C++, Listener) │                             │   agent.py          │
 │                   │ ◄─────────────────────────  │   (Python, OpenAI   │
 └───────────────────┘   KEY injections            │    or Gemini)       │
                                                   └──────────┬──────────┘
                                                              │ OPTION A/B/C/D
                                                              │ (TCP :9921)
                                                              ▼
                                                   ┌─────────────────────┐
                                                   │  MouseCornerInter-  │
                                                   │  ceptor.exe  (C++)  │
                                                   │  moves cursor to    │
                                                   │  screen corner      │
                                                   └─────────────────────┘
```

All IPC is **loopback TCP** — zero network exposure.

---

## Repository Structure

```
KeyFlow/                                     # Repository root
│
├── My Project/                          # Main project root
│   ├── launcher.py                      # One-click orchestrator (source)
│   ├── KeyFlow.exe                      # Compiled launcher — run this
│   ├── KeyFlow.spec                     # PyInstaller build spec
│   │
│   ├── Listener/                        # Keyboard proxy (C++)
│   │   ├── proxy.cpp                    # Source
│   │   ├── proxy.exe                    # Pre-built binary
│   │   ├── build.bat                    # Build script (MSVC or MinGW)
│   │   ├── interception.h               # Interception SDK header
│   │   ├── interception.dll             # Interception runtime
│   │   ├── interception.lib             # Interception import library
│   │   └── Interception/               # Driver installer
│   │       └── command line installer/
│   │           └── install-interception.exe
│   │
│   ├── MouseInterceptor/                # Mouse corner injector (C++)
│   │   ├── MouseCornerInjector.cpp      # Source
│   │   ├── MouseCornerInterceptor.kiosk.exe  # Pre-built (used by launcher)
│   │   ├── build_mouse_interceptor.bat  # Build script
│   │   └── interception.dll
│   │
│   └── agent/                           # AI agent (Python)
│       ├── agent.py                     # Main agent
│       ├── agent.exe                    # Pre-built standalone executable
│       ├── .env.example                 # Configuration template
│       ├── requirements.txt             # Python dependencies
│       ├── start.vbs                    # Silent background launcher
│       ├── stop.bat                     # Kill all components
│       └── updater.spec                 # PyInstaller spec for agent
│
├── .gitignore
├── LICENSE
└── README.md
```

---

## Prerequisites

| Requirement | Notes |
|-------------|-------|
| Windows 10 / 11 (64-bit) | Required |
| Administrator privileges | Requested automatically via UAC |
| Interception kernel driver | Installed automatically on first run |
| OpenAI **or** Google Gemini API key | Configured in `agent/.env` |
| Python 3.9+ | Only needed to run from source |

> **Memory Integrity (HVCI)** must be disabled for the Interception driver to load.  
> The launcher detects and disables it automatically — this requires **one reboot**.

---

## Quick Start

### Option A — Pre-built (recommended)

1. Clone or download this repository.
2. Open `My Project/agent/.env.example`, copy it to `My Project/agent/.env`, and add your API key:
   ```env
   AI_PROVIDER=openai
   OPENAI_API_KEY=sk-...
   ```
3. **Double-click `My Project/Launch_All.exe`** and accept the UAC prompt.
4. If this is the first run, follow the on-screen countdown — the launcher handles driver installation and reboots automatically. After each reboot, double-click `Launch_All.exe` again.
5. Once all three components start, you're ready.

### Option B — Run from source

```bash
# Install Python dependencies for the agent
cd "My Project/agent"
pip install -r requirements.txt

# Build proxy.exe (from a Developer Command Prompt or with MinGW in PATH)
cd "../Listener"
build.bat

# Build MouseCornerInterceptor.exe
cd "../MouseInterceptor"
build_mouse_interceptor.bat

# Launch everything
cd ".."
python launcher.py
```

---

## Configuration

Copy `agent/.env.example` → `agent/.env` and fill in your values.

| Variable | Default | Description |
|---|---|---|
| `AI_PROVIDER` | `openai` | Provider: `openai` or `gemini` |
| `OPENAI_API_KEY` | — | Your OpenAI secret key |
| `OPENAI_MODEL` | `gpt-4o-mini` | Model name (e.g. `gpt-4o`) |
| `API_BASE_URL` | `https://api.openai.com/v1` | Override for custom/local endpoints |
| `GEMINI_API_KEY` | — | Your Gemini API key |
| `GEMINI_MODEL` | `gemini-3.6-flash` | Gemini model (e.g. `gemini-1.5-pro`) |
| `KEY_SCREENSHOT` | `qwe` | Capture a screenshot |
| `KEY_START_INPUT` | `asd` | Start recording typed text |
| `KEY_SOLVE` | `jkl` | Submit to AI and execute solution |
| `KEY_DEBUG` | `debug` | Short debug/explain summary |
| `KEY_CLEAR` | `clear` | Clear all buffers |
| `KEY_PAUSE` | `<alt>+<space>` | Pause auto-typing |
| `KEY_RESUME` | `<ctrl>+<space>` | Resume auto-typing |
| `KEY_KILL_HOTKEY` | `<ctrl>+<alt>+a` | Kill the agent immediately |
| `TYPING_WPM` | `55` | Auto-typing speed (words per minute) |
| `PROXY_PORT` | `9920` | TCP port for `proxy.exe` |
| `MOUSE_INTERCEPTOR_PORT` | `9921` | TCP port for the mouse interceptor |

---

## Usage

All triggers are typed **anywhere on screen** — no window focus or special mode needed.

### Capturing Context

| Type this | What happens |
|-----------|-------------|
| `qwe` | Screenshot captured and added to queue (up to 7) |
| `asd` | Enters text-input mode — type your question, press **Enter** to finish |

You can mix both: type `asd`, start your question, type `qwe` mid-sentence to add a screenshot, then keep typing.

### Submitting

| Type this | What happens |
|-----------|-------------|
| `jkl` | Sends all queued screenshots + text to the AI, then executes the solution |
| `debug` | Sends context for a concise 10–20 word explanation instead of a full solution |
| `clear` | Wipes all queued screenshots and text without solving |

### Solution Execution

| Question type | How the answer is delivered |
|---------------|----------------------------|
| **MCQ (A / B / C / D)** | Mouse cursor moves to the corresponding screen corner |
| **Coding / SQL** | Answer is auto-typed into your active editor at realistic speed |
| **Text / Other** | Answer is typed into the active window |

### MCQ Corner Mapping

```
┌────────────────────────────────────┐
│  A — Top-Left      B — Top-Right   │
│                                    │
│  D — Bottom-Left   C — Bottom-Right│
└────────────────────────────────────┘
```

### Active Controls (during auto-typing)

| Key | Action |
|-----|--------|
| `<alt>+<space>` | Pause typing |
| `<ctrl>+<space>` | Resume typing |
| `<ctrl>+<alt>+a` | Kill the agent |

---

## How the Launcher Works

`Launch_All.exe` is a self-contained orchestrator that walks through a multi-stage setup on first run:

```
Run Launch_All.exe
       │
       ├─ Already set up? ──────────────────────► Launch all 3 components
       │
       ├─ HVCI (Memory Integrity) is ON?
       │       └─ Disable via registry → reboot (1 of up to 2)
       │
       ├─ Vulnerable Driver Blocklist is ON?
       │       └─ Disable via registry → reboot
       │
       └─ Interception driver not installed?
               └─ Run install-interception.exe → reboot → done
```

Flag files (`.pending_reboot`, `.install_driver_after_reboot`, `.blocklist_was_disabled`) are written to the project folder so the launcher knows which stage to resume after each reboot.

---

## Building from Source

### Keyboard Proxy (`proxy.exe`)

Requires MSVC (`cl.exe`) **or** MinGW (`g++`) with `interception.lib` in the `Listener/` folder.

```bat
cd "My Project\Listener"
build.bat
```

### Mouse Corner Interceptor

```bat
cd "My Project\MouseInterceptor"
build_mouse_interceptor.bat
```

### AI Agent (`agent.exe`)

```bat
cd "My Project\agent"
pip install pyinstaller -r requirements.txt
pyinstaller updater.spec
```

### Launcher (`Launch_All.exe`)

```bat
cd "My Project"
pip install pyinstaller
pyinstaller KeyFlow.spec
```

---

## Troubleshooting

<details>
<summary><strong>Driver installation fails</strong></summary>

1. Open **Windows Security → App & Browser Control → Exploit protection settings** and turn off **"Block vulnerable drivers"**.
2. Or run in an elevated prompt: `bcdedit /set testsigning on` then reboot.
3. Re-run `Launch_All.exe`.

</details>

<details>
<summary><strong>Memory Integrity keeps turning back on</strong></summary>

Your system or organization's policy may enforce HVCI. Go to:  
**Windows Security → Device Security → Core Isolation Details → Memory Integrity → OFF**  
Disable it, reboot, and re-run the launcher.

</details>

<details>
<summary><strong>Port already in use (9920 or 9921)</strong></summary>

Set `PROXY_PORT` and/or `MOUSE_INTERCEPTOR_PORT` in `agent/.env` to any free port numbers, then rebuild/rerun.

</details>

<details>
<summary><strong>Auto-typing adds extra brackets or indentation</strong></summary>

Editors like VS Code and PyCharm auto-insert closing brackets and auto-indent. Temporarily disable:
- **Auto Closing Brackets**
- **Auto Closing Quotes**
- **Auto Indentation**

Or use `<alt>+<space>` / `<ctrl>+<space>` to pause and manually fix, then resume.

</details>

<details>
<summary><strong>Agent doesn't respond to trigger sequences</strong></summary>

- Confirm all three processes are running in their console windows.
- Check `agent/agent.log` for error messages.
- Verify your API key in `agent/.env` is valid and has available quota.
- Make sure the Interception driver is installed: look for `keyboard_upper_filter.sys` or `mouse_upper_filter.sys` in `C:\Windows\System32\drivers\`.

</details>

---

## License

This project is licensed under the [MIT License](LICENSE).
