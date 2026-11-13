"""my-dictation v1: hold Right Option, speak, release -> text is pasted at your cursor."""
import fcntl
import json
import os
import queue
import re
import socket
import subprocess
import sys
import threading
import time
import wave
from datetime import datetime
from pathlib import Path

import mlx_whisper
import numpy as np
import pyperclip
import sounddevice as sd
from pynput import keyboard

MODEL = "mlx-community/whisper-large-v3-turbo"
HOTKEY = keyboard.Key.alt_r  # Right Option
SAMPLE_RATE = 16000
MIN_SECONDS = 0.3  # ignore accidental taps; also the max length of a "tap"
DOUBLE_TAP_WINDOW = 0.5  # two taps within this many seconds = hands-free mode
HISTORY_FILE = Path(__file__).parent / "history.jsonl"  # one JSON line per dictation
PIECE_SECONDS = 30  # long recordings: send a piece to Whisper every ~30s while still recording
CUT_SEARCH_SECONDS = 5  # cut each piece at the quietest moment in its last 5s
PARTIAL_FILE = Path(__file__).parent / "current_session.txt"  # text so far; survives a crash
SHOW_OVERLAY = True  # set False to turn the floating status bar off
SHOW_MENU_BAR = True  # set False to hide the 🎙 menu bar icon
HISTORY_PORT = 8765  # must match history_ui.py
SHOW_COPY_POPUP = True  # set False to turn the "no text box" copy popup off
POPUP_WORDS = 60  # popup shows only the first N words; Copy copies everything
POPUP_SECONDS = 20  # popup closes itself after this long
TEXT_ROLES = {"AXTextField", "AXTextArea", "AXComboBox", "AXSearchField"}
SKIP_SILENCE = True  # don't send silent pieces to Whisper (it tends to invent "Thank you." there)
SPEECH_RMS = 0.005  # loudness that counts as speech; room noise measured ~0.0016, voice ~0.027
MIN_SPEECH_SECONDS = 0.2  # need at least this much speech-loud audio to transcribe a piece
DICTIONARY_FILE = Path(__file__).parent / "dictionary.txt"  # your words + "wrong => right" fixes
MAX_PROMPT_WORDS = 100  # Whisper only reads a short hint, so cap the word list
SAVE_AUDIO = True  # keep a .wav of each dictation (~2 MB per minute) for the history play button
AUDIO_DIR = Path(__file__).parent / "audio"
LOCK_FILE = Path(__file__).parent / ".dictate.lock"  # only one copy may run, or text pastes twice
RESTORE_CLIPBOARD = True  # put back what you had copied after pasting
AI_CLEANUP = False  # True = fix grammar/punctuation and remove "um/uh" with a local AI model (see cleanup.py)
# Tone per app for AI clean-up (app bundle id -> style in cleanup.STYLES); other apps use "default"
APP_STYLES = {
    "com.apple.mail": "formal",
    "com.microsoft.Outlook": "formal",
    "com.tinyspeck.slackmacgap": "casual",
    "com.apple.MobileSMS": "casual",
    "net.whatsapp.WhatsApp": "casual",
}
CLIPBOARD_RESTORE_DELAY = 1.0  # seconds; the app needs time to read the pasted text first

kb = keyboard.Controller()
jobs = queue.Queue()  # ("piece", audio) or ("end", seconds), handled in order by worker()
chunks = []
pending_samples = 0  # audio not yet sent to Whisper
session_samples = 0
session_pieces = 0
recording = False
stream = None
overlay_proc = None
history_proc = None
key_down = False
hands_free = False
ignore_release = False
press_time = 0.0
last_tap_time = 0.0


def on_audio(indata, frames, time_info, status):
    global pending_samples, session_samples
    chunks.append(indata.copy())
    pending_samples += frames
    session_samples += frames
    if pending_samples >= PIECE_SECONDS * SAMPLE_RATE:
        cut_piece()


def cut_piece():
    # Send everything up to the quietest recent moment to Whisper; keep the rest recording
    global chunks, pending_samples, session_pieces
    audio = np.concatenate(chunks).flatten()
    cut = quietest_point(audio)
    jobs.put(("piece", audio[:cut]))
    rest = audio[cut:]
    chunks = [rest.reshape(-1, 1)]
    pending_samples = len(rest)
    session_pieces += 1


def quietest_point(audio):
    # Split the last few seconds into 0.1s frames and return the middle of the quietest one
    frame = SAMPLE_RATE // 10
    n_frames = CUT_SEARCH_SECONDS * 10
    start = len(audio) - n_frames * frame
    energy = (audio[start:].reshape(n_frames, frame) ** 2).mean(axis=1)
    return start + int(energy.argmin()) * frame + frame // 2


def has_speech(audio):
    # Count 0.1s frames louder than SPEECH_RMS
    frame = SAMPLE_RATE // 10
    n = len(audio) // frame
    if n == 0:
        return False
    rms = np.sqrt((audio[: n * frame].reshape(n, frame) ** 2).mean(axis=1))
    return (rms > SPEECH_RMS).sum() * 0.1 >= MIN_SPEECH_SECONDS


def load_dictionary():
    # Returns (words to hint to Whisper, [(wrong, right)] fixes); empty if the file is missing or broken
    words, fixes = [], []
    try:
        for line in DICTIONARY_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=>" in line:
                wrong, right = (part.strip() for part in line.split("=>", 1))
                if wrong:
                    fixes.append((wrong, right))
                    words.append(right)
            else:
                words.append(line)
    except Exception:
        pass
    return [w for w in dict.fromkeys(words) if w], fixes


def apply_fixes(text, fixes):
    # Whole-word, case-insensitive replace: "ice berg" fixes "Ice Berg" but not "nice berg"
    for wrong, right in fixes:
        text = re.sub(rf"(?<!\w){re.escape(wrong)}(?!\w)", lambda m, r=right: r, text, flags=re.IGNORECASE)
    return text


def start_recording():
    global recording, stream, chunks, pending_samples, session_samples, session_pieces
    chunks = []
    pending_samples = session_samples = session_pieces = 0
    stream = sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="float32", callback=on_audio)
    stream.start()
    recording = True
    overlay("recording")
    print("🎙  Recording...")


def stop_recording():
    global recording
    recording = False
    stream.stop()
    stream.close()
    audio = np.concatenate(chunks).flatten() if chunks else np.zeros(0, dtype="float32")
    if session_pieces == 0 and len(audio) < MIN_SECONDS * SAMPLE_RATE:
        overlay("hide")
        print("   (too short, skipped)")
        return
    overlay("transcribing")
    if len(audio) >= MIN_SECONDS * SAMPLE_RATE:  # skip a tiny leftover tail
        jobs.put(("piece", audio))
    jobs.put(("end", session_samples / SAMPLE_RATE))


def worker():
    # Runs in the background: transcribes pieces in order, then pastes the full text at "end"
    parts = []
    wav = None  # open audio file for the current session
    while True:
        kind, payload = jobs.get()
        if kind == "piece":
            if SAVE_AUDIO:
                wav = wav or open_audio_file()
                write_audio(wav, payload)
            if SKIP_SILENCE and not has_speech(payload):
                print(f"   🤫 silent piece skipped ({len(payload) / SAMPLE_RATE:.0f}s audio)")
                continue
            t = time.time()
            words, fixes = load_dictionary()
            hint = {"initial_prompt": ", ".join(words[:MAX_PROMPT_WORDS])} if words else {}
            try:
                text = mlx_whisper.transcribe(
                    payload, path_or_hf_repo=MODEL, condition_on_previous_text=False, **hint
                )["text"].strip()
            except Exception as e:
                # Lose this piece only; the worker must keep running for the next dictation
                print(f"   ⚠️  Whisper failed on this piece: {e}")
                continue
            text = apply_fixes(text, fixes)
            if text:
                parts.append(text)
                with open(PARTIAL_FILE, "a", encoding="utf-8") as f:
                    f.write(text + "\n")
            print(f"   ✏️  piece {len(parts)} ({len(payload) / SAMPLE_RATE:.0f}s audio, {time.time() - t:.1f}s): {text[:60]!r}")
        else:
            full = " ".join(parts)
            parts = []
            PARTIAL_FILE.unlink(missing_ok=True)
            audio_name = close_audio_file(wav, keep=bool(full))
            wav = None
            raw = full
            if full and AI_CLEANUP:
                full = ai_cleanup(full)
            if not recording:  # a new recording may already have started
                overlay("hide")
            if full:
                print(f"   ✅ {len(full.split())} words from {payload:.0f}s of audio")
                save_history(full, payload, audio_name, raw if raw != full else None)
                no_text_box = SHOW_COPY_POPUP and focused_on_text_box() is False
                paste(full)
                if no_text_box:
                    threading.Thread(target=show_copy_popup, args=(full,), daemon=True).start()


def paste(text):
    saved = save_clipboard() if RESTORE_CLIPBOARD else None
    pyperclip.copy(text)
    with kb.pressed(keyboard.Key.cmd):
        kb.press("v")
        kb.release("v")
    if saved is not None:
        threading.Timer(CLIPBOARD_RESTORE_DELAY, restore_clipboard, args=(saved, clipboard_change_count())).start()


def save_clipboard():
    # Copy of every item and format (text, images, files) on the clipboard; None if it fails
    try:
        from AppKit import NSPasteboard

        items = NSPasteboard.generalPasteboard().pasteboardItems() or []
        return [{t: item.dataForType_(t) for t in item.types()} for item in items]
    except Exception:
        return None


def clipboard_change_count():
    try:
        from AppKit import NSPasteboard

        return NSPasteboard.generalPasteboard().changeCount()
    except Exception:
        return None


def restore_clipboard(saved, expected_count):
    # Skip if anything was copied after our paste, so we never overwrite newer content
    try:
        from AppKit import NSPasteboard, NSPasteboardItem

        pb = NSPasteboard.generalPasteboard()
        if expected_count is None or pb.changeCount() != expected_count:
            return
        pb.clearContents()
        new_items = []
        for formats in saved:
            item = NSPasteboardItem.alloc().init()
            for t, data in formats.items():
                if data is not None:
                    item.setData_forType_(data, t)
            new_items.append(item)
        if new_items:
            pb.writeObjects_(new_items)
    except Exception as e:
        print(f"   (clipboard not restored: {e})")


def start_overlay():
    # Floating bar + menu icon run as their own process; if it fails, dictation works without it
    global overlay_proc
    if not (SHOW_OVERLAY or SHOW_MENU_BAR):
        return
    args = [] if SHOW_OVERLAY else ["--no-bar"]
    args += [] if SHOW_MENU_BAR else ["--no-menu"]
    try:
        overlay_proc = subprocess.Popen(
            [sys.executable, str(Path(__file__).parent / "overlay.py"), *args],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
        )
        threading.Thread(target=read_menu_clicks, args=(overlay_proc,), daemon=True).start()
    except Exception as e:
        print(f"(floating bar not started: {e})")


def read_menu_clicks(proc):
    for line in proc.stdout:
        try:
            handle_menu_click(line.strip())
        except Exception as e:
            print(f"   (menu action failed: {e})")


def handle_menu_click(action):
    if action == "open-history":
        open_history()
    elif action == "quit":
        print("Quit from menu bar.")
        quit_app()


def quit_app():
    for proc in (history_proc, overlay_proc):
        try:
            if proc:
                proc.terminate()
        except Exception:
            pass
    os._exit(0)  # exit code 0: the login item won't restart it


def history_server_running():
    try:
        with socket.create_connection(("127.0.0.1", HISTORY_PORT), timeout=0.3):
            return True
    except OSError:
        return False


def open_history():
    # Start history_ui.py in the background if it isn't running yet, then open it in the browser
    global history_proc
    if not history_server_running():
        history_proc = subprocess.Popen(
            [sys.executable, str(Path(__file__).parent / "history_ui.py"), "--no-browser"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        for _ in range(30):
            if history_server_running():
                break
            time.sleep(0.1)
    subprocess.run(["open", f"http://127.0.0.1:{HISTORY_PORT}"])


def overlay(state):
    try:
        if overlay_proc and overlay_proc.poll() is None:
            overlay_proc.stdin.write(state + "\n")
            overlay_proc.stdin.flush()
    except Exception:
        pass


def focused_on_text_box():
    # True/False if macOS tells us; None if we can't tell (then no popup, same as before)
    try:
        from ApplicationServices import AXIsProcessTrusted, AXUIElementCopyAttributeValue, AXUIElementCreateSystemWide

        if not AXIsProcessTrusted():
            return None
        err, el = AXUIElementCopyAttributeValue(AXUIElementCreateSystemWide(), "AXFocusedUIElement", None)
        if err or el is None:
            return False
        err, role = AXUIElementCopyAttributeValue(el, "AXRole", None)
        return role in TEXT_ROLES
    except Exception:
        return None


def show_copy_popup(text):
    # Native macOS dialog in its own process, so a problem here can't affect dictation
    try:
        words = text.split()
        preview = " ".join(words[:POPUP_WORDS]) + (" …" if len(words) > POPUP_WORDS else "")
        script = [
            "on run argv",
            f'display dialog (item 1 of argv) with title "my-dictation: no text box found" '
            f'buttons {{"Close", "Copy all"}} default button "Copy all" giving up after {POPUP_SECONDS}',
            "return button returned of result",
            "end run",
        ]
        args = ["osascript"] + [p for line in script for p in ("-e", line)] + [preview]
        result = subprocess.run(args, capture_output=True, text=True, timeout=POPUP_SECONDS + 10)
        if result.stdout.strip() == "Copy all":
            pyperclip.copy(text)
            print("   📋 Copied full text from popup")
    except Exception as e:
        print(f"   (popup failed: {e})")


def open_audio_file():
    # Returns an open 16 kHz mono wav, or None if it can't be created (dictation still works)
    try:
        AUDIO_DIR.mkdir(exist_ok=True)
        path = AUDIO_DIR / f"{datetime.now():%Y%m%d-%H%M%S-%f}.wav"
        f = wave.open(str(path), "wb")
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(SAMPLE_RATE)
        f.path = path
        return f
    except Exception as e:
        print(f"   (audio not saved: {e})")
        return None


def write_audio(f, audio):
    try:
        if f:
            f.writeframes((np.clip(audio, -1, 1) * 32767).astype("<i2").tobytes())
    except Exception as e:
        print(f"   (audio not saved: {e})")


def close_audio_file(f, keep):
    # Returns the file name to store in history, or None
    if not f:
        return None
    try:
        f.close()
        if keep:
            return f.path.name
        f.path.unlink(missing_ok=True)  # nothing was said, so don't keep the audio
    except Exception as e:
        print(f"   (audio not saved: {e})")
    return None


def take_single_instance_lock():
    # Returns the open lock file (keep it open), or None if another copy already holds it
    f = open(LOCK_FILE, "w")
    try:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return f
    except OSError:
        f.close()
        return None


def frontmost_app():
    try:
        from AppKit import NSWorkspace

        return NSWorkspace.sharedWorkspace().frontmostApplication().bundleIdentifier()
    except Exception:
        return None


def ai_cleanup(text):
    # Returns cleaned text, or the original if clean-up fails or looks wrong
    try:
        import cleanup

        style = APP_STYLES.get(frontmost_app(), "default")
        t = time.time()
        cleaned, why = cleanup.clean(text, style)
        print(f"   🧹 clean-up ({style}, {time.time() - t:.1f}s): {why}")
        return cleaned
    except Exception as e:
        print(f"   (clean-up skipped: {e})")
        return text


def save_history(text, audio_seconds, audio_name=None, raw=None):
    entry = {"time": datetime.now().isoformat(timespec="seconds"), "text": text, "seconds": round(audio_seconds, 1)}
    if audio_name:
        entry["audio"] = audio_name
    if raw:
        entry["raw"] = raw  # what Whisper heard, before AI clean-up
    with open(HISTORY_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def on_press(key):
    global key_down, press_time, hands_free, ignore_release
    # Holding a key repeats on_press, so only act once
    if key != HOTKEY or key_down:
        return
    key_down = True
    press_time = time.time()
    if hands_free:
        # Tap while hands-free -> finish recording
        hands_free = False
        ignore_release = True
        stop_recording()
    elif not recording:
        start_recording()


def on_release(key):
    global key_down, hands_free, ignore_release, last_tap_time
    if key != HOTKEY:
        return
    key_down = False
    if ignore_release:
        ignore_release = False
        return
    if not recording:
        return
    now = time.time()
    if now - press_time >= MIN_SECONDS:
        stop_recording()  # normal hold-to-talk
    elif now - last_tap_time < DOUBLE_TAP_WINDOW:
        hands_free = True  # second quick tap -> keep recording
        last_tap_time = 0.0
        overlay("handsfree")
        print("   🔓 Hands-free on. Tap Right Option to finish.")
    else:
        last_tap_time = now  # first quick tap -> wait for a second one
        stop_recording()


if __name__ == "__main__":
    lock = take_single_instance_lock()
    if lock is None:
        print("my-dictation is already running (maybe started at login). Not starting a second copy.")
        sys.exit(0)
    print("Loading Whisper model...")
    mlx_whisper.transcribe(np.zeros(SAMPLE_RATE, dtype="float32"), path_or_hf_repo=MODEL)
    if AI_CLEANUP:
        print("Loading AI clean-up model...")
        try:
            import cleanup

            cleanup.load()
        except Exception as e:
            print(f"(AI clean-up unavailable, dictation works without it: {e})")
    if PARTIAL_FILE.exists():
        # Last run crashed mid-recording: keep its text instead of mixing it with the next one
        saved = PARTIAL_FILE.with_name(f"recovered-{datetime.now():%Y%m%d-%H%M%S}.txt")
        PARTIAL_FILE.rename(saved)
        print(f"Recovered text from an unfinished session: {saved.name}")
    threading.Thread(target=worker, daemon=True).start()
    start_overlay()
    print("Ready. Hold Right Option to dictate, or double-tap for hands-free. Ctrl+C to quit.")
    with keyboard.Listener(on_press=on_press, on_release=on_release) as listener:
        listener.join()
