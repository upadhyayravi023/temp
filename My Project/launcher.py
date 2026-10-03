import os, sys, subprocess, ctypes, time, winreg, shutil

os.system('')
C_HEADER = '\033[95m'
C_INFO   = '\033[94m'
C_OK     = '\033[92m'
C_WARN   = '\033[93m'
C_FAIL   = '\033[91m'
C_BOLD   = '\033[1m'
C_RESET  = '\033[0m'

# ── Paths ─────────────────────────────────────────────────────────────────────

if getattr(sys, 'frozen', False):
    BASE_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

PROXY_EXE = os.path.join(BASE_DIR, 'Listener', 'proxy.exe')
MOUSE_EXE = os.path.join(BASE_DIR, 'MouseInterceptor', 'MouseCornerInterceptor.kiosk.exe')
AGENT_EXE = os.path.join(BASE_DIR, 'agent', 'agent.exe')
INSTALLER = os.path.join(BASE_DIR, 'Listener', 'Interception', 'command line installer', 'install-interception.exe')

# Flag files (written to BASE_DIR so they survive across reboots)
FLAG_PENDING_REBOOT      = os.path.join(BASE_DIR, '.pending_reboot')        # driver installed, need reboot
FLAG_INSTALL_AFTER_REBOOT = os.path.join(BASE_DIR, '.install_driver_after_reboot')  # HVCI just disabled
FLAG_BLOCKLIST_DISABLED  = os.path.join(BASE_DIR, '.blocklist_was_disabled') # blocklist disabled, need reboot then install

# Registry paths
_HVCI_KEY       = r'SYSTEM\CurrentControlSet\Control\DeviceGuard\Scenarios\HypervisorEnforcedCodeIntegrity'
_BLOCKLIST_KEY  = r'SYSTEM\CurrentControlSet\Control\CI\Config'
_BLOCKLIST_VAL  = 'VulnerableDriverBlocklistEnable'

# ── UI helpers ────────────────────────────────────────────────────────────────

def banner():
    sep = '=' * 58
    print(C_HEADER + sep)
    print('  ' + C_BOLD + 'KeyFlow  --  One-Click Launcher' + C_RESET + C_HEADER)
    print(sep + C_RESET + '\n')

def log(msg, color=C_INFO):
    ts = time.strftime('%H:%M:%S')
    print(color + '[' + ts + '] ' + str(msg) + C_RESET)

def step(n, total, msg):
    print(C_BOLD + '[' + str(n) + '/' + str(total) + '] ' + msg + C_RESET)

def msgbox(title, msg, icon=0x10):
    """Windows MessageBox — works in both console and --noconsole EXEs.
    icon: 0x10=Error  0x30=Warning  0x40=Info"""
    ctypes.windll.user32.MessageBoxW(0, str(msg), str(title), icon)

def do_reboot(reason, delay=10):
    """Print countdown and reboot."""
    print()
    print(C_WARN + C_BOLD + '  ' + reason + C_RESET)
    print(C_WARN + '  System will reboot in ' + str(delay) + ' seconds.')
    print('  After reboot, double-click Launch_All.exe again.' + C_RESET)
    print()
    for i in range(delay, 0, -1):
        print(C_WARN + '  Rebooting in ' + str(i) + '...   ' + C_RESET, end='\r')
        time.sleep(1)
    subprocess.run(['shutdown', '/r', '/t', '0'])
    sys.exit(0)

# ── Admin ─────────────────────────────────────────────────────────────────────

def is_admin():
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False

def elevate_and_exit():
    exe = sys.executable
    params = ' '.join('"' + a + '"' for a in sys.argv)
    ctypes.windll.shell32.ShellExecuteW(None, 'runas', exe, params, None, 1)
    sys.exit(0)

# ── HVCI (Memory Integrity) ───────────────────────────────────────────────────

def is_hvci_enabled():
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, _HVCI_KEY) as k:
            val, _ = winreg.QueryValueEx(k, 'Enabled')
            return bool(val)
    except OSError:
        return False

def disable_hvci():
    """Write HVCI=0 to registry. Effective after reboot."""
    key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, _HVCI_KEY, 0, winreg.KEY_SET_VALUE)
    winreg.SetValueEx(key, 'Enabled', 0, winreg.REG_DWORD, 0)
    winreg.CloseKey(key)

# ── Vulnerable Driver Blocklist ───────────────────────────────────────────────

def get_blocklist():
    """Return current value (0/1) or -1 if key absent."""
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, _BLOCKLIST_KEY) as k:
            val, _ = winreg.QueryValueEx(k, _BLOCKLIST_VAL)
            return int(val)
    except OSError:
        return -1

def set_blocklist(enabled: bool):
    try:
        key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, _BLOCKLIST_KEY, 0, winreg.KEY_SET_VALUE)
        winreg.SetValueEx(key, _BLOCKLIST_VAL, 0, winreg.REG_DWORD, 1 if enabled else 0)
        winreg.CloseKey(key)
        return True
    except Exception as e:
        log('Blocklist registry write failed: ' + str(e), C_FAIL)
        return False

# ── Driver detection ──────────────────────────────────────────────────────────

def is_driver_installed():
    sys_dir = os.path.join(os.environ.get('SystemRoot', r'C:\Windows'), 'System32', 'drivers')
    for fname in ('keyboard_upper_filter.sys', 'mouse_upper_filter.sys'):
        if os.path.isfile(os.path.join(sys_dir, fname)):
            return True
    for svc in ('keyboard_upper_filter', 'mouse_upper_filter'):
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                    r'SYSTEM\CurrentControlSet\Services\\' + svc):
                return True
        except OSError:
            pass
    for guid in (
        r'SYSTEM\CurrentControlSet\Control\Class\{4D36E96B-E325-11CE-BFC1-08002BE10318}',
        r'SYSTEM\CurrentControlSet\Control\Class\{4D36E96C-E325-11CE-BFC1-08002BE10318}',
    ):
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, guid) as k:
                val, _ = winreg.QueryValueEx(k, 'UpperFilters')
                entries = val if isinstance(val, list) else [val]
                if any('filter' in f.lower() for f in entries):
                    return True
        except OSError:
            pass
    return False

# ── Driver installation ───────────────────────────────────────────────────────

def run_installer():
    """
    Copy install-interception.exe to C:\\interception_tmp (a plain local path,
    not OneDrive/network/AppData-Temp) and run it with captured output.

    Returns (success: bool, output: str).

    NOTE: VulnerableDriverBlocklistEnable MUST be 0 at boot time for this to
    succeed. That means the caller must have disabled it and rebooted BEFORE
    calling this function.

    FIX: Previously used CREATE_NEW_CONSOLE together with stdout/stderr file
    redirections — these two are mutually exclusive on Windows. CREATE_NEW_CONSOLE
    allocates a fresh console with new stdio handles for the child, so any
    pipe/file redirections passed by the parent are silently ignored and output
    is never captured.  We now use CREATE_NO_WINDOW (keeps the process hidden
    without spawning a new console) so that stdout/stderr capture works correctly.
    """
    if not os.path.isfile(INSTALLER):
        return False, 'install-interception.exe not found at:\n' + INSTALLER

    tmp_dir = 'C:\\interception_tmp'
    tmp_exe = os.path.join(tmp_dir, 'install-interception.exe')

    try:
        os.makedirs(tmp_dir, exist_ok=True)
        shutil.copy2(INSTALLER, tmp_exe)
    except Exception as e:
        return False, 'Failed to copy installer to C:\\interception_tmp: ' + str(e)

    log('Running installer from: ' + tmp_dir, C_INFO)

    # Run the installer and capture stdout+stderr directly.
    #
    # IMPORTANT: Do NOT use CREATE_NEW_CONSOLE here.  On Windows, that flag
    # allocates a brand-new console for the child process (with fresh stdio
    # handles), which makes stdout= / stderr= pipe redirections completely
    # ineffective — the child's output goes to the new console window and
    # nothing is captured.  CREATE_NO_WINDOW hides the window while keeping
    # the existing console context, so pipe redirection works correctly.
    try:
        result = subprocess.run(
            [tmp_exe, '/install'],
            cwd=tmp_dir,
            capture_output=True,
            text=True,
            timeout=60,
            creationflags=subprocess.CREATE_NO_WINDOW
        )
        rc = result.returncode
        output = (result.stdout + result.stderr).strip()
    except subprocess.TimeoutExpired:
        return False, 'Installer timed out after 60 seconds.'
    except Exception as e:
        return False, 'Installer launch error: ' + str(e)
    finally:
        # Best-effort cleanup of the temp directory
        try:
            shutil.rmtree(tmp_dir, ignore_errors=True)
        except Exception:
            pass

    return (rc == 0), output

# ── Component helpers ─────────────────────────────────────────────────────────

def check_file(path, label):
    if not os.path.isfile(path):
        log('NOT FOUND: ' + label, C_FAIL)
        log('  Expected at: ' + path, C_FAIL)
        return False
    return True

def launch_exe(path, label):
    log('Launching ' + label + ' ...')
    subprocess.Popen([path], cwd=os.path.dirname(path),
                     creationflags=subprocess.CREATE_NEW_CONSOLE)
    log(label + ' is running.', C_OK)

# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    banner()

    if not is_admin():
        log('Administrator rights required. Requesting UAC...', C_WARN)
        time.sleep(0.5)
        elevate_and_exit()

    log('Running as ' + C_BOLD + 'Administrator' + C_RESET + C_OK + '.', C_OK)
    print()

    # ══════════════════════════════════════════════════════════════════════
    # STAGE 0 — Driver already installed and reboot done → just start apps
    # ══════════════════════════════════════════════════════════════════════
    step(1, 4, 'Checking Interception driver...')

    if os.path.isfile(FLAG_PENDING_REBOOT):
        # We installed the driver last run — now we must reboot.
        os.remove(FLAG_PENDING_REBOOT)
        do_reboot('Interception driver installed. Reboot required to activate it.')

    # ══════════════════════════════════════════════════════════════════════
    # STAGE 3 — Blocklist was disabled and we rebooted → now install driver
    # ══════════════════════════════════════════════════════════════════════
    if os.path.isfile(FLAG_BLOCKLIST_DISABLED):
        os.remove(FLAG_BLOCKLIST_DISABLED)
        log('Blocklist was disabled last boot. Installing driver now...', C_WARN)

        ok, output = run_installer()
        log('Installer output: ' + (output or '(no output)'),
            C_OK if ok else C_FAIL)

        # Always re-enable the blocklist after install attempt
        if set_blocklist(True):
            log('Vulnerable Driver Blocklist re-enabled.', C_OK)

        if ok:
            open(FLAG_PENDING_REBOOT, 'w').close()
            do_reboot('Driver installed! One more reboot needed to activate it.')
        else:
            msgbox('Driver Install Failed',
                   'Interception driver installation failed even after\n'
                   'disabling the Vulnerable Driver Blocklist.\n\n'
                   'Installer output:\n' + output + '\n\n'
                   'Possible fixes:\n'
                   '  1. Windows Security → App & Browser Control\n'
                   '     → Exploit protection → turn OFF "Block vulnerable drivers"\n'
                   '  2. Or run: bcdedit /set testsigning on  (then reboot)\n'
                   '  3. Or install a newer signed Interception build.', 0x10)
            sys.exit(1)

    # ══════════════════════════════════════════════════════════════════════
    # STAGE 2 — HVCI was just disabled → install after reboot flag
    # ══════════════════════════════════════════════════════════════════════
    if os.path.isfile(FLAG_INSTALL_AFTER_REBOOT):
        os.remove(FLAG_INSTALL_AFTER_REBOOT)
        log('HVCI/Memory Integrity was disabled. Proceeding with driver install...', C_WARN)
        # Fall through to the normal install path below

    # ══════════════════════════════════════════════════════════════════════
    # STAGE 1 — Normal check: driver not installed → set up and reboot chain
    # ══════════════════════════════════════════════════════════════════════
    if not is_driver_installed():

        # Step 1a: HVCI must be OFF
        if is_hvci_enabled():
            log('Memory Integrity (HVCI) is ON → disabling, will reboot...', C_WARN)
            try:
                disable_hvci()
                open(FLAG_INSTALL_AFTER_REBOOT, 'w').close()
                do_reboot('Memory Integrity disabled. Reboot 1 of 2 for driver setup.')
            except Exception as e:
                msgbox('HVCI Disable Failed',
                       'Could not disable Memory Integrity automatically.\n\n'
                       'Please disable it manually:\n'
                       '  Windows Security → Device Security\n'
                       '  → Core Isolation Details → Memory Integrity → OFF\n\n'
                       'Then reboot and run Launch_All.exe again.\n\n'
                       'Error: ' + str(e), 0x10)
                sys.exit(1)

        # Step 1b: Vulnerable Driver Blocklist must be OFF at next boot
        blocklist_val = get_blocklist()
        if blocklist_val == 1:
            log('Vulnerable Driver Blocklist is ON → disabling, will reboot to apply...', C_WARN)
            log('  (This change takes effect at next boot — one reboot required before install)', C_INFO)
            if set_blocklist(False):
                log('Blocklist disabled in registry. Rebooting now...', C_OK)
                open(FLAG_BLOCKLIST_DISABLED, 'w').close()
                do_reboot('Vulnerable Driver Blocklist disabled. Reboot needed before driver install.')
            else:
                msgbox('Registry Error',
                       'Could not disable the Vulnerable Driver Blocklist.\n\n'
                       'Manual fix:\n'
                       '  Windows Security → App & Browser Control\n'
                       '  → Exploit protection settings\n'
                       '  → Turn OFF "Block vulnerable drivers"\n\n'
                       'Then reboot and run Launch_All.exe again.', 0x30)
                sys.exit(1)

        # Step 1c: Blocklist is already 0 (or absent) — install now
        log('Interception driver NOT installed. Installing now...', C_WARN)
        ok, output = run_installer()
        log('Installer output: ' + (output or '(no output)'),
            C_OK if ok else C_FAIL)

        if ok:
            open(FLAG_PENDING_REBOOT, 'w').close()
            do_reboot('Driver installed! Reboot needed to activate it.')
        else:
            msgbox('Driver Install Failed',
                   'Interception driver installation failed.\n\n'
                   'Installer output:\n' + output + '\n\n'
                   'Possible fixes:\n'
                   '  1. Windows Security → App & Browser Control\n'
                   '     → Exploit protection → turn OFF "Block vulnerable drivers"\n'
                   '  2. Or run: bcdedit /set testsigning on  (then reboot)\n'
                   '  3. Or install a newer signed Interception build.', 0x10)
            sys.exit(1)

    else:
        log('Interception driver already installed.', C_OK)
    print()

    # ══════════════════════════════════════════════════════════════════════
    # All driver checks passed — launch the components
    # ══════════════════════════════════════════════════════════════════════

    step(2, 4, 'Verifying component files...')
    ok = check_file(PROXY_EXE, 'proxy.exe')
    ok = check_file(MOUSE_EXE, 'MouseCornerInterceptor.kiosk.exe') and ok
    ok = check_file(AGENT_EXE, 'agent.exe') and ok
    if not ok:
        msgbox('Missing Files',
               'One or more required files are missing.\n\n'
               'Expected:\n'
               '  Listener\\proxy.exe\n'
               '  MouseInterceptor\\MouseCornerInterceptor.kiosk.exe\n'
               '  agent\\agent.exe\n\n'
               'Please verify the folder structure and try again.', 0x10)
        sys.exit(1)
    log('All files verified.', C_OK)
    print()

    step(3, 4, 'Starting keyboard proxy (port 9920)...')
    launch_exe(PROXY_EXE, 'proxy.exe')
    time.sleep(2)
    print()

    step(4, 4, 'Starting mouse interceptor (port 9921) and AI agent...')
    launch_exe(MOUSE_EXE, 'MouseCornerInterceptor.kiosk.exe')
    time.sleep(1)
    launch_exe(AGENT_EXE, 'agent.exe')
    print()

    sep = '=' * 58
    print(C_HEADER + sep + C_RESET)
    print(C_OK + C_BOLD + '  All components launched successfully!' + C_RESET)
    print(C_HEADER + sep + C_RESET)
    print(C_INFO)
    print('  proxy.exe              ->  listening on port 9920')
    print('  MouseCornerInterceptor ->  listening on port 9921')
    print('  agent.py (agent.exe)   ->  waiting for key triggers')
    print(C_RESET)
    print(C_WARN + '  Key triggers:  qwe=screenshot  asd=text  jkl=solve  clear=reset' + C_RESET)
    print(C_WARN + '\n  This window closes in 8 seconds...' + C_RESET)
    time.sleep(8)

if __name__ == '__main__':
    try:
        main()
    except SystemExit:
        raise   # normal exit, don't intercept
    except Exception as _e:
        import traceback
        _tb = traceback.format_exc()
        try:
            ctypes.windll.user32.MessageBoxW(
                0,
                'Launcher crashed with an unexpected error:\n\n' + _tb,
                'KeyFlow — Unexpected Error',
                0x10   # MB_ICONERROR
            )
        except Exception:
            pass   # if even msgbox fails, nothing we can do
