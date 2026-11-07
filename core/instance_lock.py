"""Защита от запуска двух экземпляров бота с одним токеном.

Два процесса с одним токеном получают одни и те же interaction-события:
один успевает ответить, второй получает `10062 Unknown interaction`.
Файловая блокировка не даёт запустить второй экземпляр на той же машине.
"""

from __future__ import annotations

import os
from pathlib import Path


class AlreadyRunningError(RuntimeError):
    pass


class InstanceLock:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._file = None
        self._locked = False

    def acquire(self) -> None:
        if self._locked:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open("a+", encoding="utf-8")
        try:
            if os.name == "nt":
                import msvcrt

                handle.seek(0)
                if handle.read(1) == "":
                    handle.write("0")
                    handle.flush()
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            handle.close()
            raise AlreadyRunningError(
                "Похоже, бот уже запущен. Остановите другой процесс и попробуйте снова."
            ) from exc

        self._file = handle
        self._locked = True
        handle.seek(0)
        handle.truncate()
        handle.write(str(os.getpid()))
        handle.flush()

    def release(self) -> None:
        if not self._locked or self._file is None:
            return
        try:
            if os.name == "nt":
                import msvcrt

                self._file.seek(0)
                msvcrt.locking(self._file.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self._file.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        finally:
            try:
                self._file.close()
            finally:
                self._file = None
                self._locked = False

    def __enter__(self) -> "InstanceLock":
        self.acquire()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.release()
