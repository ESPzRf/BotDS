"""Тест блокировки второго экземпляра бота."""
from __future__ import annotations

import unittest
from pathlib import Path

from core.instance_lock import AlreadyRunningError, InstanceLock

DATA_DIR = Path(__file__).resolve().parent / "_data"


class InstanceLockTests(unittest.TestCase):
    def test_second_instance_is_rejected(self) -> None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        path = DATA_DIR / "bot-test.lock"
        first = InstanceLock(path)
        second = InstanceLock(path)
        try:
            first.acquire()
            with self.assertRaises(AlreadyRunningError):
                second.acquire()
        finally:
            second.release()
            first.release()
            path.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
