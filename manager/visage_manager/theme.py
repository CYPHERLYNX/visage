"""Visage Manager visual theme.

Apple-grade dark design language: near-black surfaces, one accent blue,
generous spacing, quiet typography. Every view reads its constants from
here so the whole app stays visually coherent.
"""

# --- Surfaces ---
BG = "#0B0B0D"            # window background (near black)
SURFACE = "#141416"       # sidebar / raised panels
CARD = "#1A1A1E"          # cards
CARD_HOVER = "#202026"
BORDER = "#2A2A30"        # hairline borders
DIVIDER = "#232328"

# --- Text ---
TEXT = "#F5F5F7"          # primary
TEXT_SECONDARY = "#A1A1A6"
TEXT_TERTIARY = "#6E6E73"

# --- Accent & status ---
ACCENT = "#0A84FF"        # Apple system blue
ACCENT_DIM = "#0A84FF33"
SUCCESS = "#30D158"
WARNING = "#FF9F0A"
DANGER = "#FF453A"

# --- Spacing (px) ---
PAD_PAGE = 32
PAD_CARD = 20
GAP = 16
GAP_SMALL = 8
RADIUS = 12
RADIUS_SMALL = 8

# --- Typography ---
# Prefer the platform's native UI font so it feels at home on Windows.
FONT_FAMILY = "Segoe UI, SF Pro Text, Inter, sans-serif"
TITLE_SIZE = 26
HEADING_SIZE = 17
BODY_SIZE = 13
CAPTION_SIZE = 11

APP_NAME = "Visage"
APP_SUBTITLE = "Face unlock for Windows"

STYLESHEET = f"""
* {{
    font-family: {FONT_FAMILY};
}}
QWidget {{
    background: {BG};
    color: {TEXT};
}}
QMainWindow, QDialog {{
    background: {BG};
}}
QLabel {{
    background: transparent;
}}
QLabel#pageTitle {{
    font-size: {TITLE_SIZE}px;
    font-weight: 600;
    color: {TEXT};
}}
QLabel#pageSubtitle {{
    font-size: {BODY_SIZE}px;
    color: {TEXT_SECONDARY};
}}
QLabel#sectionTitle {{
    font-size: {HEADING_SIZE}px;
    font-weight: 600;
    color: {TEXT};
}}
QLabel#caption {{
    font-size: {CAPTION_SIZE}px;
    color: {TEXT_SECONDARY};
}}
QLabel#hint {{
    font-size: {CAPTION_SIZE}px;
    color: {TEXT_TERTIARY};
}}
QLabel#feedbackWarn {{
    font-size: {BODY_SIZE}px;
    color: {WARNING};
}}
QLabel#feedbackOk {{
    font-size: {BODY_SIZE}px;
    color: {SUCCESS};
}}
QFrame#card {{
    background: {CARD};
    border: 1px solid {BORDER};
    border-radius: {RADIUS}px;
}}
QFrame#sidebar {{
    background: {SURFACE};
    border: none;
    border-right: 1px solid {DIVIDER};
}}
QPushButton {{
    background: {CARD_HOVER};
    color: {TEXT};
    border: 1px solid {BORDER};
    border-radius: {RADIUS_SMALL}px;
    padding: 9px 18px;
    font-size: {BODY_SIZE}px;
}}
QPushButton:hover {{
    background: #28282E;
}}
QPushButton:pressed {{
    background: #303036;
}}
QPushButton#primary {{
    background: {ACCENT};
    border: none;
    color: white;
    font-weight: 600;
}}
QPushButton#primary:hover {{
    background: #2B93FF;
}}
QPushButton#primary:pressed {{
    background: #0070E0;
}}
QPushButton#danger {{
    background: transparent;
    border: 1px solid #5A2A28;
    color: {DANGER};
}}
QPushButton#danger:hover {{
    background: #2A1A18;
}}
QPushButton#ghost {{
    background: transparent;
    border: none;
    color: {ACCENT};
    padding: 6px 10px;
}}
QPushButton#ghost:hover {{
    background: {ACCENT_DIM};
}}
QPushButton:disabled {{
    color: {TEXT_TERTIARY};
    background: {CARD};
}}
QLineEdit {{
    background: #101013;
    border: 1px solid {BORDER};
    border-radius: {RADIUS_SMALL}px;
    padding: 10px 12px;
    font-size: {BODY_SIZE}px;
    color: {TEXT};
    selection-background-color: {ACCENT};
}}
QLineEdit:focus {{
    border: 1px solid {ACCENT};
}}
QComboBox {{
    background: #101013;
    border: 1px solid {BORDER};
    border-radius: {RADIUS_SMALL}px;
    padding: 8px 12px;
    font-size: {BODY_SIZE}px;
    color: {TEXT};
}}
QComboBox QAbstractItemView {{
    background: {CARD};
    border: 1px solid {BORDER};
    selection-background-color: {ACCENT};
    color: {TEXT};
}}
QCheckBox, QRadioButton {{
    font-size: {BODY_SIZE}px;
    color: {TEXT};
    spacing: 8px;
}}
QSlider::groove:horizontal {{
    height: 4px;
    background: {BORDER};
    border-radius: 2px;
}}
QSlider::handle:horizontal {{
    width: 16px;
    height: 16px;
    margin: -6px 0;
    border-radius: 8px;
    background: {TEXT};
}}
QSlider::sub-page:horizontal {{
    background: {ACCENT};
    border-radius: 2px;
}}
QProgressBar {{
    background: {BORDER};
    border: none;
    border-radius: 3px;
    height: 6px;
    text-align: center;
}}
QProgressBar::chunk {{
    background: {ACCENT};
    border-radius: 3px;
}}
QScrollBar:vertical {{
    background: transparent;
    width: 10px;
}}
QScrollBar::handle:vertical {{
    background: #3A3A40;
    border-radius: 5px;
    min-height: 30px;
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0;
}}
QScrollArea {{
    border: none;
    background: transparent;
}}
QToolTip {{
    background: {CARD};
    color: {TEXT};
    border: 1px solid {BORDER};
    padding: 6px;
}}
QMessageBox {{
    background: {SURFACE};
}}
QTabWidget::pane {{
    border: none;
}}
QTabBar::tab {{
    background: transparent;
    color: {TEXT_SECONDARY};
    padding: 8px 16px;
    font-size: {BODY_SIZE}px;
}}
QTabBar::tab:selected {{
    color: {TEXT};
    border-bottom: 2px solid {ACCENT};
}}
QTextEdit#log {{
    background: #0E0E10;
    border: 1px solid {BORDER};
    border-radius: {RADIUS_SMALL}px;
    font-family: Consolas, monospace;
    font-size: {CAPTION_SIZE}px;
    color: {TEXT_SECONDARY};
}}
"""
