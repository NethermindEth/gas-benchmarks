import os
import sys

# The modules under test (utils, report_html, ...) live at the repository root.
# Put the root on sys.path so `import utils` resolves whether the suite is run
# with `pytest` or `python -m pytest` from the repo root.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
