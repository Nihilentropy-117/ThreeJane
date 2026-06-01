import os
import re
import shlex
import uuid

import pexpect


class PersistentShell:
    """A long-lived bash process. Working directory, environment variables, and
    activated environments persist across run() calls within a session."""

    def __init__(self, cwd: str = "/workspace"):
        self.cwd = cwd
        self._needs_check = False
        self._spawn()

    def _spawn(self):
        """(Re)start the underlying bash process at self.cwd."""
        os.makedirs(self.cwd, exist_ok=True)
        self.child = pexpect.spawn(
            "/bin/bash", ["--norc", "--noprofile"],
            encoding="utf-8", codec_errors="replace",
            echo=False, dimensions=(60, 220), timeout=None, cwd=self.cwd,
        )
        # Neutralise prompts and pagers so they never pollute captured output.
        self.child.sendline("export PS1='' PS2='' PROMPT_COMMAND='' PAGER=cat GIT_PAGER=cat TERM=dumb")
        self.child.sendline("stty -echo 2>/dev/null; set +o history 2>/dev/null")
        self._drain()
        self._needs_check = False

    def _respawn(self):
        """Replace a corrupted or dead child. In-shell state (cwd changes, env
        vars) is lost; this only fires on detected corruption, where reusing the
        shell would make every subsequent command fail."""
        try:
            self.child.close(force=True)
        except Exception:
            pass
        self._spawn()

    def _probe(self, timeout: float = 3.0) -> bool:
        """Return True if the shell is responsive and in sync with the marker
        protocol."""
        token = f"__3JANE_PROBE_{uuid.uuid4().hex}__"
        try:
            self.child.sendline(f'printf "%s\\n" "{token}"')
            self.child.expect(re.escape(token), timeout=timeout)
            self._drain()
            return True
        except Exception:
            return False

    def _drain(self):
        try:
            while True:
                self.child.read_nonblocking(size=65536, timeout=0.05)
        except Exception:
            pass

    def run(self, command: str, workdir: str = None, timeout_ms: int = 120000):
        """Run a command, returning (output, exit_code, timed_out)."""
        if self._needs_check and not self._probe():
            self._respawn()
        self._needs_check = False
        marker = f"__3JANE_{uuid.uuid4().hex}__"
        prefix = f"cd {shlex.quote(workdir)} && " if workdir else ""
        self.child.send(prefix + command + "\n")
        self.child.sendline(f'printf "\\n%s:%s\\n" "{marker}" "$?"')
        try:
            self.child.expect(re.escape(marker) + r":(-?\d+)", timeout=timeout_ms / 1000.0)
            out = self.child.before or ""
            code = int(self.child.match.group(1))
            timed_out = False
        except pexpect.TIMEOUT:
            self.child.sendcontrol("c")
            self._drain()
            out = self.child.before or ""
            code = 124
            timed_out = True
            # The deferred marker line may be left unconsumed; verify before next run.
            self._needs_check = True
        except pexpect.EOF:
            out = self.child.before or ""
            code = 137
            timed_out = False
            self._respawn()
        return out.replace("\r\n", "\n").strip("\n"), code, timed_out

    def close(self):
        try:
            self.child.sendline("exit")
            self.child.close(force=True)
        except Exception:
            pass
