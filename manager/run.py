#!/usr/bin/env python3
"""Launch Mien Manager.

Usage:
    python run.py

Environment:
    MIEN_DEMO_CAMERA=1   use a synthetic camera (for testing with no hardware)
    QT_QPA_PLATFORM=offscreen   headless smoke test (no window shown)
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mien_manager.app import main

if __name__ == "__main__":
    sys.exit(main())
