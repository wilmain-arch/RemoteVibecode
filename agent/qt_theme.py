"""Focus design tokens and shared Qt widget styling."""

LIGHT = dict(
    bg="#F8F9F7",
    low="#F2F4F0",
    tonal="#EBEEEA",
    text="#202321",
    muted="#565D58",
    accent="#247661",
    selected="#E5F1EA",
    error="#A7352D",
)
DARK = dict(
    bg="#141715",
    low="#1A1F1B",
    tonal="#242A25",
    text="#F2F4F1",
    muted="#A0A8A2",
    accent="#90D4BB",
    selected="#20382E",
    error="#FFB4AB",
)


def stylesheet(c):
    return f"""
    QWidget {{ color:{c["text"]}; background:{c["bg"]}; font-size:14px; }}
    QFrame#sidebar {{ background:{c["low"]}; }}
    QFrame#sidebar QLabel, QFrame#sidebar QPushButton {{ background:transparent; }}
    QLabel[role="title"] {{ font-size:28px; font-weight:bold; }}
    QLabel[role="section"] {{ font-size:18px; font-weight:bold; }}
    QLabel[role="heroTitle"] {{ font-size:22px; font-weight:bold; }}
    QLabel[role="muted"] {{ color:{c["muted"]}; font-size:13px; }}
    QLabel[role="accent"] {{ color:{c["accent"]}; font-size:13px; }}
    QLabel[role="error"] {{ color:{c["error"]}; }}
    QFrame[role="segmented"] {{ background:{c["tonal"]}; border-radius:14px; }}
    QFrame[role="hero"] {{ background:{c["tonal"]}; border-radius:16px; }}
    QFrame[role="hero"] QWidget {{ background:transparent; }}
    QPushButton {{ border:0; border-radius:12px; padding:10px 16px; min-height:24px; font-weight:bold; background:transparent; color:{c["accent"]}; }}
    QPushButton:hover {{ background:{c["tonal"]}; }}
    QPushButton:pressed {{ background:{c["selected"]}; }}
    QPushButton:focus, QLineEdit:focus {{ border:2px solid {c["accent"]}; }}
    QPushButton[role="primary"] {{ background:{c["text"]}; color:{c["bg"]}; }}
    QPushButton[role="primary"]:hover {{ background:{c["muted"]}; }}
    QPushButton[role="danger"] {{ color:{c["error"]}; }}
    QPushButton:disabled {{ color:{c["muted"]}; background:{c["tonal"]}; }}
    QPushButton[role="nav"] {{ text-align:left; color:{c["muted"]}; padding:12px; min-height:24px; font-weight:400; }}
    QFrame#sidebar QPushButton[role="nav"]:checked {{ background:{c["selected"]}; color:{c["text"]}; font-weight:bold; }}
    QLineEdit {{ background:{c["tonal"]}; border:2px solid transparent; border-radius:12px; padding:10px 12px; min-height:24px; selection-background-color:{c["accent"]}; }}
    QLineEdit[invalid="true"] {{ border-color:{c["error"]}; }}
    QTextBrowser {{ border:0; background:transparent; selection-background-color:{c["accent"]}; }}
    QScrollArea {{ border:0; }}
    QScrollBar:vertical {{ background:transparent; width:8px; margin:0; }}
    QScrollBar::handle:vertical {{ background:{c["tonal"]}; border-radius:4px; min-height:32px; }}
    QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical {{ height:0; }}
    QScrollBar::add-page:vertical,QScrollBar::sub-page:vertical {{ background:transparent; }}
    QProgressBar {{ border:0; border-radius:3px; background:{c["tonal"]}; height:6px; max-height:6px; }}
    QProgressBar::chunk {{ background:{c["accent"]}; border-radius:3px; }}
    QToolTip {{ background:{c["tonal"]}; color:{c["text"]}; border:0; padding:8px; }}
    """
