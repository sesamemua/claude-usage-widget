# Claude Usage Widget

A small, electrifying macOS desktop widget that shows your [claude.ai](https://claude.ai) usage at a glance.

![preview](docs/preview.png)

## Features

- **Always-visible at-a-glance stats** — Session %, weekly All-Models %, and weekly Sonnet-only %
- **Pulsing neon glow lines** — vivid electric blue, magenta, and warning amber for high usage
- **One-click drill-down** — click any metric for a popover with full details (plan, time-left, exact reset time, etc.)
- **Calculated reset clock time** — Session bar shows e.g. `4h 5m · 4:39am` so you know exactly when you regain capacity
- **Horizontal or vertical layout** — toggle with the inline rotate button (⇌)
- **Optional menu-bar mode** — also shows the highest current usage as a colored % in the macOS menu bar
- **Drag to reposition** — grip dots on the side
- **Login built-in** — first launch opens claude.ai in an embedded WebView; session persists in the system keychain
- **Borderless, translucent, dark techno theme** — sits politely on your desktop
- **Auto-start on login** (optional, configurable from the right-click menu)

## Install

Requires macOS 12+ and a Claude.ai account.

```bash
git clone https://github.com/<your-username>/claude-usage-widget.git
cd claude-usage-widget
./install.sh
```

The installer will:
1. Install the required Python dependencies (`pyobjc-framework-WebKit`, `pyobjc-framework-Cocoa`)
2. Build a `.app` bundle and copy it into `/Applications`
3. Launch it for the first time so you can sign in

After install, you can run it from Spotlight, Launchpad, or Finder → Applications → "Claude Usage".

## Manual run (no install)

If you'd rather just try it without installing:

```bash
pip3 install pyobjc-framework-WebKit pyobjc-framework-Cocoa
python3 claude_usage_widget.py
```

## Settings

Right-click anywhere on the widget for a context menu:

- **Start on login** — installs a `LaunchAgent` so the widget runs at every login
- **Pin on top** — keep above all windows (off = sits with regular windows)
- **Show in menu bar** — adds a colored percentage to the macOS menu bar
- **Vertical layout** — same toggle as the inline ⇌ button
- **Refresh Now** (⌘R) — forces a re-fetch
- **Quit** (⌘Q)

## How it works

The widget is a small Python AppKit / WebKit script. On first launch it loads `claude.ai/settings/usage` in a hidden `WKWebView`. After you sign in, the page is scraped via JavaScript every 5 minutes and the parsed values drive the visualization.

No data leaves your machine. Cookies are stored by macOS WebKit in the standard system data store — same as Safari.

## Limitations

- macOS only. Anchored on AppKit / PyObjC, so no Linux/Windows port planned.
- Scraping is best-effort. If Anthropic changes the page structure, the regex parsing may break — file an issue.
- Not affiliated with Anthropic. "Claude" is a trademark of Anthropic, PBC.

## License

[MIT](LICENSE) — do whatever, no warranty.
