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

# Single-row layout: drag tab + 4 bars + countdown + ⇌ ↻ × buttons all inline.
# Half-height compact row: smaller fonts let BAR_H drop from 20 to 11.
BAR_H = 11
BAR_FONT = 7.5        # metric label font (was 9.5)
RESET_FONT = 6.5      # reset / detail font (was 8.5)
WIDGET_PAD = 4
ROW_GAP = 2
DRAG_TAB_W = 8
BTN_W = 11

# Session/All keep room for the long reset string "4h 10m · 4:39am". Sonnet is
# usually 0% with no reset line, so it can be much narrower. Disk shows
# "DISK 42%" + "320/994G".
SESSION_BAR_W = 172
ALL_BAR_W = 150
SONNET_BAR_W = 90
DISK_BAR_W = 122
COUNTDOWN_W = 40

NUM_METRICS = 4       # session, all, sonnet, disk

# Horizontal: [pad][tab][session][all][sonnet][disk][⟲][⇌↻×][pad]
H_WIDGET_W = (WIDGET_PAD + DRAG_TAB_W + 4
              + SESSION_BAR_W + ROW_GAP
              + ALL_BAR_W + ROW_GAP
              + SONNET_BAR_W + ROW_GAP
              + DISK_BAR_W + ROW_GAP
              + COUNTDOWN_W
              + 4 + BTN_W * 3 + WIDGET_PAD)
H_WIDGET_H = BAR_H

# Refresh-interval choices (seconds) cycled by clicking the countdown
REFRESH_INTERVAL_CHOICES = [60, 300, 900, 1800]  # 1m, 5m, 15m, 30m

# Vertical (stacked) — long & thin column.
V_PAD = 2
V_DRAG_H = 6
V_GAP = 2
V_BAR_H = 14          # bar (metric) height
V_BTN_W = 16          # button square size in vertical mode
V_BAR_W = 16          # bar width in the vertical column
V_WIDGET_W = 20       # column width
V_WIDGET_H = (V_PAD + V_DRAG_H + 4
              + V_BAR_H * NUM_METRICS + V_GAP * (NUM_METRICS - 1)
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

# claude.ai/settings/usage is a lazy-loading SPA. The usage panel shows
# "Loading" placeholders for a few seconds, then renders text like:
#   Current session / Resets in 3 hr 8 min / 23% used
#   Weekly limits
#     All models    / Resets Tue 11:00 AM   / 3% used
#     Fable         / Resets Tue 11:00 AM   / 31% used   (model-specific; the
#                                                          2nd weekly metric was
#                                                          "Sonnet only", now
#                                                          "Fable" — it changes)
# Session + All models are matched by their fixed labels. The 2nd weekly metric
# is parsed generically (its label is a model name that changes over time), so
# we return its name too and the widget relabels that bar dynamically.
EXTRACT_JS = r"""
(function() {
    var data = {loggedIn: false};
    var body = document.body ? document.body.innerText : '';
    if (body.indexOf('% used') === -1) return JSON.stringify(data);
    data.loggedIn = true;
    function section(label, stops) {
        var i = body.indexOf(label);
        if (i === -1) return null;
        var rest = body.substring(i + label.length);
        var cut = rest.length;
        for (var k = 0; k < stops.length; k++) {
            var j = rest.indexOf(stops[k]);
            if (j !== -1 && j < cut) cut = j;
        }
        rest = rest.substring(0, cut);
        var pm = rest.match(/(\d+)%\s*used/);
        var rm = rest.match(/Resets(?:\s+in)?\s+([^\n]+)/);
        return {pct: pm ? parseInt(pm[1], 10) : null,
                reset: rm ? rm[1].trim() : ""};
    }
    var sess = section('Current session',
        ['All models', 'Weekly limits', 'Last updated']);
    if (sess && sess.pct !== null) { data.sPct = sess.pct; data.sReset = sess.reset; }
    var all = section('All models',
        ['Last updated', 'Additional features', 'Usage credits']);
    if (all && all.pct !== null) { data.aPct = all.pct; data.aReset = all.reset; }

    // Second weekly metric — model-specific label (Sonnet, Fable, ...). Parse
    // the weekly block by lines: each metric is [Name][Resets.../You haven't][N% used].
    var wl = body.indexOf('Weekly limits');
    var lu = body.indexOf('Last updated');
    if (wl !== -1) {
        var region = body.substring(wl, lu !== -1 ? lu : wl + 700);
        var lines = region.split('\n')
            .map(function(s) { return s.trim(); })
            .filter(function(s) { return s.length; });
        for (var i = 2; i < lines.length; i++) {
            var m = lines[i].match(/^(\d+)%\s*used$/);
            if (!m) continue;
            var nm = lines[i - 2];
            if (nm === 'All models' || /^Learn more/.test(nm)
                || /^Weekly limits/.test(nm)) continue;
            data.nName = nm;
            data.nPct = parseInt(m[1], 10);
            data.nReset = /^Resets/.test(lines[i - 1])
                ? lines[i - 1].replace(/^Resets(\s+in)?\s+/, '') : "";
            break;
        }
    }
    var pm = body.match(/Max \(([^)]+)\)/);
    if (pm) data.plan = pm[1];
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
g_disk = {}                # last computed disk-usage snapshot
g_countdown = None         # CountdownView instance
g_last_refresh_time = 0.0  # time.time() of last successful page reload
g_refresh_timer = None     # Foundation.NSTimer for auto-refresh
g_extract_timer = None     # repeating timer that polls for data after a load
g_extract_tries = 0        # poll attempts since the last page load
g_reload_retries = 0       # reloads triggered because a load surfaced no data


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
NEON_TEAL    = (0.10, 0.90, 0.72)  # #1AE6B8 — disk / storage


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
#
# IMPORTANT: macOS 26 does not composite custom NSView drawRect_ output for
# subviews of a borderless, transparent window — only AppKit controls
# (NSButton, NSTextField) and layer-backed views with a backgroundColor render.
# So the widget's content is built from those primitives, not from drawRect_.


def _clear_label(font_size, white=1.0):
    """A non-interactive NSTextField label with a transparent background."""
    tf = AppKit.NSTextField.labelWithString_("")
    tf.setFont_(AppKit.NSFont.systemFontOfSize_(font_size))
    tf.setTextColor_(AppKit.NSColor.colorWithCalibratedWhite_alpha_(white, 1.0))
    tf.setDrawsBackground_(False)
    tf.setBezeled_(False)
    tf.setEditable_(False)
    tf.setSelectable_(False)
    return tf


def _ns_cgcolor(nscolor):
    return nscolor.CGColor()


class MetricBarView(AppKit.NSView):
    """Single-line metric: a dark rounded track, a colored progress line, and
    text — all as AppKit subviews so they composite on macOS 26."""

    def initWithFrame_label_(self, frame, label):
        self = objc.super(MetricBarView, self).initWithFrame_(frame)
        if self is None:
            return None
        self._label = label
        self._key = None
        self._pct = None
        self._resetText = ""
        self._compact = False     # vertical mode = compact (percentage only)
        self._barColor = AppKit.NSColor.colorWithCalibratedRed_green_blue_alpha_(
            *NEON_BLUE, 1.0)
        w, h = frame.size.width, frame.size.height

        # Dark rounded track slot
        self._track = AppKit.NSView.alloc().initWithFrame_(
            Foundation.NSMakeRect(0, 0, w, h))
        self._track.setWantsLayer_(True)
        self._track.setAutoresizingMask_(
            AppKit.NSViewWidthSizable | AppKit.NSViewHeightSizable)
        self._track.layer().setCornerRadius_(4)
        self._track.layer().setBackgroundColor_(_ns_cgcolor(
            AppKit.NSColor.colorWithCalibratedRed_green_blue_alpha_(
                0.04, 0.06, 0.13, 1.0)))
        self.addSubview_(self._track)

        # Colored progress line along the bottom edge
        self._line = AppKit.NSView.alloc().initWithFrame_(
            Foundation.NSMakeRect(5, 1, 0, 2))
        self._line.setWantsLayer_(True)
        self._line.layer().setCornerRadius_(1)
        self.addSubview_(self._line)

        # Text labels (left = label+pct, right = reset time)
        self._textLabel = _clear_label(BAR_FONT)
        self.addSubview_(self._textLabel)
        self._resetLabel = _clear_label(RESET_FONT, white=0.98)
        self._resetLabel.setAlignment_(AppKit.NSTextAlignmentRight)
        self.addSubview_(self._resetLabel)

        self._layoutLabels()
        self._refresh()   # show the "—" placeholder until data arrives
        return self

    def _layoutLabels(self):
        b = self.bounds()
        h = b.size.height
        if self._compact:
            self._textLabel.setAlignment_(AppKit.NSTextAlignmentCenter)
            self._textLabel.setFont_(
                AppKit.NSFont.monospacedDigitSystemFontOfSize_weight_(
                    BAR_FONT, 0.0))
            self._textLabel.setFrame_(
                Foundation.NSMakeRect(0, (h - 10) / 2.0, b.size.width, 10))
            self._resetLabel.setHidden_(True)
        else:
            self._textLabel.setAlignment_(AppKit.NSTextAlignmentLeft)
            self._textLabel.setFont_(AppKit.NSFont.systemFontOfSize_(BAR_FONT))
            self._textLabel.setFrame_(
                Foundation.NSMakeRect(6, 0, b.size.width - 12, h))
            self._resetLabel.setHidden_(False)
            self._resetLabel.setFrame_(
                Foundation.NSMakeRect(6, 0, b.size.width - 12, h))

    def setCompactMode_(self, compact):
        self._compact = compact
        self._layoutLabels()
        self._refresh()

    def setData_color_resetText_(self, pct, color, resetText):
        self._pct = pct
        self._barColor = color
        self._resetText = resetText
        self.setToolTip_(resetText or "")
        self._refresh()

    def _refresh(self):
        pct = self._pct
        if pct is not None and pct > 0:
            track_w = self.bounds().size.width - 10
            line_w = max(2, track_w * pct / 100.0)
            self._line.setFrame_(Foundation.NSMakeRect(5, 1, line_w, 2))
            self._line.layer().setBackgroundColor_(_ns_cgcolor(self._barColor))
            self._line.setHidden_(False)
        else:
            self._line.setHidden_(True)
        if self._compact:
            self._textLabel.setStringValue_(
                str(pct) if pct is not None else "—")
        else:
            pct_text = f"{pct}%" if pct is not None else "—"
            self._textLabel.setStringValue_(f"{self._label.upper()} {pct_text}")
            self._resetLabel.setStringValue_(self._resetText or "")

    def hitTest_(self, point):
        # Route clicks (even over the child labels) to this view so the detail
        # popover fires.
        if Foundation.NSPointInRect(point, self.frame()):
            return self
        return None

    def mouseDown_(self, event):
        if self._key:
            show_metric_popover(self._key, self)


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
    """Small grip area on the left edge — drags the window when clicked.
    Layer-backed with three grip dots drawn as tiny sublayers (drawRect_ would
    not composite on macOS 26)."""

    def initWithFrame_(self, frame):
        self = objc.super(DragTabView, self).initWithFrame_(frame)
        if self is None:
            return None
        self.setWantsLayer_(True)
        cx = frame.size.width / 2.0
        cy = frame.size.height / 2.0
        dot_color = _ns_cgcolor(
            AppKit.NSColor.colorWithCalibratedWhite_alpha_(0.45, 1.0))
        offsets = (-5, 0, 5) if frame.size.height >= frame.size.width \
            else (-5, 0, 5)
        for d in offsets:
            dot = AppKit.NSView.alloc().initWithFrame_(
                Foundation.NSMakeRect(cx - 1, cy + d - 1, 2, 2))
            dot.setWantsLayer_(True)
            dot.layer().setCornerRadius_(1)
            dot.layer().setBackgroundColor_(dot_color)
            self.addSubview_(dot)
        return self

    def mouseDown_(self, event):
        self.window().performWindowDragWithEvent_(event)


class CountdownView(AppKit.NSView):
    """Mini timer showing seconds until next refresh. Click cycles interval.
    Built from a layer-backed track + an NSTextField (not drawRect_)."""

    def initWithFrame_(self, frame):
        self = objc.super(CountdownView, self).initWithFrame_(frame)
        if self is None:
            return None
        self._secondsLeft = 0
        self._compact = False
        self.setWantsLayer_(True)
        self.layer().setCornerRadius_(4)
        self.layer().setBackgroundColor_(_ns_cgcolor(
            AppKit.NSColor.colorWithCalibratedRed_green_blue_alpha_(
                0.04, 0.06, 0.13, 1.0)))
        self._label = _clear_label(BAR_FONT, white=0.85)
        self._label.setFont_(
            AppKit.NSFont.monospacedDigitSystemFontOfSize_weight_(BAR_FONT, 0.0))
        self._label.setAlignment_(AppKit.NSTextAlignmentCenter)
        self._label.setFrame_(Foundation.NSMakeRect(
            0, (frame.size.height - 10) / 2.0, frame.size.width, 10))
        self._label.setAutoresizingMask_(AppKit.NSViewWidthSizable)
        self.addSubview_(self._label)
        self._render()
        return self

    def setCompactMode_(self, compact):
        self._compact = compact
        self._label.setFont_(AppKit.NSFont.monospacedDigitSystemFontOfSize_weight_(
            6.5 if compact else BAR_FONT, 0.0))
        self._render()

    def setSecondsLeft_(self, s):
        self._secondsLeft = max(0, int(s))
        self._render()

    def _render(self):
        m, s = divmod(self._secondsLeft, 60)
        self._label.setStringValue_(
            f"{m}:{s:02d}" if self._compact else f"⟲ {m}:{s:02d}")

    def mouseDown_(self, event):
        cycle_refresh_interval()


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
    if key == "disk":
        pct = g_disk.get("pct", 0)
        return NEON_PINK if pct >= 85 else NEON_TEAL
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
    if key == "disk":
        di = g_disk or {}
        return [
            ("Volume", "Macintosh HD"),
            ("Used", f"{di.get('pct', '—')}%"),
            ("Used space", _fmt_gb(di["used"]) if di.get("used") else "—"),
            ("Free", _fmt_gb(di["free"]) if di.get("free") else "—"),
            ("Total", _fmt_gb(di["total"]) if di.get("total") else "—"),
        ]
    # second weekly metric (model-specific: Sonnet, Fable, ...)
    return [
        ("Model", d.get("nName", "—")),
        ("Type", "Weekly limit"),
        ("Used", f"{d.get('nPct', '—')}%"),
        ("Resets", d.get("nReset") or "—"),
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
    if key == "sonnet":
        title_text = (g_last_data.get("nName") or "Sonnet").upper()
    else:
        title_text = {
            "session": "SESSION",
            "all": "ALL MODELS",
            "disk": "DISK SPACE",
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
    # NOTE: deliberately NOT layer-backed. On macOS 26 a layer-backed,
    # masksToBounds container in a borderless transparent window stops the
    # non-layer-backed custom subviews (bars, countdown, grip) from
    # compositing their drawRect_ output. Classic (non-layer) drawing works,
    # and the rounded look still comes from DraggableView.drawRect_.

    rows = [("session", "Session"), ("all", "All"),
            ("sonnet", "Sonnet"), ("disk", "Disk")]

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
        cd_y = (bar_top - V_BAR_H * NUM_METRICS - V_GAP * (NUM_METRICS - 1)
                - 3 - countdown_h)
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

        # Bars in a row — session/all wide, sonnet narrow, then disk.
        x = WIDGET_PAD + DRAG_TAB_W + 4
        bar_widths = {"session": SESSION_BAR_W,
                      "all": ALL_BAR_W,
                      "sonnet": SONNET_BAR_W,
                      "disk": DISK_BAR_W}
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
            "\u21cc", target.toggleRotation_, 9))
        view.addSubview_(_make_btn(
            target, Foundation.NSMakeRect(bx + BTN_W, btn_y, BTN_W, BTN_W),
            "\u21bb", target.manualRefresh_, 10))
        view.addSubview_(_make_btn(
            target,
            Foundation.NSMakeRect(bx + BTN_W * 2, btn_y, BTN_W, BTN_W),
            "\u00d7", target.closeWidget_, 11))

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
                # 2nd weekly metric is model-specific — relabel it (Fable, etc.)
                name = (data.get("nName") or "Sonnet").replace(" only", "")
                g_bars[key]._label = name
            reset = data.get(reset_key, "")
            short = shorten_reset(reset, is_session)
            g_bars[key].setData_color_resetText_(pct, color, short)

    update_disk()
    update_menubar(data)


def _fmt_gb(nbytes):
    # Base-10 units to match how macOS reports storage ("About This Mac").
    gb = nbytes / (1000 ** 3)
    if gb >= 1000:
        return f"{gb / 1000:.1f}T"
    return f"{gb:.0f}G"


def get_disk_info(path="/"):
    """Return (used_pct, 'used/total' string, detail dict) for `path`'s volume."""
    import shutil
    total, used, free = shutil.disk_usage(path)
    pct = int(round(used / total * 100)) if total else 0
    return pct, f"{_fmt_gb(used)}/{_fmt_gb(total)}", {
        "used": used, "free": free, "total": total, "pct": pct}


def update_disk():
    """Refresh the local disk-usage bar (independent of the web scrape)."""
    global g_disk
    if "disk" not in g_bars:
        return
    try:
        pct, label, info = get_disk_info("/")
    except Exception:
        return
    g_disk = info
    if pct >= 85:
        color = color_for_pct(pct)          # warn amber/red when nearly full
    else:
        color = AppKit.NSColor.colorWithCalibratedRed_green_blue_alpha_(
            *NEON_TEAL, 1.0)
    g_bars["disk"].setData_color_resetText_(pct, color, label)


def do_extract(on_done=None):
    """Scrape the loaded page. Calls on_done(success) where success is True only
    once real usage data (at least one percentage) was extracted — the SPA shows
    "Loading" placeholders for a few seconds, so early attempts legitimately fail
    and should be retried by the caller."""
    global g_webview

    def handle(result, error):
        ok = False
        if not error and result:
            try:
                data = json.loads(result)
            except Exception:
                data = None
            if data and data.get("loggedIn") and any(
                    data.get(k) is not None
                    for k in ("sPct", "aPct", "nPct")):
                global g_reload_retries
                g_reload_retries = 0
                delegate = AppKit.NSApplication.sharedApplication().delegate()
                switch_to_widget(delegate)
                update_ui(data)
                ok = True
        if on_done is not None:
            on_done(ok)

    if g_webview is not None:
        g_webview.evaluateJavaScript_completionHandler_(EXTRACT_JS, handle)
    elif on_done is not None:
        on_done(False)


# ── ObjC delegates ──

class NavDelegate(AppKit.NSObject):
    # The usage panel lazy-loads after the document finishes, so we poll a few
    # times rather than extracting once at a fixed delay (which used to race the
    # load and usually lose, leaving the bars empty).
    POLL_INTERVAL = 1.5
    MAX_TRIES = 15   # ~22s of polling — comfortably longer than the load time
    MAX_RELOADS = 6  # if a cold load is slow, reload a few times before idling

    def webView_didFinishNavigation_(self, webView, navigation):
        global g_extract_timer, g_extract_tries
        g_extract_tries = 0
        if g_extract_timer is not None:
            g_extract_timer.invalidate()
        g_extract_timer = Foundation.NSTimer\
            .scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
                self.POLL_INTERVAL, self, "pollExtract:", None, True)

    def pollExtract_(self, timer):
        global g_extract_tries, g_extract_timer
        g_extract_tries += 1
        tries = g_extract_tries

        def done(ok):
            global g_extract_timer, g_reload_retries
            if ok:
                if g_extract_timer is not None:
                    g_extract_timer.invalidate()
                    g_extract_timer = None
            elif tries >= self.MAX_TRIES:
                if g_extract_timer is not None:
                    g_extract_timer.invalidate()
                    g_extract_timer = None
                # The page didn't surface usage data within the poll window
                # (slow cold load). Reload to retry promptly rather than waiting
                # for the next full refresh interval — bounded so a genuinely
                # logged-out session doesn't reload forever.
                if g_reload_retries < self.MAX_RELOADS and g_webview is not None:
                    g_reload_retries += 1
                    url = Foundation.NSURL.URLWithString_(USAGE_URL)
                    g_webview.loadRequest_(
                        Foundation.NSURLRequest.requestWithURL_(url))

        do_extract(done)


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
