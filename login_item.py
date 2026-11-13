"""Start my-dictation automatically when you log in (macOS LaunchAgent).

Usage:
  python login_item.py install     # start at login, and start it now
  python login_item.py uninstall   # stop it and don't start at login
  python login_item.py status      # is it installed / running?

Logs go to logs/dictate.log in this folder.
"""
import os
import plistlib
import subprocess
import sys
from pathlib import Path

LABEL = "com.my-dictation"
HERE = Path(__file__).resolve().parent
PLIST = Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"
LOG = HERE / "logs" / "dictate.log"
DOMAIN = f"gui/{os.getuid()}"


def build_plist():
    return {
        "Label": LABEL,
        # -u: unbuffered, so the log shows lines immediately
        "ProgramArguments": [str(HERE / ".venv" / "bin" / "python"), "-u", str(HERE / "dictate.py")],
        "WorkingDirectory": str(HERE),
        "RunAtLoad": True,
        "KeepAlive": {"SuccessfulExit": False},  # restart after a crash, not after you quit it
        "ThrottleInterval": 10,
        "ProcessType": "Interactive",
        "StandardOutPath": str(LOG),
        "StandardErrorPath": str(LOG),
    }


def launchctl(*args):
    return subprocess.run(["launchctl", *args], capture_output=True, text=True)


def install():
    LOG.parent.mkdir(exist_ok=True)
    PLIST.parent.mkdir(parents=True, exist_ok=True)
    launchctl("bootout", f"{DOMAIN}/{LABEL}")  # remove an older copy if present
    PLIST.write_bytes(plistlib.dumps(build_plist()))
    r = launchctl("bootstrap", DOMAIN, str(PLIST))
    if r.returncode != 0:
        print(f"Could not start it: {r.stderr.strip()}")
        return 1
    print(f"Installed. my-dictation now starts at login. Log: {LOG}")
    print("First run: macOS may ask for Microphone; also allow 'Python' in")
    print("System Settings > Privacy & Security > Accessibility and Input Monitoring.")
    return 0


def uninstall():
    launchctl("bootout", f"{DOMAIN}/{LABEL}")
    PLIST.unlink(missing_ok=True)
    print("Uninstalled. my-dictation will no longer start at login.")
    return 0


def status():
    installed = PLIST.exists()
    running = launchctl("print", f"{DOMAIN}/{LABEL}").returncode == 0
    print(f"Installed: {'yes' if installed else 'no'}   Loaded: {'yes' if running else 'no'}   Log: {LOG}")
    return 0


if __name__ == "__main__":
    commands = {"install": install, "uninstall": uninstall, "status": status}
    if len(sys.argv) != 2 or sys.argv[1] not in commands:
        print(__doc__)
        sys.exit(1)
    sys.exit(commands[sys.argv[1]]())
