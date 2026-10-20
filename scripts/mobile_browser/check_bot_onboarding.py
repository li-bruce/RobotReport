"""Compatibility entry point for the current robot workspace browser checks."""
import runpy
from pathlib import Path
runpy.run_path(str(Path(__file__).with_name('check_relations.py')), run_name='__main__')
