# -*- coding: utf-8 -*-
"""打字助手入口：python AutoTyper.pyw"""

import os
import sys

PROGRAM_DIR = os.path.dirname(os.path.abspath(__file__))
if PROGRAM_DIR not in sys.path:
    sys.path.insert(0, PROGRAM_DIR)

from autotyper.app import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
