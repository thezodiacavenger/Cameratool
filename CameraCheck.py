#!/usr/bin/env python3
"""Camera Check - used camera condition evaluator. Double-click or run: python CameraCheck.py"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
if sys.version_info < (3, 8):
    sys.exit("Camera Check needs Python 3.8 or newer - https://www.python.org/downloads/")

from camcheck.server import main  # noqa: E402

main()
