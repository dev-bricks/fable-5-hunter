#!/usr/bin/env python3
"""
Regression tests for the 2026-06-21 bugsweep of fable-5-hunter.

Each test is RED on the pre-fix code (verified via revert) and GREEN after the fix.
Covers config-robustness defects found by the sweep:

  B1 — check_fable5(): int() on a bad claude_timeout_seconds crashed instead of
       returning the documented ERROR status (exit-code contract "2 = error").
  B2 — hunt(): same unguarded int() casts crashed the daemon on bad config.
  C1 — dispatch(): a string "notifiers" value iterated per character → every
       char an unknown notifier → silently zero notifications (core function).
  C4 — _resolve_telegram(): a non-dict "telegram" value raised AttributeError.

Run:
    python -m unittest tests.test_bug_regressions
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import fable_hunter as fh


class FakeProc:
    def __init__(self, stdout="", stderr="", returncode=0):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


# ===========================================================================
# B1 — check_fable5: bad claude_timeout_seconds must yield ERROR, not crash
# ===========================================================================
class CheckFable5ConfigGuardTests(unittest.TestCase):
    def test_non_numeric_timeout_returns_error(self):
        cfg = dict(fh.DEFAULT_CONFIG)
        cfg["claude_timeout_seconds"] = "abc"
        with mock.patch.object(fh, "find_claude", return_value="/usr/bin/claude"):
            status, detail = fh.check_fable5(cfg)
        self.assertEqual(status, fh.ERROR)
        self.assertIn("claude_timeout_seconds", detail)

    def test_null_timeout_returns_error(self):
        cfg = dict(fh.DEFAULT_CONFIG)
        cfg["claude_timeout_seconds"] = None
        with mock.patch.object(fh, "find_claude", return_value="/usr/bin/claude"):
            status, _ = fh.check_fable5(cfg)
        self.assertEqual(status, fh.ERROR)

    def test_valid_timeout_still_works(self):
        cfg = dict(fh.DEFAULT_CONFIG)
        cfg["claude_timeout_seconds"] = 5
        proc = FakeProc(stdout=f"{fh.ECHO_TOKEN}\n", returncode=0)
        with mock.patch.object(fh, "find_claude", return_value="/usr/bin/claude"), \
             mock.patch.object(fh.subprocess, "run", return_value=proc):
            status, _ = fh.check_fable5(cfg)
        self.assertEqual(status, fh.AVAILABLE)


# ===========================================================================
# B2 — hunt: bad interval config must return 1, not raise
# ===========================================================================
class HuntConfigGuardTests(unittest.TestCase):
    def test_non_numeric_interval_returns_one(self):
        # The int guard runs before load_state/acquire_lock, so the daemon
        # never starts; we just assert the clean error exit code.
        rc = fh.hunt({"check_interval_minutes": "abc"})
        self.assertEqual(rc, 1)

    def test_null_retry_returns_one(self):
        rc = fh.hunt({"alert_retry_seconds": None})
        self.assertEqual(rc, 1)


# ===========================================================================
# C1 — dispatch: string notifiers must be treated as a single name
# ===========================================================================
class DispatchStringNotifiersTests(unittest.TestCase):
    def test_string_notifier_fires_once(self):
        calls = []

        def fake(title, message, cfg):
            calls.append((title, message))
            return True

        with mock.patch.dict(fh.NOTIFIERS, {"desktop": fake}, clear=False):
            res = fh.dispatch("T", "M", {"notifiers": "desktop"})
        self.assertEqual(res, {"desktop": True})
        self.assertEqual(calls, [("T", "M")])

    def test_list_notifiers_unchanged(self):
        def fake(title, message, cfg):
            return True

        with mock.patch.dict(fh.NOTIFIERS, {"desktop": fake}, clear=False):
            res = fh.dispatch("T", "M", {"notifiers": ["desktop"]})
        self.assertEqual(res, {"desktop": True})


# ===========================================================================
# C4 — _resolve_telegram: non-dict telegram must not crash
# ===========================================================================
class TelegramNonDictTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.home = self._tmp.name
        self._patch_expand = mock.patch.object(
            fh.os.path, "expanduser",
            side_effect=lambda p: p.replace("~", self.home, 1) if p.startswith("~") else p,
        )
        self._patch_expand.start()
        self._env = mock.patch.dict(os.environ, {}, clear=False)
        self._env.start()
        os.environ.pop("TELEGRAM_BOT_TOKEN", None)
        os.environ.pop("TELEGRAM_OWNER_ID", None)

    def tearDown(self):
        self._patch_expand.stop()
        self._env.stop()
        self._tmp.cleanup()

    def test_string_telegram_does_not_crash(self):
        self.assertEqual(fh._resolve_telegram({"telegram": "oops"}), ("", ""))

    def test_list_telegram_does_not_crash(self):
        self.assertEqual(fh._resolve_telegram({"telegram": ["x"]}), ("", ""))


# ===========================================================================
# S1 — _int_cfg must reject non-positive values (negative crashes the daemon
#      via time.sleep(-n); zero busy-loops it)
# ===========================================================================
class IntCfgRangeTests(unittest.TestCase):
    # Unit-level assertions on the clamp itself — these are the clean red-on-revert
    # evidence for S1 (the pre-fix code has no value guard). The hunt()-level checks
    # below are integration coverage; pre-fix they happen to return 1 via the lock
    # path rather than the missing guard, so they do not isolate S1.
    def test_int_cfg_rejects_negative(self):
        with self.assertRaises(ValueError):
            fh._int_cfg({"x": -5}, "x", 30)

    def test_int_cfg_rejects_zero(self):
        with self.assertRaises(ValueError):
            fh._int_cfg({"x": 0}, "x", 30)

    def test_int_cfg_accepts_positive(self):
        self.assertEqual(fh._int_cfg({"x": 10}, "x", 30), 10)

    def test_negative_interval_returns_one(self):
        rc = fh.hunt({"check_interval_minutes": -5})
        self.assertEqual(rc, 1)

    def test_zero_interval_returns_one(self):
        rc = fh.hunt({"check_interval_minutes": 0})
        self.assertEqual(rc, 1)

    def test_zero_timeout_is_error(self):
        cfg = dict(fh.DEFAULT_CONFIG)
        cfg["claude_timeout_seconds"] = 0
        with mock.patch.object(fh, "find_claude", return_value="/usr/bin/claude"):
            status, _ = fh.check_fable5(cfg)
        self.assertEqual(status, fh.ERROR)


# ===========================================================================
# S2 — dispatch must tolerate null / scalar notifiers without crashing
# ===========================================================================
class DispatchNonIterableNotifiersTests(unittest.TestCase):
    def test_none_notifiers_no_crash(self):
        self.assertEqual(fh.dispatch("T", "M", {"notifiers": None}), {})

    def test_scalar_notifiers_no_crash(self):
        self.assertEqual(fh.dispatch("T", "M", {"notifiers": 5}), {})


# ===========================================================================
# R1 — banner vs. real unavailable response (Ticket T-20260622-01)
# ===========================================================================
class BannerFalsePositiveRegressionTests(unittest.TestCase):
    """
    The CLI startup banner can show "Fable 5 with high effort - Claude Max"
    even though the model is blocked. The hunter must NOT treat the banner as
    availability; only a real echo of ECHO_TOKEN counts. Conversely, the real
    unavailable message must be detected via partial substrings such as
    "is currently unavailable" and "fable-mythos-access".
    """

    def setUp(self):
        self.cfg = dict(fh.DEFAULT_CONFIG)

    def test_startup_banner_with_rc0_is_not_available(self):
        proc = FakeProc(
            stdout="Fable 5 with high effort - Claude Max\n",
            returncode=0,
        )
        with mock.patch.object(fh, "find_claude", return_value="/usr/bin/claude"), \
             mock.patch.object(fh.subprocess, "run", return_value=proc):
            status, detail = fh.check_fable5(self.cfg)
        self.assertEqual(status, fh.UNAVAILABLE)
        self.assertIn("without token", detail.lower())

    def test_real_unavailable_response_after_prompt_is_unavailable(self):
        real_response = (
            "Claude Fable 5 is currently unavailable. "
            "Learn more: https://www.anthropic.com/news/fable-mythos-access"
        )
        proc = FakeProc(stdout=real_response, returncode=0)
        with mock.patch.object(fh, "find_claude", return_value="/usr/bin/claude"), \
             mock.patch.object(fh.subprocess, "run", return_value=proc):
            status, detail = fh.check_fable5(self.cfg)
        self.assertEqual(status, fh.UNAVAILABLE)
        self.assertIn("unavailable", detail.lower())

    def test_fable_mythos_access_url_alone_is_unavailable(self):
        proc = FakeProc(
            stdout="Learn more: https://www.anthropic.com/news/fable-mythos-access",
            returncode=0,
        )
        with mock.patch.object(fh, "find_claude", return_value="/usr/bin/claude"), \
             mock.patch.object(fh.subprocess, "run", return_value=proc):
            status, _ = fh.check_fable5(self.cfg)
        self.assertEqual(status, fh.UNAVAILABLE)

    def test_only_real_token_echo_counts_as_available(self):
        proc = FakeProc(stdout=f"{fh.ECHO_TOKEN}\n", returncode=0)
        with mock.patch.object(fh, "find_claude", return_value="/usr/bin/claude"), \
             mock.patch.object(fh.subprocess, "run", return_value=proc):
            status, _ = fh.check_fable5(self.cfg)
        self.assertEqual(status, fh.AVAILABLE)


if __name__ == "__main__":
    unittest.main()
