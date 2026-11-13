# my-dictation

Free, offline voice dictation for macOS. Hold a key, speak, and the text appears wherever your cursor is. No word limits, no account, nothing leaves your Mac.

Runs [Whisper](https://github.com/openai/whisper) locally on Apple Silicon with [MLX](https://github.com/ml-explore/mlx).

## Quick start

Needs a Mac with Apple Silicon (M1 or newer) and [Homebrew](https://brew.sh).

```sh
git clone https://github.com/ujjawalrana/my-dictation.git
cd my-dictation
bash setup.sh
```

`bash setup.sh` installs Python 3.12 if needed, creates a virtual environment, installs everything and starts the app.
The first start downloads the Whisper model (about 1.6 GB).

**First time only:** in System Settings → Privacy & Security, allow your terminal app under **Microphone**, **Accessibility** and **Input Monitoring**.

Next time, start it with:

```sh
source .venv/bin/activate && python dictate.py
```

## How to use

| Action | What happens |
|---|---|
| Hold **Right Option** and speak | Text is pasted when you let go |
| **Double-tap** Right Option | Hands-free mode; tap once more to finish |
| 🎙 in the menu bar | Open history, or quit |

Long recordings (meetings) are transcribed in 30-second pieces while you talk, so the text is ready a second or two after you stop.

## Features

- About 99 languages, detected automatically
- Floating status bar and menu bar icon
- History with search, copy and audio playback: `python history_ui.py` or 🎙 → Open history
- History in the terminal: `python history.py`, or `python history.py -s word` to search
- Personal dictionary: add names and terms to `dictionary.txt`
- Copy popup when there is no text box to paste into
- Your clipboard is restored after pasting
- Silent parts are skipped, so Whisper doesn't invent text
- Crash-safe: text from an unfinished session is saved to `recovered-*.txt`
- Optional AI clean-up (removes "um/uh", fixes punctuation) with a local model: set `AI_CLEANUP = True` in `dictate.py` (downloads about 1.7 GB on first use)

Every feature can be switched on or off at the top of `dictate.py`.

## Start automatically at login

```sh
source .venv/bin/activate
python login_item.py install     # undo with: python login_item.py uninstall
```

macOS will then ask you to allow **Python** (instead of your terminal) for Microphone, Accessibility and Input Monitoring.
Logs go to `logs/dictate.log`.

## Tests

```sh
source .venv/bin/activate
python -m unittest discover -s tests -v
```

A few tests use a short recording of your voice. To include them, record `test.wav` by saying "hello":

```sh
python -c "import sounddevice as sd, scipy.io.wavfile as w; a=sd.rec(5*16000, samplerate=16000, channels=1, dtype='int16'); sd.wait(); w.write('test.wav', 16000, a)"
```

## Privacy

Everything runs on your Mac. Your history (`history.jsonl`), recordings (`audio/`) and dictionary stay local and are excluded from git by `.gitignore`.
