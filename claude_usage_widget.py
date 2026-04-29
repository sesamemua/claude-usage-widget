#!/usr/bin/env python3
"""
Claude Usage Desktop Widget
============================
A true macOS desktop widget — borderless, pinned to the desktop behind
all windows, rounded corners, translucent. Shows Claude usage stats.
Uses a hidden WKWebView to fetch data. Auto-refreshes every 5 minutes.
"""

import json
import datetime
import math
import os
import subprocess
import time
import objc
import AppKit
import WebKit
import Foundation

USAGE_URL = "https://claude.ai/settings/usage"
REFRESH_SECONDS = 5 * 60

LOGIN_W, LOGIN_H = 400, 500

# Single-row layout: drag tab + 3 bars + ↻ × buttons all inline
BAR_H = 20
WIDGET_PAD = 4
ROW_GAP = 2
DRAG_TAB_W = 10
BTN_W = 18

# Variable bar widths — session is widest to fit "4h 10m · 4:39am"
SESSION_BAR_W = 195
ALL_BAR_W = 132
SONNET_BAR_W = 92  # bumped a bit per user request
COUNTDOWN_W = 44

# Horizontal: [pad][tab][gap][session][gap][all][gap][sonnet][gap][⏱][gap][↻⇌×][pad]
H_WIDGET_W = (WIDGET_PAD + DRAG_TAB_W + 4
              + SESSION_BAR_W + ROW_GAP
              + ALL_BAR_W + ROW_GAP
              + SONNET_BAR_W + ROW_GAP
              + COUNTDOWN_W
              + 4 + BTN_W * 3 + WIDGET_PAD)
H_WIDGET_H = BAR_H

# Refresh-interval choices (seconds) cycled by clicking the countdown
REFRESH_INTERVAL_CHOICES = [60, 300, 900, 1800]  # 1m, 5m, 15m, 30m

# Vertical (stacked) — long & thin, no wider than horizontal H (=BAR_H).
V_PAD = 2
V_DRAG_H = 6
V_GAP = 2
V_BAR_H = 16          # bar (metric) height
V_BTN_W = 16          # button square size in vertical mode
V_BAR_W = BAR_H - V_PAD * 2  # 16 — bar width = horizontal widget height minus pad
V_WIDGET_W = BAR_H            # 20, exactly horizontal widget's height
V_WIDGET_H = (V_PAD + V_DRAG_H + 4
              + V_BAR_H * 3 + V_GAP * 2
              + 3 + 12 + 3                  # countdown row
              + V_BTN_W * 3 + V_GAP * 2
              + V_PAD)


def widget_dimensions():
    prefs = load_prefs()
    if prefs.get("vertical"):
        return (V_WIDGET_W, V_WIDGET_H)
    return (H_WIDGET_W, H_WIDGET_H)

PLIST_PATH = os.path.expanduser(
    "~/Library/LaunchAgents/com.claude.usage-widget.plist"
)
PLIST_LABEL = "com.claude.usage-widget"
PREFS_PATH = os.path.expanduser("~/.claude_usage_widget_prefs.json")


def load_prefs():
    try:
        with open(PREFS_PATH) as f:
            return json.load(f)
    except Exception:
        return {"pin_on_top": True}


def save_prefs(prefs):
    try:
        with open(PREFS_PATH, "w") as f:
            json.dump(prefs, f)
    except Exception:
        pass

EXTRACT_JS = r"""
(function() {
    var data = {loggedIn: false};
    var body = document.body ? document.body.innerText : '';
    if (body.indexOf('% used') === -1) return JSON.stringify(data);
    data.loggedIn = true;
    var m = body.match(/Current session[\s\S]*?Resets in ([^\n]+)[\s\S]*?(\d+)% used/);
    if (m) { data.sPct = parseInt(m[2]); data.sReset = m[1].trim(); }
    m = body.match(/All models[\s\S]*?Resets ([^\n]+)[\s\S]*?(\d+)% used/);
    if (m) { data.aPct = parseInt(m[2]); data.aReset = m[1].trim(); }
    var s = body.substring(body.indexOf('Sonnet only'));
    m = s.match(/Sonnet only[\s\S]*?Resets ([^\n]+)[\s\S]*?(\d+)% used/);
    if (m) { data.nPct = parseInt(m[2]); data.nReset = m[1].trim(); }
    m = body.match(/Max \(([^)]+)\)/);
    if (m) data.plan = m[1];
    return JSON.stringify(data);
})()
"""

# ── Globals ──
g_window = None
g_login_window = None
g_webview = None
g_logged_in = False
g_labels = {}
g_bars = {}
g_status_label = None
g_autostart_btn = None
g_pin_btn = None
g_menubar_btn = None
g_status_item = None       # NSStatusItem (menu bar item)
g_status_menu_items = {}   # dict of NSMenuItems we update with usage data
g_last_data = {}           # last fetched data, used to repopulate menubar item
g_countdown = None         # CountdownView instance
g_last_refresh_time = 0.0  # time.time() of last successful page reload
g_refresh_timer = None     # Foundation.NSTimer for auto-refresh


def get_refresh_interval():
    return load_prefs().get("refresh_seconds", 300)


def is_autostart_enabled():
    return os.path.exists(PLIST_PATH)


def set_autostart(enabled):
    if enabled:
        plist = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>{PLIST_LABEL}</string>
    <key>ProgramArguments</key>
    <array>
        <string>/usr/bin/open</string>
        <string>-a</string>
        <string>/Applications/Claude Usage.app</string>
    </array>
    <key>RunAtLoad</key><true/>
    <key>KeepAlive</key><false/>
</dict>
</plist>"""
        os.makedirs(os.path.dirname(PLIST_PATH), exist_ok=True)
        with open(PLIST_PATH, "w") as f:
            f.write(plist)
        subprocess.run(["launchctl", "load", PLIST_PATH], capture_output=True)
    else:
        subprocess.run(["launchctl", "unload", PLIST_PATH], capture_output=True)
        if os.path.exists(PLIST_PATH):
            os.remove(PLIST_PATH)


# High-tech saturated palette (cyber/neon)
NEON_BLUE    = (0.05, 0.70, 1.0)   # #0DB3FF — electrifying tech blue
NEON_MAGENTA = (1.0,  0.20, 0.78)  # #FF33C7 — vivid magenta
NEON_AMBER   = (1.0,  0.66, 0.05)  # #FFA80D — warning
NEON_PINK    = (1.0,  0.16, 0.55)  # #FF298C — critical


def color_for_pct(pct):
    """Saturated high-tech color for the widget bars."""
    if pct < 50:
        rgb = NEON_BLUE
    elif pct < 80:
        rgb = NEON_AMBER
    else:
        rgb = NEON_PINK
    return AppKit.NSColor.colorWithCalibratedRed_green_blue_alpha_(
        rgb[0], rgb[1], rgb[2], 1.0)


def menubar_color_for_pct(pct):
    """Slightly muted version for menu-bar text legibility on light/dark bars."""
    if pct < 50:
        rgb = (0.20, 0.45, 0.95)
    elif pct < 80:
        rgb = (0.85, 0.55, 0.05)
    else:
        rgb = (0.85, 0.18, 0.50)
    return AppKit.NSColor.colorWithCalibratedRed_green_blue_alpha_(
        rgb[0], rgb[1], rgb[2], 1.0)


# ── Custom views ──

class MetricBarView(AppKit.NSView):
    """Single-line metric: text on top + thick gradient line below."""

    LINE_H = 4
    LINE_PAD_X = 3
    LINE_BOTTOM_Y = 1

    def initWithFrame_label_(self, frame, label):
        self = objc.super(MetricBarView, self).initWithFrame_(frame)
        self._label = label
        self._key = None
        self._pct = None
        self._resetText = ""
        self._compact = False     # vertical mode = compact (percentage only)
        self._barColor = AppKit.NSColor.colorWithCalibratedRed_green_blue_alpha_(
            *NEON_BLUE, 1.0)
        return self

    def setCompactMode_(self, compact):
        self._compact = compact
        self.setNeedsDisplay_(True)

    def setData_color_resetText_(self, pct, color, resetText):
        self._pct = pct
        self._barColor = color
        self._resetText = resetText
        self.setToolTip_(resetText or "")
        self.setNeedsDisplay_(True)

    def mouseDown_(self, event):
        # Click on a metric bar shows a popover with details
        if self._key:
            show_metric_popover(self._key, self)

    def drawRect_(self, rect):
        # Full-height track — darker techno-blue slot
        AppKit.NSColor.colorWithCalibratedRed_green_blue_alpha_(
            0.04, 0.06, 0.13, 1.0).setFill()
        AppKit.NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            rect, 4, 4).fill()

        # Empty track line at the bottom — very dim
        line_x_pad = 5
        line_y = 2.5             # baseline of the pulse line
        line_w_max = rect.size.width - line_x_pad * 2

        empty_rect = Foundation.NSMakeRect(
            line_x_pad, line_y, line_w_max, 1.0)
        AppKit.NSColor.colorWithCalibratedWhite_alpha_(0.10, 1.0).setFill()
        AppKit.NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            empty_rect, 0.5, 0.5).fill()

        # Pulsing glowing line for the percentage
        if self._pct is not None and self._pct > 0:
            phase = (time.time() * 1.6) % (2 * math.pi)
            pulse = 0.5 + 0.5 * math.sin(phase)  # 0.0 → 1.0
            line_w = max(2, line_w_max * self._pct / 100.0)

            # Halo rings (largest first, dimmest) — extra ring for more glow
            for radius, base_a in ((4, 0.04), (3, 0.09), (2, 0.16), (1, 0.26)):
                halo = Foundation.NSMakeRect(
                    line_x_pad - radius, line_y - radius,
                    line_w + radius * 2, 1.0 + radius * 2)
                a = base_a + 0.10 * pulse
                self._barColor.colorWithAlphaComponent_(a).setFill()
                AppKit.NSBezierPath\
                    .bezierPathWithRoundedRect_xRadius_yRadius_(
                        halo, radius + 0.5, radius + 0.5).fill()

            # Hot core (1px solid line)
            core = Foundation.NSMakeRect(
                line_x_pad, line_y, line_w, 1.0)
            core_alpha = 0.65 + 0.35 * pulse
            self._barColor.colorWithAlphaComponent_(core_alpha).setFill()
            AppKit.NSBezierPath\
                .bezierPathWithRoundedRect_xRadius_yRadius_(
                    core, 0.5, 0.5).fill()

        # Compact mode (vertical layout): just the number centered, no %/label/reset
        pct_text = f"{self._pct}%" if self._pct is not None else "—"
        if self._compact:
            num_text = (str(self._pct) if self._pct is not None
                        else "—")
            attrs = {
                AppKit.NSForegroundColorAttributeName:
                    AppKit.NSColor.whiteColor(),
                AppKit.NSFontAttributeName:
                    AppKit.NSFont.monospacedDigitSystemFontOfSize_weight_(
                        8.5, 0.0),
            }
            ns = AppKit.NSAttributedString.alloc()\
                .initWithString_attributes_(num_text, attrs)
            sz = ns.size()
            ns.drawAtPoint_(Foundation.NSMakePoint(
                (rect.size.width - sz.width) / 2, 4))
            return

        # Standard mode: "SESSION 42%" left, reset on right
        left = f"{self._label.upper()} {pct_text}"
        left_attrs = {
            AppKit.NSForegroundColorAttributeName: AppKit.NSColor.whiteColor(),
            AppKit.NSFontAttributeName:
                AppKit.NSFont.systemFontOfSize_(9.5),
        }
        ns_left = AppKit.NSAttributedString.alloc()\
            .initWithString_attributes_(left, left_attrs)
        ly = 4
        ns_left.drawAtPoint_(Foundation.NSMakePoint(7, ly))

        if self._resetText:
            right_attrs = {
                AppKit.NSForegroundColorAttributeName:
                    AppKit.NSColor.colorWithCalibratedWhite_alpha_(0.98, 1.0),
                AppKit.NSFontAttributeName:
                    AppKit.NSFont.systemFontOfSize_(8.5),
            }
            ns_right = AppKit.NSAttributedString.alloc()\
                .initWithString_attributes_(self._resetText, right_attrs)
            rsize = ns_right.size()
            ns_right.drawAtPoint_(Foundation.NSMakePoint(
                rect.size.width - rsize.width - 7, ly + 0.5))


class HoverButton(AppKit.NSButton):
    """Borderless button that fades from dim to bright on hover."""

    NORMAL_ALPHA = 0.4
    HOVER_ALPHA = 1.0

    def initWithFrame_(self, frame):
        self = objc.super(HoverButton, self).initWithFrame_(frame)
        self.setAlphaValue_(self.NORMAL_ALPHA)
        self._addTracking()
        return self

    def _addTracking(self):
        for ta in list(self.trackingAreas()):
            self.removeTrackingArea_(ta)
        opts = (AppKit.NSTrackingMouseEnteredAndExited
                | AppKit.NSTrackingActiveInActiveApp
                | AppKit.NSTrackingInVisibleRect)
        ta = AppKit.NSTrackingArea.alloc()\
            .initWithRect_options_owner_userInfo_(
                self.bounds(), opts, self, None)
        self.addTrackingArea_(ta)

    def updateTrackingAreas(self):
        objc.super(HoverButton, self).updateTrackingAreas()
        self._addTracking()

    def mouseEntered_(self, event):
        self.animator().setAlphaValue_(self.HOVER_ALPHA)

    def mouseExited_(self, event):
        self.animator().setAlphaValue_(self.NORMAL_ALPHA)


class KeyableWindow(AppKit.NSWindow):
    """Borderless window that can become key/main (so it gets mouse events)."""

    def canBecomeKeyWindow(self):
        return True

    def canBecomeMainWindow(self):
        return True


class DraggableView(AppKit.NSView):
    """Content view: drag with left-click, settings menu with right-click."""

    def initWithFrame_target_(self, frame, target):
        self = objc.super(DraggableView, self).initWithFrame_(frame)
        self._target = target
        return self

    def mouseDown_(self, event):
        self.window().performWindowDragWithEvent_(event)

    def rightMouseDown_(self, event):
        if self._target:
            menu = build_settings_menu(self._target)
            AppKit.NSMenu.popUpContextMenu_withEvent_forView_(
                menu, event, self)

    def drawRect_(self, rect):
        # Borderless: subtle dark fill, no stroke
        AppKit.NSColor.colorWithCalibratedRed_green_blue_alpha_(
            0.11, 0.10, 0.14, 0.78).setFill()
        AppKit.NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            rect, 6, 6).fill()


class DragTabView(AppKit.NSView):
    """Small grip area on the left edge — drags the window when clicked."""

    def mouseDown_(self, event):
        self.window().performWindowDragWithEvent_(event)

    def drawRect_(self, rect):
        # Three small grip dots, vertically centered
        AppKit.NSColor.colorWithCalibratedWhite_alpha_(0.45, 1.0).setFill()
        cx = rect.size.width / 2
        cy = rect.size.height / 2
        for dy in (-5, 0, 5):
            dot = Foundation.NSMakeRect(cx - 1, cy + dy - 1, 2, 2)
            AppKit.NSBezierPath.bezierPathWithOvalInRect_(dot).fill()


class CountdownView(AppKit.NSView):
    """Mini timer showing seconds until next refresh. Click cycles interval."""

    def initWithFrame_(self, frame):
        self = objc.super(CountdownView, self).initWithFrame_(frame)
        self._secondsLeft = 0
        self._compact = False
        return self

    def setCompactMode_(self, compact):
        self._compact = compact
        self.setNeedsDisplay_(True)

    def setSecondsLeft_(self, s):
        self._secondsLeft = max(0, int(s))
        self.setNeedsDisplay_(True)

    def mouseDown_(self, event):
        cycle_refresh_interval()

    def drawRect_(self, rect):
        # Track bg
        AppKit.NSColor.colorWithCalibratedRed_green_blue_alpha_(
            0.04, 0.06, 0.13, 1.0).setFill()
        AppKit.NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            rect, 4, 4).fill()

        m, s = divmod(self._secondsLeft, 60)
        if self._compact:
            text = f"{m}:{s:02d}"
            font_size = 7.5
        else:
            text = f"⟲ {m}:{s:02d}"
            font_size = 9.5

        attrs = {
            AppKit.NSForegroundColorAttributeName:
                AppKit.NSColor.colorWithCalibratedWhite_alpha_(0.85, 1.0),
            AppKit.NSFontAttributeName:
                AppKit.NSFont.monospacedDigitSystemFontOfSize_weight_(
                    font_size, 0.0),
        }
        ns = AppKit.NSAttributedString.alloc()\
            .initWithString_attributes_(text, attrs)
        sz = ns.size()
        ns.drawAtPoint_(Foundation.NSMakePoint(
            (rect.size.width - sz.width) / 2,
            (rect.size.height - sz.height) / 2))


def cycle_refresh_interval():
    """Click handler on the countdown — cycles to the next interval."""
    global g_refresh_timer, g_last_refresh_time
    prefs = load_prefs()
    cur = prefs.get("refresh_seconds", 300)
    try:
        idx = REFRESH_INTERVAL_CHOICES.index(cur)
    except ValueError:
        idx = 1
    new_interval = REFRESH_INTERVAL_CHOICES[
        (idx + 1) % len(REFRESH_INTERVAL_CHOICES)]
    prefs["refresh_seconds"] = new_interval
    save_prefs(prefs)

    # Reschedule the auto-refresh timer with the new interval
    delegate = AppKit.NSApplication.sharedApplication().delegate()
    if g_refresh_timer:
        g_refresh_timer.invalidate()
    g_refresh_timer = Foundation.NSTimer\
        .scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            new_interval, delegate, "refreshPage:", None, True)
    # Reset the last-refresh time so the countdown shows the full new interval
    g_last_refresh_time = time.time()


def build_settings_menu(target):
    """Right-click context menu with toggleable settings + quit."""
    menu = AppKit.NSMenu.alloc().init()
    menu.setAutoenablesItems_(False)
    prefs = load_prefs()

    def add_toggle(title, sel, on):
        item = AppKit.NSMenuItem.alloc()\
            .initWithTitle_action_keyEquivalent_(
                title, objc.selector(sel, signature=b"v@:@"), "")
        item.setTarget_(target)
        item.setState_(
            AppKit.NSControlStateValueOn if on
            else AppKit.NSControlStateValueOff)
        menu.addItem_(item)

    add_toggle("Start on login",
               target.toggleAutostart_, is_autostart_enabled())
    add_toggle("Pin on top",
               target.togglePin_, prefs.get("pin_on_top", True))
    add_toggle("Show in menu bar",
               target.toggleMenubar_, prefs.get("show_menubar", False))
    add_toggle("Vertical layout",
               target.toggleVertical_, prefs.get("vertical", False))

    menu.addItem_(AppKit.NSMenuItem.separatorItem())

    refresh_item = AppKit.NSMenuItem.alloc()\
        .initWithTitle_action_keyEquivalent_(
            "Refresh Now",
            objc.selector(target.manualRefresh_, signature=b"v@:@"),
            "r")
    refresh_item.setTarget_(target)
    menu.addItem_(refresh_item)

    quit_item = AppKit.NSMenuItem.alloc()\
        .initWithTitle_action_keyEquivalent_(
            "Quit",
            objc.selector(target.closeWidget_, signature=b"v@:@"),
            "q")
    quit_item.setTarget_(target)
    menu.addItem_(quit_item)

    return menu


# ── Build the compact widget UI ──

# ── Click-to-show popover with extra metric details ──

g_popover = None


def _color_for_key(key):
    if key == "session":
        return NEON_BLUE
    if key == "all":
        return NEON_MAGENTA
    pct = g_last_data.get("nPct")
    if pct is None or pct < 50:
        return NEON_BLUE
    if pct < 80:
        return NEON_AMBER
    return NEON_PINK


def _stats_for_key(key):
    """Returns list of (label, value) tuples to display in the popover."""
    d = g_last_data or {}
    if key == "session":
        sReset = d.get("sReset", "")
        # Time-left + clock time, separately
        import re
        h_m = re.search(r"(\d+)\s*hr", sReset)
        m_m = re.search(r"(\d+)\s*min", sReset)
        h = int(h_m.group(1)) if h_m else 0
        m = int(m_m.group(1)) if m_m else 0
        rel = (f"{h}h {m}m" if h and m else
               f"{h}h" if h else f"{m}m" if m else "—")
        clock = ""
        if h or m:
            t = datetime.datetime.now() + datetime.timedelta(
                hours=h, minutes=m)
            hr12 = t.hour % 12 or 12
            ampm = "am" if t.hour < 12 else "pm"
            clock = f"{hr12}:{t.minute:02d}{ampm}"
        return [
            ("Plan", d.get("plan", "—")),
            ("Used", f"{d.get('sPct', '—')}%"),
            ("Time left", rel),
            ("Resets at", clock or "—"),
        ]
    if key == "all":
        return [
            ("Type", "Weekly limit"),
            ("Used", f"{d.get('aPct', '—')}%"),
            ("Resets", d.get("aReset", "—")),
        ]
    # sonnet
    return [
        ("Type", "Sonnet only"),
        ("Used", f"{d.get('nPct', '—')}%"),
        ("Resets", d.get("nReset", "—")),
    ]


class PopoverContentView(AppKit.NSView):
    """Dark techno-themed background for the metric details popover."""

    def initWithFrame_color_(self, frame, accent_rgb):
        self = objc.super(PopoverContentView, self).initWithFrame_(frame)
        self._accent = accent_rgb
        return self

    def drawRect_(self, rect):
        # Dark navy bg
        AppKit.NSColor.colorWithCalibratedRed_green_blue_alpha_(
            0.05, 0.06, 0.12, 1.0).setFill()
        AppKit.NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            rect, 8, 8).fill()
        # Pulsing accent line at the bottom
        phase = (time.time() * 1.6) % (2 * math.pi)
        pulse = 0.5 + 0.5 * math.sin(phase)
        line_w = rect.size.width - 24
        line_y = 8
        ar, ag, ab = self._accent
        accent = AppKit.NSColor.colorWithCalibratedRed_green_blue_alpha_(
            ar, ag, ab, 0.65 + 0.35 * pulse)
        accent.setFill()
        AppKit.NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            Foundation.NSMakeRect(12, line_y, line_w, 1.0),
            0.5, 0.5).fill()
        # Halo
        for r, base in ((3, 0.04), (2, 0.10), (1, 0.18)):
            halo_a = base + 0.08 * pulse
            halo_color = AppKit.NSColor.colorWithCalibratedRed_green_blue_alpha_(
                ar, ag, ab, halo_a)
            halo_color.setFill()
            AppKit.NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
                Foundation.NSMakeRect(
                    12 - r, line_y - r, line_w + r * 2, 1.0 + r * 2),
                r + 0.5, r + 0.5).fill()


def show_metric_popover(key, anchor_view):
    """Pop up a small details panel anchored to the clicked metric bar."""
    global g_popover

    accent = _color_for_key(key)
    stats = _stats_for_key(key)

    # Layout
    pad_x, pad_y = 14, 10
    title_h = 18
    row_h = 17
    bottom_pad = 18  # space for the pulsing accent line
    width = 240
    height = pad_y * 2 + title_h + 4 + len(stats) * row_h + bottom_pad

    content = PopoverContentView.alloc().initWithFrame_color_(
        Foundation.NSMakeRect(0, 0, width, height), accent)
    content.setWantsLayer_(True)

    # Title
    title_text = {
        "session": "SESSION",
        "all": "ALL MODELS",
        "sonnet": "SONNET ONLY",
    }[key]
    accent_color = AppKit.NSColor.colorWithCalibratedRed_green_blue_alpha_(
        accent[0], accent[1], accent[2], 1.0)
    title = AppKit.NSTextField.labelWithString_(title_text)
    title.setFont_(AppKit.NSFont.systemFontOfSize_(12))
    title.setTextColor_(accent_color)
    title.setFrame_(Foundation.NSMakeRect(
        pad_x, height - pad_y - title_h, width - pad_x * 2, title_h))
    content.addSubview_(title)

    # Stats rows
    y = height - pad_y - title_h - 4 - row_h
    for label, value in stats:
        l = AppKit.NSTextField.labelWithString_(label.upper())
        l.setFont_(AppKit.NSFont.systemFontOfSize_(8.5))
        l.setTextColor_(AppKit.NSColor.colorWithCalibratedWhite_alpha_(
            0.45, 1.0))
        l.setFrame_(Foundation.NSMakeRect(pad_x, y, 95, row_h - 2))
        content.addSubview_(l)

        v = AppKit.NSTextField.labelWithString_(str(value))
        v.setFont_(AppKit.NSFont.systemFontOfSize_(11))
        v.setTextColor_(AppKit.NSColor.whiteColor())
        v.setFrame_(Foundation.NSMakeRect(
            pad_x + 100, y, width - pad_x * 2 - 100, row_h - 2))
        content.addSubview_(v)
        y -= row_h

    # Build / reuse popover
    if g_popover and g_popover.isShown():
        g_popover.close()
    vc = AppKit.NSViewController.alloc().init()
    vc.setView_(content)

    g_popover = AppKit.NSPopover.alloc().init()
    g_popover.setContentViewController_(vc)
    g_popover.setContentSize_(Foundation.NSMakeSize(width, height))
    g_popover.setBehavior_(AppKit.NSPopoverBehaviorTransient)
    try:
        g_popover.setAppearance_(
            AppKit.NSAppearance.appearanceNamed_("NSAppearanceNameDarkAqua"))
    except Exception:
        pass
    g_popover.showRelativeToRect_ofView_preferredEdge_(
        anchor_view.bounds(), anchor_view, 3)  # 3 = NSMinYEdge (below)


def _make_btn(target, frame, glyph, action, font_size):
    btn = HoverButton.alloc().initWithFrame_(frame)
    btn.setBordered_(False)
    btn.setTarget_(target)
    btn.setAction_(objc.selector(action, signature=b"v@:@"))
    btn.setAttributedTitle_(
        AppKit.NSAttributedString.alloc().initWithString_attributes_(glyph, {
            AppKit.NSForegroundColorAttributeName:
                AppKit.NSColor.colorWithCalibratedWhite_alpha_(0.92, 1.0),
            AppKit.NSFontAttributeName:
                AppKit.NSFont.systemFontOfSize_(font_size),
        }))
    return btn


def build_compact_ui(target):
    """Single-row layout: drag tab + 3 bars + \u21bb \u00d7 buttons, all inline."""
    global g_bars, g_countdown
    g_bars = {}
    prefs = load_prefs()
    vertical = prefs.get("vertical", False)
    w, h = widget_dimensions()

    view = DraggableView.alloc().initWithFrame_target_(
        Foundation.NSMakeRect(0, 0, w, h), target)
    view.setWantsLayer_(True)
    view.layer().setCornerRadius_(6)
    view.layer().setMasksToBounds_(True)

    rows = [("session", "Session"), ("all", "All"), ("sonnet", "Sonnet")]

    if vertical:
        # Tall thin column. Width = horizontal widget's height.
        # Layout from top: drag tab, 3 bars stacked, 3 buttons stacked.
        col_x = V_PAD
        col_w = V_BAR_W

        # Drag tab on top
        tab_y = h - V_PAD - V_DRAG_H
        tab = DragTabView.alloc().initWithFrame_(
            Foundation.NSMakeRect(col_x, tab_y, col_w, V_DRAG_H))
        view.addSubview_(tab)

        # Stacked metric bars (compact \u2014 number only)
        bar_top = tab_y - 4
        for i, (key, label) in enumerate(rows):
            y = bar_top - (i + 1) * V_BAR_H - i * V_GAP
            bar = MetricBarView.alloc().initWithFrame_label_(
                Foundation.NSMakeRect(col_x, y, col_w, V_BAR_H), label)
            bar._key = key
            bar.setCompactMode_(True)
            view.addSubview_(bar)
            g_bars[key] = bar

        # Countdown row between bars and buttons
        countdown_h = 12
        cd_y = bar_top - V_BAR_H * 3 - V_GAP * 2 - 3 - countdown_h
        g_countdown = CountdownView.alloc().initWithFrame_(
            Foundation.NSMakeRect(col_x, cd_y, col_w, countdown_h))
        g_countdown.setCompactMode_(True)
        g_countdown.setSecondsLeft_(get_refresh_interval())
        view.addSubview_(g_countdown)

        # Stacked buttons at the bottom: rotate \u00b7 refresh \u00b7 close (top\u2192bottom)
        bot_top = cd_y - 3
        for i, (glyph, action, fs) in enumerate([
            ("\u21cc", target.toggleRotation_, 11),
            ("\u21bb", target.manualRefresh_, 12),
            ("\u00d7", target.closeWidget_, 14),
        ]):
            y = bot_top - (i + 1) * V_BTN_W - i * V_GAP
            view.addSubview_(_make_btn(
                target, Foundation.NSMakeRect(col_x, y, col_w, V_BTN_W),
                glyph, action, fs))
    else:
        # Drag tab on the left
        tab = DragTabView.alloc().initWithFrame_(
            Foundation.NSMakeRect(WIDGET_PAD, 0, DRAG_TAB_W, h))
        view.addSubview_(tab)

        # 3 bars in a row — session is wider, sonnet is narrower
        x = WIDGET_PAD + DRAG_TAB_W + 4
        bar_widths = {"session": SESSION_BAR_W,
                      "all": ALL_BAR_W,
                      "sonnet": SONNET_BAR_W}
        for key, label in rows:
            bw = bar_widths[key]
            bar = MetricBarView.alloc().initWithFrame_label_(
                Foundation.NSMakeRect(x, 0, bw, BAR_H), label)
            bar._key = key
            view.addSubview_(bar)
            g_bars[key] = bar
            x += bw + ROW_GAP

        # Countdown timer (clickable to change interval)
        g_countdown = CountdownView.alloc().initWithFrame_(
            Foundation.NSMakeRect(x, 0, COUNTDOWN_W, BAR_H))
        g_countdown.setSecondsLeft_(get_refresh_interval())
        view.addSubview_(g_countdown)
        x += COUNTDOWN_W

        # Buttons on the right: rotate \u00b7 refresh \u00b7 close
        btn_y = (h - BTN_W) / 2
        bx = x + 2
        view.addSubview_(_make_btn(
            target, Foundation.NSMakeRect(bx, btn_y, BTN_W, BTN_W),
            "\u21cc", target.toggleRotation_, 12))
        view.addSubview_(_make_btn(
            target, Foundation.NSMakeRect(bx + BTN_W, btn_y, BTN_W, BTN_W),
            "\u21bb", target.manualRefresh_, 13))
        view.addSubview_(_make_btn(
            target,
            Foundation.NSMakeRect(bx + BTN_W * 2, btn_y, BTN_W, BTN_W),
            "\u00d7", target.closeWidget_, 15))

    return view


# ── Menu bar status item ──

def build_status_menu(target):
    """Build the dropdown menu shown when the menu bar item is clicked."""
    global g_status_menu_items
    g_status_menu_items = {}

    menu = AppKit.NSMenu.alloc().init()
    menu.setAutoenablesItems_(False)

    for key, label in [("session", "Session"),
                       ("all", "All Models"),
                       ("sonnet", "Sonnet")]:
        item = AppKit.NSMenuItem.alloc()\
            .initWithTitle_action_keyEquivalent_(f"{label}: --", None, "")
        item.setEnabled_(False)
        menu.addItem_(item)
        g_status_menu_items[key] = item

    menu.addItem_(AppKit.NSMenuItem.separatorItem())

    refresh_item = AppKit.NSMenuItem.alloc()\
        .initWithTitle_action_keyEquivalent_(
            "Refresh Now",
            objc.selector(target.manualRefresh_, signature=b"v@:@"),
            "r")
    refresh_item.setTarget_(target)
    menu.addItem_(refresh_item)

    show_item = AppKit.NSMenuItem.alloc()\
        .initWithTitle_action_keyEquivalent_(
            "Show Widget",
            objc.selector(target.showWidget_, signature=b"v@:@"),
            "")
    show_item.setTarget_(target)
    menu.addItem_(show_item)

    menu.addItem_(AppKit.NSMenuItem.separatorItem())

    quit_item = AppKit.NSMenuItem.alloc()\
        .initWithTitle_action_keyEquivalent_(
            "Quit Claude Usage",
            objc.selector(target.closeWidget_, signature=b"v@:@"),
            "q")
    quit_item.setTarget_(target)
    menu.addItem_(quit_item)

    return menu


def _log(msg):
    try:
        with open("/tmp/claude_usage.log", "a") as f:
            f.write(f"[{datetime.datetime.now()}] {msg}\n")
    except Exception:
        pass


def set_menubar_visible(target, enabled):
    """Create or destroy the menu bar status item."""
    global g_status_item
    _log(f"set_menubar_visible({enabled}) g_status_item={g_status_item}")
    if enabled:
        if g_status_item:
            return
        bar = AppKit.NSStatusBar.systemStatusBar()
        g_status_item = bar.statusItemWithLength_(-1.0)  # variable length
        _log(f"  created status item: {g_status_item}")
        try:
            g_status_item.button().setTitle_("Claude")
            _log(f"  set title; visible={g_status_item.isVisible()}")
            menu = build_status_menu(target)
            g_status_item.setMenu_(menu)
            _log(f"  menu attached")
        except Exception as e:
            _log(f"  ERROR: {e}")
        if g_last_data:
            update_menubar(g_last_data)
    else:
        if g_status_item:
            AppKit.NSStatusBar.systemStatusBar()\
                .removeStatusItem_(g_status_item)
            g_status_item = None


def update_menubar(data):
    """Show the highest of the three percentages compactly in the menu bar."""
    global g_status_item, g_status_menu_items
    if not g_status_item:
        return

    pcts = [data.get(k) for k in ("sPct", "aPct", "nPct")]
    pcts = [p for p in pcts if p is not None]
    worst = max(pcts) if pcts else None

    if worst is None:
        plain = "—"
        color = AppKit.NSColor.colorWithCalibratedWhite_alpha_(0.6, 1.0)
    else:
        plain = f"{worst}%"
        color = menubar_color_for_pct(worst)

    font = AppKit.NSFont.monospacedDigitSystemFontOfSize_weight_(11, 0.0)
    title = AppKit.NSAttributedString.alloc()\
        .initWithString_attributes_(plain, {
            AppKit.NSForegroundColorAttributeName: color,
            AppKit.NSFontAttributeName: font,
        })
    g_status_item.button().setAttributedTitle_(title)
    _log(f"update_menubar: set title to '{plain}'")

    # Update dropdown menu items
    for key, pct_key, reset_key in [
        ("session", "sPct", "sReset"),
        ("all", "aPct", "aReset"),
        ("sonnet", "nPct", "nReset"),
    ]:
        if key in g_status_menu_items:
            pct = data.get(pct_key)
            if pct is not None:
                label = {"session": "Session",
                         "all": "All Models",
                         "sonnet": "Sonnet"}[key]
                reset = data.get(reset_key, "")
                prefix = "in " if key == "session" else ""
                title_str = f"{label}: {pct}%   ·   resets {prefix}{reset}"
                g_status_menu_items[key].setTitle_(title_str)


# ── State transitions ──

def _create_widget_window(target, origin=None):
    """Build (or rebuild) the desktop widget window using current prefs/dimensions."""
    global g_window
    _log(f"_create_widget_window starting")
    w, h = widget_dimensions()
    if origin is None:
        screen = AppKit.NSScreen.mainScreen().frame()
        # Vertical mode hugs the screen edge less aggressively (away from dock)
        prefs = load_prefs()
        right_offset = 120 if prefs.get("vertical") else 40
        x = screen.size.width - w - right_offset
        y = screen.size.height - h - 60
    else:
        x, y = origin
    frame = Foundation.NSMakeRect(x, y, w, h)

    g_window = KeyableWindow.alloc().initWithContentRect_styleMask_backing_defer_(
        frame,
        AppKit.NSWindowStyleMaskBorderless,
        AppKit.NSBackingStoreBuffered,
        False,
    )
    g_window.setOpaque_(False)
    g_window.setBackgroundColor_(AppKit.NSColor.clearColor())
    prefs = load_prefs()
    g_window.setLevel_(
        AppKit.NSFloatingWindowLevel if prefs.get("pin_on_top", True)
        else AppKit.NSNormalWindowLevel)
    g_window.setHasShadow_(True)
    g_window.setAlphaValue_(1.0)
    g_window.setMovableByWindowBackground_(True)
    g_window.setCollectionBehavior_(
        AppKit.NSWindowCollectionBehaviorCanJoinAllSpaces
        | AppKit.NSWindowCollectionBehaviorStationary
        | AppKit.NSWindowCollectionBehaviorIgnoresCycle
    )
    try:
        ui = build_compact_ui(target)
        _log(f"  built UI: {ui}")
        g_window.setContentView_(ui)
        g_window.orderFront_(None)
        _log(f"  widget window ordered front")
    except Exception as e:
        import traceback
        _log(f"  ERROR building widget UI: {e}\n{traceback.format_exc()}")


def switch_to_widget(target):
    """Close login window, show desktop widget."""
    global g_logged_in, g_login_window
    if g_logged_in:
        return
    g_logged_in = True
    if g_login_window:
        g_login_window.orderOut_(None)
    _create_widget_window(target)


def rebuild_widget_window():
    """Tear down + rebuild the widget window (used when toggling vertical layout)."""
    global g_window
    if not g_window:
        return
    old_origin = (g_window.frame().origin.x, g_window.frame().origin.y)
    g_window.orderOut_(None)
    g_window = None
    target = AppKit.NSApplication.sharedApplication().delegate()
    _create_widget_window(target, origin=old_origin)
    if g_last_data:
        update_ui(g_last_data)


def shorten_reset(text, is_session):
    """Compact reset time strings for tight display."""
    import re
    if not text:
        return ""
    if is_session:
        # "in 4 hr 49 min" → "4h 49m · 4:38am"
        h_m = re.search(r"(\d+)\s*hr", text)
        m_m = re.search(r"(\d+)\s*min", text)
        h = int(h_m.group(1)) if h_m else 0
        m = int(m_m.group(1)) if m_m else 0
        rel = ""
        if h and m:
            rel = f"{h}h {m}m"
        elif h:
            rel = f"{h}h"
        elif m:
            rel = f"{m}m"
        # Calculate clock time when the session resets
        clock = ""
        if h or m:
            t = datetime.datetime.now() + datetime.timedelta(hours=h, minutes=m)
            hr12 = t.hour % 12 or 12
            ampm = "am" if t.hour < 12 else "pm"
            clock = f"{hr12}:{t.minute:02d}{ampm}"
        if rel and clock:
            return f"{rel} · {clock}"
        return rel or clock or text
    # Weekly: "Sun 10:00 AM" → "Sun 10am"
    m = re.search(r"(\w+)\s*(\d+)(?::\d+)?\s*(AM|PM)", text)
    if m:
        return f"{m.group(1)} {int(m.group(2))}{m.group(3).lower()}"
    return text


def update_ui(data):
    global g_bars, g_last_data
    g_last_data = data

    # Fixed colors per metric: session = blue, all = magenta. Sonnet still
    # follows the percentage scale so it can warn when limit is approached.
    session_color = AppKit.NSColor.colorWithCalibratedRed_green_blue_alpha_(
        *NEON_BLUE, 1.0)
    all_color = AppKit.NSColor.colorWithCalibratedRed_green_blue_alpha_(
        *NEON_MAGENTA, 1.0)

    for key, pct_key, reset_key, is_session in [
        ("session", "sPct", "sReset", True),
        ("all", "aPct", "aReset", False),
        ("sonnet", "nPct", "nReset", False),
    ]:
        pct = data.get(pct_key)
        if pct is not None and key in g_bars:
            if key == "session":
                color = session_color
            elif key == "all":
                color = all_color
            else:
                color = color_for_pct(pct)
            reset = data.get(reset_key, "")
            short = shorten_reset(reset, is_session)
            g_bars[key].setData_color_resetText_(pct, color, short)

    update_menubar(data)


def do_extract():
    global g_webview

    def handle(result, error):
        if error or not result:
            return
        try:
            data = json.loads(result)
        except Exception:
            return
        if not data.get("loggedIn"):
            return
        delegate = AppKit.NSApplication.sharedApplication().delegate()
        switch_to_widget(delegate)
        update_ui(data)

    g_webview.evaluateJavaScript_completionHandler_(EXTRACT_JS, handle)


# ── ObjC delegates ──

class NavDelegate(AppKit.NSObject):
    def webView_didFinishNavigation_(self, webView, navigation):
        Foundation.NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            2.0, self, "onPageReady:", None, False)

    def onPageReady_(self, timer):
        do_extract()


class AppDelegate(AppKit.NSObject):
    def applicationDidFinishLaunching_(self, notification):
        global g_login_window, g_webview

        # Hidden WKWebView for data fetching (shared across login & widget)
        config = WebKit.WKWebViewConfiguration.alloc().init()
        config.setWebsiteDataStore_(WebKit.WKWebsiteDataStore.defaultDataStore())
        g_webview = WebKit.WKWebView.alloc().initWithFrame_configuration_(
            Foundation.NSMakeRect(0, 0, LOGIN_W, LOGIN_H), config)
        self._nav = NavDelegate.alloc().init()
        g_webview.setNavigationDelegate_(self._nav)

        # Login window (titled, closable — only shown if not logged in)
        screen = AppKit.NSScreen.mainScreen().frame()
        x = screen.size.width / 2 - LOGIN_W / 2
        y = screen.size.height / 2 - LOGIN_H / 2
        frame = Foundation.NSMakeRect(x, y, LOGIN_W, LOGIN_H)
        mask = (AppKit.NSWindowStyleMaskTitled
                | AppKit.NSWindowStyleMaskClosable
                | AppKit.NSWindowStyleMaskMiniaturizable)
        g_login_window = AppKit.NSWindow.alloc()\
            .initWithContentRect_styleMask_backing_defer_(
                frame, mask, AppKit.NSBackingStoreBuffered, False)
        g_login_window.setTitle_("Claude Usage — Sign In")
        g_login_window.setLevel_(AppKit.NSFloatingWindowLevel)
        g_login_window.setContentView_(g_webview)

        # Load usage page (will show login if needed)
        url = Foundation.NSURL.URLWithString_(USAGE_URL)
        g_webview.loadRequest_(Foundation.NSURLRequest.requestWithURL_(url))
        g_login_window.makeKeyAndOrderFront_(None)

        # Auto-refresh timer (interval is user-configurable via countdown click)
        global g_refresh_timer, g_last_refresh_time
        g_last_refresh_time = time.time()
        g_refresh_timer = Foundation.NSTimer\
            .scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
                get_refresh_interval(), self, "refreshPage:", None, True)

        # Pulse-redraw timer: ~30 fps redraw of all bars for the glowing line
        Foundation.NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            1.0 / 30.0, self, "tickPulse:", None, True)

        # Restore menu bar item if user had it enabled
        prefs = load_prefs()
        if prefs.get("show_menubar"):
            set_menubar_visible(self, True)

    def refreshPage_(self, timer):
        global g_webview, g_last_refresh_time
        if g_webview:
            url = Foundation.NSURL.URLWithString_(USAGE_URL)
            g_webview.loadRequest_(Foundation.NSURLRequest.requestWithURL_(url))
            g_last_refresh_time = time.time()

    def manualRefresh_(self, sender):
        self.refreshPage_(None)

    def tickPulse_(self, timer):
        # Redraw each metric bar so the glow keeps animating
        for bar in g_bars.values():
            bar.setNeedsDisplay_(True)
        # Update the refresh countdown
        if g_countdown is not None:
            interval = get_refresh_interval()
            secs_left = max(
                0,
                (g_last_refresh_time + interval) - time.time())
            g_countdown.setSecondsLeft_(secs_left)
        # Also pulse the popover (if shown)
        if g_popover and g_popover.isShown():
            v = g_popover.contentViewController().view()
            if v:
                v.setNeedsDisplay_(True)

    def toggleAutostart_(self, sender):
        # NSMenuItem state doesn't auto-toggle; flip it ourselves.
        new_state = sender.state() != AppKit.NSControlStateValueOn
        sender.setState_(
            AppKit.NSControlStateValueOn if new_state
            else AppKit.NSControlStateValueOff)
        set_autostart(new_state)

    def togglePin_(self, sender):
        global g_window
        new_state = sender.state() != AppKit.NSControlStateValueOn
        sender.setState_(
            AppKit.NSControlStateValueOn if new_state
            else AppKit.NSControlStateValueOff)
        prefs = load_prefs()
        prefs["pin_on_top"] = new_state
        save_prefs(prefs)
        if g_window:
            g_window.setLevel_(
                AppKit.NSFloatingWindowLevel if new_state
                else AppKit.NSNormalWindowLevel)

    def toggleMenubar_(self, sender):
        new_state = sender.state() != AppKit.NSControlStateValueOn
        sender.setState_(
            AppKit.NSControlStateValueOn if new_state
            else AppKit.NSControlStateValueOff)
        prefs = load_prefs()
        prefs["show_menubar"] = new_state
        save_prefs(prefs)
        set_menubar_visible(self, new_state)

    def toggleVertical_(self, sender):
        new_state = sender.state() != AppKit.NSControlStateValueOn
        sender.setState_(
            AppKit.NSControlStateValueOn if new_state
            else AppKit.NSControlStateValueOff)
        prefs = load_prefs()
        prefs["vertical"] = new_state
        save_prefs(prefs)
        rebuild_widget_window()

    def toggleRotation_(self, sender):
        # Triggered by the inline rotate (⇌) button on the widget itself.
        prefs = load_prefs()
        prefs["vertical"] = not prefs.get("vertical", False)
        save_prefs(prefs)
        rebuild_widget_window()

    def showWidget_(self, sender):
        global g_window
        if g_window:
            g_window.makeKeyAndOrderFront_(None)
            AppKit.NSApplication.sharedApplication()\
                .activateIgnoringOtherApps_(True)

    def closeWidget_(self, sender):
        AppKit.NSApplication.sharedApplication().terminate_(None)

    def applicationShouldTerminateAfterLastWindowClosed_(self, sender):
        return True


def main():
    app = AppKit.NSApplication.sharedApplication()
    # Accessory = no Dock icon, no menu bar
    app.setActivationPolicy_(AppKit.NSApplicationActivationPolicyAccessory)
    delegate = AppDelegate.alloc().init()
    app.setDelegate_(delegate)
    app.activateIgnoringOtherApps_(True)
    app.run()


if __name__ == "__main__":
    main()
