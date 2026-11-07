"""Загрузка .env."""
from __future__ import annotations

import os
import unittest
from pathlib import Path

from core.env import load_dotenv

DATA_DIR = Path(__file__).resolve().parent / "_data"


class DotenvTests(unittest.TestCase):
    def setUp(self) -> None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        self._created: list[Path] = []

    def tearDown(self) -> None:
        for path in self._created:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass

    def _write(self, name: str, content: str) -> Path:
        path = DATA_DIR / name
        path.write_text(content, encoding="utf-8")
        self._created.append(path)
        return path

    def test_load_simple_file(self) -> None:
        path = self._write(
            "simple.env",
            "# комментарий\n"
            "DSH_TEST_ONE=hello\n"
            "DSH_TEST_TWO='quoted value'\n"
            "DSH_TEST_THREE=\"line1\\nline2\"\n",
        )

        os.environ.pop("DSH_TEST_ONE", None)
        os.environ.pop("DSH_TEST_TWO", None)
        os.environ.pop("DSH_TEST_THREE", None)
        try:
            load_dotenv(path)
            self.assertEqual(os.environ["DSH_TEST_ONE"], "hello")
            self.assertEqual(os.environ["DSH_TEST_TWO"], "quoted value")
            self.assertEqual(os.environ["DSH_TEST_THREE"], "line1\nline2")
        finally:
            for key in ("DSH_TEST_ONE", "DSH_TEST_TWO", "DSH_TEST_THREE"):
                os.environ.pop(key, None)

    def test_existing_values_are_not_overwritten(self) -> None:
        path = self._write("existing.env", "DSH_TEST_EXISTING=new\n")

        os.environ["DSH_TEST_EXISTING"] = "old"
        try:
            load_dotenv(path)
            self.assertEqual(os.environ["DSH_TEST_EXISTING"], "old")

            load_dotenv(path, override=True)
            self.assertEqual(os.environ["DSH_TEST_EXISTING"], "new")
        finally:
            os.environ.pop("DSH_TEST_EXISTING", None)


if __name__ == "__main__":
    unittest.main()
