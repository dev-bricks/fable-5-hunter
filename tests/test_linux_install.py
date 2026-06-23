#!/usr/bin/env python3
"""Regression tests for the Linux autostart installer contract."""
import shutil
import shlex
import subprocess
import unittest
from pathlib import Path
from typing import Optional


ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "install" / "install_linux.sh"


def find_usable_bash() -> Optional[str]:
    """Return a Bash binary that can read Windows workspace paths."""
    candidates = []
    found = shutil.which("bash")
    if found and "WindowsApps" not in found:
        candidates.append(Path(found))
    candidates.append(Path(r"C:\Program Files\Git\bin\bash.exe"))
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    return None


def bash_readable_path(bash: str, path: Path) -> str:
    """Convert a Windows path into a path syntax the selected Bash understands."""
    posix_tail = path.as_posix().replace(":", "", 1)
    if "system32\\bash.exe" in bash.lower():
        return f"/mnt/{path.drive[0].lower()}{posix_tail[1:]}"
    return f"/{path.drive[0].lower()}{posix_tail[2:]}"


class LinuxInstallerTests(unittest.TestCase):
    def test_script_exists_and_is_bash(self):
        self.assertTrue(SCRIPT.is_file())
        text = SCRIPT.read_text(encoding="utf-8")
        self.assertTrue(text.startswith("#!/usr/bin/env bash"))
        self.assertIn("set -euo pipefail", text)

    def test_systemd_user_unit_contract_is_present(self):
        text = SCRIPT.read_text(encoding="utf-8")
        self.assertIn("Description=Fable 5 Hunter availability watcher", text)
        self.assertIn("ExecStart=$PYTHON $SCRIPT run", text)
        self.assertIn("Restart=on-failure", text)
        self.assertIn("WantedBy=default.target", text)
        self.assertIn("systemctl --user enable --now", text)

    def test_cron_fallback_contract_is_present(self):
        text = SCRIPT.read_text(encoding="utf-8")
        self.assertIn("CRON_BEGIN=\"# BEGIN fable-5-hunter\"", text)
        self.assertIn("@reboot cd $(shell_quote \"$SCRIPT_DIR\")", text)
        self.assertIn("hunter.cron.log", text)
        self.assertIn("crontab \"$clean\"", text)

    def test_installer_is_user_scoped(self):
        text = SCRIPT.read_text(encoding="utf-8")
        self.assertNotIn("sudo ", text)
        self.assertIn("No sudo/admin rights required", text)
        self.assertIn("systemctl --user", text)

    def test_bash_syntax_when_bash_is_available(self):
        bash = find_usable_bash()
        if not bash:
            self.skipTest("usable bash is not available on this host")
        script_path = bash_readable_path(bash, SCRIPT)
        subprocess.run([bash, "-lc", f"bash -n {shlex.quote(script_path)}"], check=True)


if __name__ == "__main__":
    unittest.main()
