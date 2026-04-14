"""
Pytest conftest — ensures src/ is importable without pip install.
"""

import sys
from pathlib import Path

# add project root to path so `from src.xxx import yyy` works
sys.path.insert(0, str(Path(__file__).parent.parent))
