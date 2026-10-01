"""Shared visual tokens for the native RemoteVibecode desktop UI.

The palette and hierarchy follow ``design/bridge-ui/dashboard-concept.png``.
Tk widgets are laid out responsively in :mod:`agent.gui`; text is never baked
into a bitmap, so system DPI changes do not shrink the interface copy.
"""

COLORS = {
    "bg": "#080e1a",
    "sidebar": "#0d1525",
    "panel": "#10192b",
    "panel_raised": "#142039",
    "panel_hover": "#1a2a49",
    "edge": "#26334c",
    "white": "#f8f9fe",
    "muted": "#a8b9dc",
    "dim": "#8294b8",
    "blue": "#536dff",
    "blue_hover": "#687fff",
    "purple": "#783dff",
    "cyan": "#00d3e9",
    "cyan_dark": "#136b88",
    "green": "#14dca4",
    "error_bg": "#3b2032",
    "error_edge": "#824050",
    "error_fg": "#ffc0bf",
    "focus": "#8beaff",
}

FONT = "Segoe UI"
TYPE = {
    "display": 28,
    "page": 26,
    "section": 16,
    "body": 11,
    "small": 9,
    "button": 10,
    "mono": 10,
}

SPACE = {
    "xs": 4,
    "sm": 8,
    "md": 12,
    "lg": 18,
    "xl": 24,
    "xxl": 32,
}
