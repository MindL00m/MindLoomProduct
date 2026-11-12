"""PyInstaller entry point for MindLoom.exe."""

import sys

from mindloom_agent.__main__ import main

if __name__ == "__main__":
    sys.exit(main())
