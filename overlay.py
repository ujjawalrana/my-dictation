"""Floating status pill + menu bar icon for my-dictation. Started by dictate.py.

Reads one state per line on stdin: recording | handsfree | transcribing | hide
Writes menu clicks to stdout: open-history | quit
Options: --no-bar (menu icon only), --no-menu (floating bar only)
Runs as its own process so a problem here can never break dictation.
"""
import sys
import threading
import time

import AppKit
import Quartz
from PyObjCTools import AppHelper

WIDTH, HEIGHT = 260, 36
BOTTOM_MARGIN = 90  # distance from the bottom of the screen (above the Dock)

state = "hide"
started_at = 0.0
panel = label = status_item = None
MENU_ICONS = {"recording": "🔴", "handsfree": "🔴", "transcribing": "⏳"}  # idle: 🎙


class MenuHandler(AppKit.NSObject):
    # Menu clicks are sent to dictate.py, which does the actual work
    def openHistory_(self, sender):
        print("open-history", flush=True)

    def quit_(self, sender):
        print("quit", flush=True)


def make_menu():
    item = AppKit.NSStatusBar.systemStatusBar().statusItemWithLength_(AppKit.NSVariableStatusItemLength)
    item.button().setTitle_("🎙")
    menu = AppKit.NSMenu.alloc().init()
    menu.setAutoenablesItems_(False)
    title = menu.addItemWithTitle_action_keyEquivalent_("my-dictation · hold Right ⌥ to talk", None, "")
    title.setEnabled_(False)
    menu.addItem_(AppKit.NSMenuItem.separatorItem())
    for text, action in [("Open history", "openHistory:"), ("Quit my-dictation", "quit:")]:
        entry = menu.addItemWithTitle_action_keyEquivalent_(text, action, "")
        entry.setTarget_(handler)
    item.setMenu_(menu)
    return item


def make_panel():
    screen = AppKit.NSScreen.mainScreen().visibleFrame()
    x = screen.origin.x + (screen.size.width - WIDTH) / 2
    y = screen.origin.y + BOTTOM_MARGIN
    style = AppKit.NSWindowStyleMaskBorderless | AppKit.NSWindowStyleMaskNonactivatingPanel
    panel = AppKit.NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
        ((x, y), (WIDTH, HEIGHT)), style, AppKit.NSBackingStoreBuffered, False
    )
    # Float above everything, on every Space, never take focus or mouse clicks
    panel.setLevel_(AppKit.NSStatusWindowLevel)
    panel.setCollectionBehavior_(
        AppKit.NSWindowCollectionBehaviorCanJoinAllSpaces
        | AppKit.NSWindowCollectionBehaviorStationary
        | AppKit.NSWindowCollectionBehaviorFullScreenAuxiliary
    )
    panel.setIgnoresMouseEvents_(True)
    panel.setOpaque_(False)
    panel.setHasShadow_(True)
    panel.setBackgroundColor_(AppKit.NSColor.clearColor())

    view = panel.contentView()
    view.setWantsLayer_(True)
    view.layer().setCornerRadius_(HEIGHT / 2)
    view.layer().setBackgroundColor_(Quartz.CGColorCreateGenericGray(0.1, 0.92))

    label = AppKit.NSTextField.labelWithString_("")
    label.setFrame_(((0, (HEIGHT - 20) / 2), (WIDTH, 20)))
    label.setAlignment_(AppKit.NSTextAlignmentCenter)
    label.setTextColor_(AppKit.NSColor.whiteColor())
    label.setFont_(AppKit.NSFont.systemFontOfSize_weight_(13, AppKit.NSFontWeightMedium))
    view.addSubview_(label)
    return panel, label


def render():
    if status_item:
        status_item.button().setTitle_(MENU_ICONS.get(state, "🎙"))
    if not panel:
        return
    if state == "hide":
        panel.orderOut_(None)
        return
    secs = int(time.time() - started_at)
    clock = f"{secs // 60}:{secs % 60:02d}"
    text = {
        "recording": f"🔴  Recording  {clock}",
        "handsfree": f"🟢  Hands-free  {clock}  ·  tap ⌥ to stop",
        "transcribing": "⏳  Transcribing…",
    }.get(state, "")
    label.setStringValue_(text)
    panel.orderFrontRegardless()


def set_state(new):
    global state, started_at
    if new == "recording" or (new == "handsfree" and state not in ("recording", "handsfree")):
        started_at = time.time()
    state = new
    render()


def read_stdin():
    for line in sys.stdin:
        AppHelper.callAfter(set_state, line.strip())
    AppHelper.callAfter(AppHelper.stopEventLoop)  # parent closed the pipe -> quit


def tick():
    while True:
        time.sleep(0.5)
        if panel and state in ("recording", "handsfree"):
            AppHelper.callAfter(render)


if __name__ == "__main__":
    app = AppKit.NSApplication.sharedApplication()
    app.setActivationPolicy_(AppKit.NSApplicationActivationPolicyAccessory)  # no Dock icon
    handler = MenuHandler.alloc().init()
    if "--no-bar" not in sys.argv:
        panel, label = make_panel()
    if "--no-menu" not in sys.argv:
        status_item = make_menu()
    threading.Thread(target=read_stdin, daemon=True).start()
    threading.Thread(target=tick, daemon=True).start()
    AppHelper.runEventLoop()
