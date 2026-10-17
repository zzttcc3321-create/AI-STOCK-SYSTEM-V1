"""Process-scoped audit guard for the offline 015 Phase B retry.

Install only in a disposable child process: Python audit hooks cannot be removed.
This guards audited Python operations, not unrelated processes or arbitrary native
code. The one permitted child is the exact read-only Git HEAD query. Do not pass
open writable file descriptors into untrusted code.
"""
from __future__ import annotations

import os
from pathlib import Path
import stat
import sys


class PhaseBStop(RuntimeError):
    """The first stop event remains available even if a CLI catches this error."""


class PhaseBMonitor:
    def __init__(self, *, allowed_directories=(), allowed_files=(),
                 allowed_git_command=None, git_cwd=None, event_sink=None,
                 scope="LIVE"):
        if scope not in {"LIVE", "REGRESSION"}:
            raise ValueError("Invalid monitor scope")
        self.allowed_directories = frozenset(self._absolute(p) for p in allowed_directories)
        self.allowed_files = frozenset(self._absolute(p) for p in allowed_files)
        self.allowed_git_command = tuple(allowed_git_command) if allowed_git_command else None
        if self.allowed_git_command is not None:
            command = self.allowed_git_command
            if not (len(command) == 5 and command[:2] == ("git", "-C")
                    and os.path.isabs(command[2]) and command[3:] == ("rev-parse", "HEAD")):
                raise ValueError("Only an exact git -C <absolute repo> rev-parse HEAD is allowed")
        self.git_cwd = os.fspath(git_cwd) if git_cwd is not None else None
        self.event_sink = event_sink
        self.scope = scope
        self.stop_event = None
        self.installed = False

    @staticmethod
    def _absolute(path):
        return os.path.abspath(os.fsdecode(os.fspath(path)))

    @staticmethod
    def _safe_existing_components(path, *, leaf_directory=False):
        """Do not follow symlinks, junctions or non-directory ancestors."""
        absolute = Path(path)
        for component in reversed((absolute, *absolute.parents)):
            try:
                info = component.lstat()
            except FileNotFoundError:
                continue
            except OSError:
                return False
            attributes = getattr(info, "st_file_attributes", None)
            if sys.platform == "win32" and attributes is None:
                return False
            if stat.S_ISLNK(info.st_mode) or ((attributes or 0) &
                    getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)):
                return False
            is_directory = component != absolute or leaf_directory
            if is_directory and not stat.S_ISDIR(info.st_mode):
                return False
            if not is_directory and not stat.S_ISREG(info.st_mode):
                return False
        return True

    @staticmethod
    def _benign_device(path, flags):
        if path not in {"/dev/null", "/dev/random", "/dev/urandom", "/dev/zero"}:
            return False
        permitted_flags = os.O_ACCMODE | os.O_CLOEXEC | os.O_NONBLOCK
        permitted_flags |= getattr(os, "O_NOCTTY", 0) | getattr(os, "O_NOFOLLOW", 0)
        permitted_flags |= getattr(os, "O_LARGEFILE", 0) | getattr(os, "O_BINARY", 0)
        if flags & ~permitted_flags:
            return False
        if path != "/dev/null" and (flags & os.O_ACCMODE) != os.O_RDONLY:
            return False
        if (flags & os.O_ACCMODE) not in {os.O_RDONLY, os.O_WRONLY, os.O_RDWR}:
            return False
        try:
            parent = Path("/dev").lstat()
            info = Path(path).lstat()
        except OSError:
            return False
        linux_devices = {"/dev/null": (1, 3), "/dev/zero": (1, 5),
                         "/dev/random": (1, 8), "/dev/urandom": (1, 9)}
        if not sys.platform.startswith("linux") or (os.major(info.st_rdev),
                os.minor(info.st_rdev)) != linux_devices[path]:
            return False
        return (stat.S_ISDIR(parent.st_mode) and stat.S_ISCHR(info.st_mode)
                and not ((getattr(info, "st_file_attributes", 0) or 0) &
                         getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)))

    def _stop(self, trigger, event):
        # Latch before invoking any logging callback or raising: generic CLI
        # exception handlers must not erase the reason for the stop.
        if self.stop_event is None:
            self.stop_event = {"trigger": trigger, "event": event, "scope": self.scope,
                               "expected_negative_test": self.scope == "REGRESSION"}
            if self.event_sink is not None:
                try:
                    self.event_sink(dict(self.stop_event))
                except Exception:
                    self.stop_event["event_sink_failed"] = True
        raise PhaseBStop(self.stop_event["trigger"])

    def raise_if_stopped(self):
        if self.stop_event is not None:
            raise PhaseBStop(self.stop_event["trigger"])

    def _write_path(self, path, event, *, directory=False, dir_fd=None):
        # dir_fd aliases cannot be compared safely to the explicit path allowlist.
        if isinstance(path, int) or dir_fd not in (None, -1):
            self._stop("UNEXPECTED_WRITE", event)
        try:
            literal = os.fsdecode(os.fspath(path))
            # The open audit event omits dir_fd. Relative names could otherwise
            # match the cwd allowlist but write through a different directory FD.
            if not os.path.isabs(literal) or ".." in Path(literal).parts:
                self._stop("UNEXPECTED_WRITE", event)
            absolute = self._absolute(path)
        except (TypeError, ValueError):
            self._stop("UNEXPECTED_WRITE", event)
        approved = self.allowed_directories if directory else self.allowed_files
        if absolute not in approved or not self._safe_existing_components(
                absolute, leaf_directory=directory):
            self._stop("UNKNOWN_FILE_CREATED" if directory else "UNEXPECTED_WRITE", event)

    def _audit(self, event, args):
        monitored = (event == "open" or event.startswith(("socket.", "sqlite3.connect"))
                     or event in {"os.mkdir", "os.remove", "os.rmdir", "os.rename", "os.link",
                                  "os.symlink", "os.truncate", "os.chmod", "os.chown", "os.utime",
                                  "os.system", "os.exec", "os.posix_spawn", "os.fork", "os.forkpty",
                                  "subprocess.Popen", "urllib.Request", "http.client.connect",
                                  "import", "sys.addaudithook"})
        if not monitored:
            return
        self.raise_if_stopped()
        if event == "open":
            path, mode, flags = args
            if not isinstance(flags, int):
                self._stop("UNEXPECTED_WRITE", event)
            if isinstance(path, (str, bytes, os.PathLike)):
                absolute = self._absolute(path)
                # Require the literal device spelling, not a symlink/relative alias.
                if absolute == "/dev" or absolute.startswith("/dev/"):
                    if os.fsdecode(os.fspath(path)) == absolute and self._benign_device(absolute, flags):
                        return
                    self._stop("UNEXPECTED_WRITE", event)
            write_flags = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND
            temporary_flags = getattr(os, "O_TMPFILE", 0)
            temporary_write = bool(temporary_flags and flags & temporary_flags == temporary_flags)
            if flags & write_flags or temporary_write or (mode is not None and any(c in mode for c in "wax+")):
                self._write_path(path, event)
        elif event == "os.mkdir":
            self._write_path(args[0], event, directory=True,
                             dir_fd=args[2] if len(args) > 2 else None)
        elif event in {"os.remove", "os.rmdir", "os.rename", "os.link", "os.symlink",
                       "os.truncate", "os.chmod", "os.chown", "os.utime"}:
            # Initialization requires creation and writes only; no replacement,
            # deletion, link creation or metadata mutation is authorized.
            self._stop("UNEXPECTED_WRITE", event)
        elif event.startswith("sqlite3.connect"):
            self._stop("UNEXPECTED_WRITE", event)
        elif event.startswith("socket.") or event in {"urllib.Request", "http.client.connect"}:
            self._stop("NETWORK_CALL_DETECTED", event)
        elif event == "subprocess.Popen":
            executable, command, cwd, environment = args
            if not (self.allowed_git_command is not None and executable == "git"
                    and isinstance(command, (tuple, list))
                    and tuple(command) == self.allowed_git_command and cwd == self.git_cwd
                    and environment is None):
                self._stop("UNEXPECTED_WRITE", event)
            # Git tracing/config variables can write even for rev-parse. This
            # plumbing command never invokes a pager; disabling prompts is safe.
            benign_git_environment = {"GIT_PAGER"}
            if os.environ.get("GIT_TERMINAL_PROMPT") == "0":
                benign_git_environment.add("GIT_TERMINAL_PROMPT")
            if any(key.startswith("GIT_") and key not in benign_git_environment
                   for key in os.environ):
                self._stop("UNEXPECTED_WRITE", event)
        elif event in {"os.system", "os.exec", "os.posix_spawn", "os.fork", "os.forkpty",
                       "sys.addaudithook"}:
            self._stop("UNEXPECTED_WRITE", event)
        elif event == "import":
            module = args[0]
            root = module.split(".", 1)[0]
            if root in {"apscheduler", "schedule", "scheduler", "dashboard"}:
                self._stop("SCHEDULER_STARTED", event)
            if root in {"openai", "anthropic", "litellm", "langchain", "llama_index"}:
                self._stop("LLM_CALL_DETECTED", event)
            if root in {"requests", "httpx", "aiohttp"} or module == "urllib.request":
                self._stop("NETWORK_CALL_DETECTED", event)

    def install(self):
        if self.installed:
            raise ValueError("Audit monitor is already installed")
        # Prevent import-time bytecode writes from polluting the allowed outputs.
        sys.dont_write_bytecode = True
        sys.addaudithook(self._audit)
        self.installed = True
        return self
