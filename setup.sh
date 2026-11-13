#!/usr/bin/env bash
# One-command setup: ./setup.sh
# Creates the virtual environment, installs everything, then starts my-dictation.
# Use ./setup.sh --no-run to only set up.
set -euo pipefail
cd "$(dirname "$0")"

say() { printf "\n\033[1m==> %s\033[0m\n" "$1"; }

# 1. This app needs a Mac with Apple Silicon (M1 or newer)
if [[ "$(uname -s)" != "Darwin" || "$(uname -m)" != "arm64" ]]; then
  echo "Sorry: my-dictation needs macOS on Apple Silicon (M1/M2/M3/...)." >&2
  exit 1
fi

# 2. Find Python 3.12 (or 3.11 / 3.13); install 3.12 with Homebrew if none
PYTHON=""
for v in 3.12 3.11 3.13; do
  if command -v "python$v" >/dev/null 2>&1; then PYTHON="python$v"; break; fi
done
if [[ -z "$PYTHON" ]]; then
  if ! command -v brew >/dev/null 2>&1; then
    echo "Python 3.12 is needed. Install Homebrew from https://brew.sh, then run ./setup.sh again." >&2
    exit 1
  fi
  say "Installing Python 3.12 with Homebrew"
  brew install python@3.12
  PYTHON="$(brew --prefix python@3.12)/bin/python3.12"
fi
say "Using $($PYTHON --version)"

# 3. Virtual environment (shows as "my-dictation" in your prompt)
if [[ ! -x .venv/bin/python ]]; then
  say "Creating virtual environment"
  "$PYTHON" -m venv .venv --prompt my-dictation
fi

# 4. Libraries
say "Installing libraries (first time takes a few minutes)"
.venv/bin/python -m pip install --quiet --upgrade pip
.venv/bin/python -m pip install --quiet -r requirements.txt

# 5. Your personal dictionary starts empty
[[ -f dictionary.txt ]] || cp dictionary.example.txt dictionary.txt

say "Setup done."
cat <<'EOF'
First time only, allow your terminal app in
System Settings > Privacy & Security:
  - Microphone        (macOS asks on first recording)
  - Accessibility     (needed to paste)
  - Input Monitoring  (needed for the Right Option hotkey)
The first start downloads the Whisper model (~1.6 GB).

Next time, start it with:
  source .venv/bin/activate && python dictate.py
EOF

if [[ "${1:-}" != "--no-run" ]]; then
  say "Starting my-dictation (Ctrl+C to stop)"
  exec .venv/bin/python dictate.py
fi
