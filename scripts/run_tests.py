"""Запуск всех тестов: python scripts/run_tests.py"""
import sys
import unittest
from pathlib import Path

root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(root))
sys.path.insert(0, str(root / "tests"))
suite = unittest.defaultTestLoader.discover(str(root / "tests"), pattern="test_*.py")
res = unittest.TextTestRunner(verbosity=2).run(suite)
sys.exit(0 if res.wasSuccessful() else 1)
