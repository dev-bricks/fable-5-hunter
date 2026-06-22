#!/usr/bin/env python3
"""
Tests for fable-5-hunter (standard library only, zero-dependency).

Run:
    python -m unittest discover -s tests
    # or
    python -m unittest tests.test_hunter
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

# Make the project root importable regardless of how tests are invoked
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import fable_hunter as fh


class FakeProc:
    """Minimal subprocess.CompletedProcess stand-in."""
    def __init__(self, stdout="", stderr="", returncode=0):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


# ===========================================================================
# Deep-merge
# ===========================================================================
class DeepMergeTests(unittest.TestCase):
    def test_overrides_and_preserves(self):
        base = {"a": 1, "b": {"x": 1, "y": 2}}
        out = fh._deep_merge(base, {"b": {"y": 99, "z": 3}, "c": 4})
        self.assertEqual(out["a"], 1)
        self.assertEqual(out["b"], {"x": 1, "y": 99, "z": 3})
        self.assertEqual(out["c"], 4)

    def test_does_not_mutate_base(self):
        base = {"b": {"x": 1}}
        fh._deep_merge(base, {"b": {"x": 2}})
        self.assertEqual(base["b"]["x"], 1)


# ===========================================================================
# i18n
# ===========================================================================
class I18nTests(unittest.TestCase):
    def setUp(self):
        # Clear the locale cache before each test so patches take effect.
        fh._locale_cache.clear()

    def test_english_available_key(self):
        text = fh.t("available", "en")
        self.assertIn("HOORAY", text)
        self.assertIn("Fable 5", text)

    def test_german_available_key(self):
        text = fh.t("available", "de")
        self.assertIn("HURRA", text)
        self.assertIn("Fable 5", text)

    def test_fallback_to_english_for_unknown_lang(self):
        text = fh.t("available", "zz")
        self.assertIn("HOORAY", text)

    def test_fallback_to_key_for_unknown_key(self):
        text = fh.t("nonexistent_key_xyz", "en")
        self.assertEqual(text, "nonexistent_key_xyz")

    def test_fallback_for_key_missing_in_de_but_present_in_en(self):
        # Temporarily add a key to en only by patching _load_locale
        def fake_load(lang):
            if lang == "en":
                return {"only_in_en": "English only"}
            return {}
        fh._locale_cache.clear()
        with mock.patch.object(fh, "_load_locale", side_effect=fake_load):
            text = fh.t("only_in_en", "de")
        self.assertEqual(text, "English only")

    def test_placeholder_substitution(self):
        fh._locale_cache["en"] = {"started": "Checking every {interval} minutes."}
        text = fh.t("started", "en", interval=30)
        self.assertEqual(text, "Checking every 30 minutes.")

    def test_msg_uses_cfg_override_first(self):
        cfg = {"messages": {"available": "CUSTOM MESSAGE"}, "lang": "en"}
        self.assertEqual(fh._msg(cfg, "available"), "CUSTOM MESSAGE")

    def test_msg_falls_back_to_locale(self):
        cfg = {"messages": {}, "lang": "en"}
        fh._locale_cache.clear()
        text = fh._msg(cfg, "available")
        self.assertIn("HOORAY", text)

    def test_default_messages_come_from_locales_not_hardcoded(self):
        """Ensure the hunt loop uses locale messages when cfg.messages is empty."""
        cfg = dict(fh.DEFAULT_CONFIG)
        cfg["lang"] = "en"
        # If _msg returns a non-empty string for "alive", locales are wired up.
        fh._locale_cache.clear()
        text = fh._msg(cfg, "alive")
        self.assertTrue(len(text) > 0)
        self.assertNotEqual(text, "alive")  # not just the key


# ===========================================================================
# Detection: check_fable5
# ===========================================================================
class CheckFable5Tests(unittest.TestCase):
    def setUp(self):
        self.cfg = dict(fh.DEFAULT_CONFIG)

    def test_available_when_token_echoed(self):
        proc = FakeProc(stdout=f"{fh.ECHO_TOKEN}\n", returncode=0)
        with mock.patch.object(fh, "find_claude", return_value="/usr/bin/claude"), \
             mock.patch.object(fh.subprocess, "run", return_value=proc):
            status, _ = fh.check_fable5(self.cfg)
        self.assertEqual(status, fh.AVAILABLE)

    def test_unavailable_message(self):
        proc = FakeProc(
            stdout="Claude Fable 5 is currently unavailable. Learn more: ...",
            returncode=1,
        )
        with mock.patch.object(fh, "find_claude", return_value="/usr/bin/claude"), \
             mock.patch.object(fh.subprocess, "run", return_value=proc):
            status, _ = fh.check_fable5(self.cfg)
        self.assertEqual(status, fh.UNAVAILABLE)

    def test_overloaded_is_unavailable(self):
        proc = FakeProc(stdout="overloaded_error: model is overloaded", returncode=1)
        with mock.patch.object(fh, "find_claude", return_value="/usr/bin/claude"), \
             mock.patch.object(fh.subprocess, "run", return_value=proc):
            status, _ = fh.check_fable5(self.cfg)
        self.assertEqual(status, fh.UNAVAILABLE)

    def test_issue_with_selected_model_is_unavailable(self):
        proc = FakeProc(
            stdout="There was an issue with the selected model", returncode=1
        )
        with mock.patch.object(fh, "find_claude", return_value="/usr/bin/claude"), \
             mock.patch.object(fh.subprocess, "run", return_value=proc):
            status, _ = fh.check_fable5(self.cfg)
        self.assertEqual(status, fh.UNAVAILABLE)

    def test_error_when_claude_missing(self):
        with mock.patch.object(fh, "find_claude", return_value=None):
            status, detail = fh.check_fable5(self.cfg)
        self.assertEqual(status, fh.ERROR)
        self.assertIn("claude", detail.lower())

    def test_not_logged_in_is_error_not_unavailable(self):
        proc = FakeProc(stdout="Not logged in · Please run /login", returncode=1)
        with mock.patch.object(fh, "find_claude", return_value="/usr/bin/claude"), \
             mock.patch.object(fh.subprocess, "run", return_value=proc):
            status, detail = fh.check_fable5(self.cfg)
        self.assertEqual(status, fh.ERROR)
        self.assertIn("auth", detail.lower())

    def test_timeout_is_unavailable(self):
        with mock.patch.object(fh, "find_claude", return_value="/usr/bin/claude"), \
             mock.patch.object(
                 fh.subprocess, "run",
                 side_effect=subprocess.TimeoutExpired("claude", 1),
             ):
            status, detail = fh.check_fable5(self.cfg)
        self.assertEqual(status, fh.UNAVAILABLE)
        self.assertIn("timeout", detail.lower())

    def test_exit0_without_token_is_unavailable(self):
        # No token, no marker, rc 0 → conservatively UNAVAILABLE (no false-positive).
        proc = FakeProc(stdout="Some unrelated chatter", returncode=0)
        with mock.patch.object(fh, "find_claude", return_value="/usr/bin/claude"), \
             mock.patch.object(fh.subprocess, "run", return_value=proc):
            status, _ = fh.check_fable5(self.cfg)
        self.assertEqual(status, fh.UNAVAILABLE)

    def test_token_present_but_nonzero_exit_not_available(self):
        # Token in text but rc != 0 → must NOT be treated as available.
        proc = FakeProc(stdout=f"{fh.ECHO_TOKEN}", returncode=1)
        with mock.patch.object(fh, "find_claude", return_value="/usr/bin/claude"), \
             mock.patch.object(fh.subprocess, "run", return_value=proc):
            status, _ = fh.check_fable5(self.cfg)
        self.assertEqual(status, fh.UNAVAILABLE)

    def test_token_in_stderr_only_is_not_available(self):
        # Token on stderr but NOT stdout → must not trigger AVAILABLE.
        proc = FakeProc(stdout="", stderr=f"{fh.ECHO_TOKEN}", returncode=0)
        with mock.patch.object(fh, "find_claude", return_value="/usr/bin/claude"), \
             mock.patch.object(fh.subprocess, "run", return_value=proc):
            status, _ = fh.check_fable5(self.cfg)
        self.assertEqual(status, fh.UNAVAILABLE)

    def test_banner_text_rc0_is_unavailable(self):
        # The CLI startup banner misleadingly shows "Fable 5 with high effort"
        # even though the model is not reachable. It must NOT count as available.
        proc = FakeProc(
            stdout="Fable 5 with high effort - Claude Max\n",
            returncode=0,
        )
        with mock.patch.object(fh, "find_claude", return_value="/usr/bin/claude"), \
             mock.patch.object(fh.subprocess, "run", return_value=proc):
            status, detail = fh.check_fable5(self.cfg)
        self.assertEqual(status, fh.UNAVAILABLE)
        self.assertIn("without token", detail.lower())

    def test_real_unavailable_message_is_unavailable(self):
        # Empirically observed response after sending the prompt.
        proc = FakeProc(
            stdout=(
                "Claude Fable 5 is currently unavailable. "
                "Learn more: https://www.anthropic.com/news/fable-mythos-access"
            ),
            returncode=0,
        )
        with mock.patch.object(fh, "find_claude", return_value="/usr/bin/claude"), \
             mock.patch.object(fh.subprocess, "run", return_value=proc):
            status, _ = fh.check_fable5(self.cfg)
        self.assertEqual(status, fh.UNAVAILABLE)

    def test_unavailable_url_marker_is_unavailable(self):
        # The URL path alone is a reliable negative signal.
        proc = FakeProc(
            stdout="Learn more: https://www.anthropic.com/news/fable-mythos-access",
            returncode=0,
        )
        with mock.patch.object(fh, "find_claude", return_value="/usr/bin/claude"), \
             mock.patch.object(fh.subprocess, "run", return_value=proc):
            status, _ = fh.check_fable5(self.cfg)
        self.assertEqual(status, fh.UNAVAILABLE)

    def test_negative_marker_is_case_insensitive(self):
        proc = FakeProc(stdout="Fable 5 is CURRENTLY UNAVAILABLE. Sorry.", returncode=0)
        with mock.patch.object(fh, "find_claude", return_value="/usr/bin/claude"), \
             mock.patch.object(fh.subprocess, "run", return_value=proc):
            status, _ = fh.check_fable5(self.cfg)
        self.assertEqual(status, fh.UNAVAILABLE)

    def test_token_plus_negative_marker_is_unavailable(self):
        # Safety: a negative signal must override a token-like echo.
        proc = FakeProc(
            stdout=f"{fh.ECHO_TOKEN}\nClaude Fable 5 is currently unavailable.",
            returncode=0,
        )
        with mock.patch.object(fh, "find_claude", return_value="/usr/bin/claude"), \
             mock.patch.object(fh.subprocess, "run", return_value=proc):
            status, _ = fh.check_fable5(self.cfg)
        self.assertEqual(status, fh.UNAVAILABLE)


# ===========================================================================
# Telegram notifier
# ===========================================================================
class TelegramResolveTests(unittest.TestCase):
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

    def test_config_wins(self):
        cfg = {"telegram": {"bot_token": "T", "owner_id": "C"}}
        self.assertEqual(fh._resolve_telegram(cfg), ("T", "C"))

    def test_env_fallback(self):
        os.environ["TELEGRAM_BOT_TOKEN"] = "ET"
        os.environ["TELEGRAM_OWNER_ID"] = "EC"
        self.assertEqual(fh._resolve_telegram({"telegram": {}}), ("ET", "EC"))

    def test_credentials_file_fallback(self):
        d = Path(self.home) / ".credentials"
        d.mkdir(parents=True)
        (d / "telegram_bot_token").write_text("FT", encoding="utf-8")
        (d / "telegram_owner_id").write_text("FC", encoding="utf-8")
        self.assertEqual(fh._resolve_telegram({"telegram": {}}), ("FT", "FC"))

    def test_bach_config_fallback(self):
        d = Path(self.home) / ".config" / "bach"
        d.mkdir(parents=True)
        (d / "telegram_chat.json").write_text(
            json.dumps({"bot_token": "BT", "owner_id": "BC"}), encoding="utf-8"
        )
        self.assertEqual(fh._resolve_telegram({"telegram": {}}), ("BT", "BC"))

    def test_nothing_configured(self):
        self.assertEqual(fh._resolve_telegram({"telegram": {}}), ("", ""))


class TelegramNotifierTests(unittest.TestCase):
    def test_skips_without_creds(self):
        with mock.patch.object(fh, "_resolve_telegram", return_value=("", "")):
            self.assertFalse(fh.notify_telegram("t", "m", {}))

    def test_sends_with_creds(self):
        resp = mock.MagicMock()
        resp.status = 200
        resp.__enter__ = lambda s: resp
        resp.__exit__ = lambda *a: False
        with mock.patch.object(fh, "_resolve_telegram", return_value=("T", "C")), \
             mock.patch.object(fh.urllib.request, "urlopen", return_value=resp):
            self.assertTrue(fh.notify_telegram("t", "m", {}))


# ===========================================================================
# Discord notifier
# ===========================================================================
class DiscordNotifierTests(unittest.TestCase):
    @unittest.skipIf(
        not os.environ.get("DISCORD_WEBHOOK_URL"),
        "DISCORD_WEBHOOK_URL not set — skipping live Discord test",
    )
    def test_sends_with_webhook(self):
        cfg = {"discord": {"webhook_url": os.environ["DISCORD_WEBHOOK_URL"]}}
        result = fh.notify_discord("Test", "fable-5-hunter discord test", cfg)
        self.assertTrue(result)

    def test_skips_without_webhook_url(self):
        cfg = {"discord": {"webhook_url": ""}}
        self.assertFalse(fh.notify_discord("t", "m", cfg))

    def test_sends_with_mocked_webhook(self):
        resp = mock.MagicMock()
        resp.status = 204
        resp.__enter__ = lambda s: resp
        resp.__exit__ = lambda *a: False
        cfg = {"discord": {"webhook_url": "https://discord.com/api/webhooks/fake"}}
        with mock.patch.object(fh.urllib.request, "urlopen", return_value=resp):
            self.assertTrue(fh.notify_discord("t", "m", cfg))


# ===========================================================================
# ntfy notifier
# ===========================================================================
class NtfyNotifierTests(unittest.TestCase):
    @unittest.skipIf(
        not os.environ.get("NTFY_TOPIC"),
        "NTFY_TOPIC not set — skipping live ntfy test",
    )
    def test_sends_with_topic(self):
        cfg = {"ntfy": {"topic": os.environ["NTFY_TOPIC"]}}
        result = fh.notify_ntfy("Test", "fable-5-hunter ntfy test", cfg)
        self.assertTrue(result)

    def test_skips_without_topic(self):
        cfg = {"ntfy": {"topic": ""}}
        self.assertFalse(fh.notify_ntfy("t", "m", cfg))

    def test_sends_with_mocked_ntfy(self):
        resp = mock.MagicMock()
        resp.status = 200
        resp.__enter__ = lambda s: resp
        resp.__exit__ = lambda *a: False
        cfg = {"ntfy": {"topic": "my-test-topic", "server": "https://ntfy.sh"}}
        with mock.patch.object(fh.urllib.request, "urlopen", return_value=resp):
            self.assertTrue(fh.notify_ntfy("t", "m", cfg))


# ===========================================================================
# Dispatch
# ===========================================================================
class DispatchTests(unittest.TestCase):
    def test_runs_selected_and_handles_unknown(self):
        calls = []

        def fake(title, message, cfg):
            calls.append((title, message))
            return True

        with mock.patch.dict(fh.NOTIFIERS, {"fake": fake}, clear=False):
            res = fh.dispatch("T", "M", {"notifiers": ["fake", "does-not-exist"]})
        self.assertEqual(res, {"fake": True})
        self.assertEqual(calls, [("T", "M")])

    def test_isolates_failing_notifier(self):
        def boom(title, message, cfg):
            raise RuntimeError("broken")

        with mock.patch.dict(fh.NOTIFIERS, {"boom": boom}, clear=False):
            res = fh.dispatch("T", "M", {"notifiers": ["boom"]})
        self.assertEqual(res, {"boom": False})

    def test_multiple_notifiers_all_fire(self):
        results_a = []
        results_b = []

        def na(t, m, c):
            results_a.append(True)
            return True

        def nb(t, m, c):
            results_b.append(True)
            return False

        with mock.patch.dict(fh.NOTIFIERS, {"na": na, "nb": nb}, clear=False):
            res = fh.dispatch("X", "Y", {"notifiers": ["na", "nb"]})
        self.assertTrue(res["na"])
        self.assertFalse(res["nb"])
        self.assertEqual(len(results_a), 1)
        self.assertEqual(len(results_b), 1)


# ===========================================================================
# File notifier
# ===========================================================================
class FileNotifierTests(unittest.TestCase):
    def test_writes_file_into_script_dir_at_least(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(fh, "HERE", Path(tmp)), \
                 mock.patch.object(
                     fh.os.path, "expanduser",
                     side_effect=lambda p: p,  # ~/Desktop → not a dir → skipped
                 ):
                ok = fh.notify_file("HOORAY", "IT'S BACK")
            self.assertTrue(ok)
            self.assertTrue((Path(tmp) / "FABLE5_IS_BACK.txt").is_file())

    def test_file_content_contains_title_and_message(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(fh, "HERE", Path(tmp)), \
                 mock.patch.object(
                     fh.os.path, "expanduser",
                     side_effect=lambda p: p,
                 ):
                fh.notify_file("MY TITLE", "MY MESSAGE")
            content = (Path(tmp) / "FABLE5_IS_BACK.txt").read_text(encoding="utf-8")
        self.assertIn("MY TITLE", content)
        self.assertIn("MY MESSAGE", content)

    # --- kind="alive" ---------------------------------------------------------

    def test_alive_kind_writes_alive_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(fh, "HERE", Path(tmp)), \
                 mock.patch.object(fh.os.path, "expanduser", side_effect=lambda p: p):
                fh.notify_file("Hunter alive", "Still hunting", cfg={"_notify_kind": "alive"})
            self.assertTrue((Path(tmp) / "FABLE-5-HUNTER-IS-ALIVE.txt").is_file())
            self.assertFalse((Path(tmp) / "FABLE5_IS_BACK.txt").is_file())

    # --- kind="test" ----------------------------------------------------------

    def test_test_kind_writes_delivery_test_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(fh, "HERE", Path(tmp)), \
                 mock.patch.object(fh.os.path, "expanduser", side_effect=lambda p: p):
                fh.notify_file("Test", "Delivery test", cfg={"_notify_kind": "test"})
            self.assertTrue((Path(tmp) / "DELIVERY-TEST.txt").is_file())
            self.assertFalse((Path(tmp) / "FABLE5_IS_BACK.txt").is_file())

    # --- negative: test and alive must never write the alarming name ----------

    def test_alive_does_not_write_is_back(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(fh, "HERE", Path(tmp)), \
                 mock.patch.object(fh.os.path, "expanduser", side_effect=lambda p: p):
                fh.notify_file("alive", "hunting", cfg={"_notify_kind": "alive"})
            self.assertFalse((Path(tmp) / "FABLE5_IS_BACK.txt").exists())

    def test_test_does_not_write_is_back(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(fh, "HERE", Path(tmp)), \
                 mock.patch.object(fh.os.path, "expanduser", side_effect=lambda p: p):
                fh.notify_file("test", "delivery test", cfg={"_notify_kind": "test"})
            self.assertFalse((Path(tmp) / "FABLE5_IS_BACK.txt").exists())


# ===========================================================================
# Guaranteed delivery
# ===========================================================================
class GuaranteedDeliveryTests(unittest.TestCase):
    """The HOORAY alert must not be marked found until a channel succeeds."""

    def _run_one_iteration(self, cfg, state, check_status, detail="x"):
        """Simulate exactly one hunt loop iteration without real sleep."""
        with mock.patch.object(fh, "check_fable5", return_value=(check_status, detail)), \
             mock.patch.object(fh, "load_state", return_value=state), \
             mock.patch.object(fh, "save_state", side_effect=lambda s: state.update(s)), \
             mock.patch.object(fh, "acquire_lock", return_value=True), \
             mock.patch.object(fh, "_touch_lock"), \
             mock.patch.object(fh, "release_lock"), \
             mock.patch.object(fh, "time") as fake_time:
            fake_time.sleep.side_effect = KeyboardInterrupt
            try:
                fh.hunt(cfg)
            except KeyboardInterrupt:
                pass
        return state

    def test_not_marked_found_when_all_channels_fail(self):
        cfg = dict(fh.DEFAULT_CONFIG)
        cfg["notifiers"] = ["fakechan"]
        state = {
            "found": False, "checks": 0, "last_alive_date": None,
            "pending_alert": False, "error_notified": False,
        }
        with mock.patch.dict(fh.NOTIFIERS, {"fakechan": lambda t, m, c: False}, clear=False):
            state = self._run_one_iteration(cfg, state, fh.AVAILABLE)
        self.assertFalse(state["found"])
        self.assertTrue(state["pending_alert"])

    def test_marked_found_when_a_channel_succeeds(self):
        cfg = dict(fh.DEFAULT_CONFIG)
        cfg["notifiers"] = ["fakechan"]
        state = {
            "found": False, "checks": 0, "last_alive_date": None,
            "pending_alert": False, "error_notified": False,
        }
        with mock.patch.dict(fh.NOTIFIERS, {"fakechan": lambda t, m, c: True}, clear=False):
            state = self._run_one_iteration(cfg, state, fh.AVAILABLE)
        self.assertTrue(state["found"])
        self.assertFalse(state["pending_alert"])


# ===========================================================================
# State transitions
# ===========================================================================
class TransitionTests(GuaranteedDeliveryTests):
    """State transitions in the hunt loop (reuses the one-iteration harness)."""

    def _capture_dispatch(self):
        calls = []
        return calls, mock.patch.object(
            fh, "dispatch",
            side_effect=lambda title, msg, cfg: (calls.append(msg) or {"x": True}),
        )

    def test_gone_again_resets_found_and_no_alive_same_iteration(self):
        cfg = dict(fh.DEFAULT_CONFIG)
        state = {
            "found": True, "checks": 0, "last_alive_date": None,
            "pending_alert": False, "error_notified": False,
        }
        calls, patcher = self._capture_dispatch()
        with patcher:
            state = self._run_one_iteration(cfg, state, fh.UNAVAILABLE)
        self.assertFalse(state["found"])
        # exactly ONE message (gone_again), NOT also alive in the same iteration
        self.assertEqual(len(calls), 1)
        self.assertIn("gone", calls[0].lower())

    def test_error_keeps_found_and_no_false_alarm(self):
        cfg = dict(fh.DEFAULT_CONFIG)
        state = {
            "found": True, "checks": 0, "last_alive_date": None,
            "pending_alert": False, "error_notified": False,
        }
        calls, patcher = self._capture_dispatch()
        with patcher:
            state = self._run_one_iteration(cfg, state, fh.ERROR, detail="no claude")
        self.assertTrue(state["found"])   # found must not change
        self.assertEqual(calls, [])       # no false alarm

    def test_error_warns_once_when_cannot_check_before_found(self):
        cfg = dict(fh.DEFAULT_CONFIG)
        state = {
            "found": False, "checks": 0, "last_alive_date": None,
            "pending_alert": False, "error_notified": False,
        }
        calls, patcher = self._capture_dispatch()
        with patcher:
            state = self._run_one_iteration(cfg, state, fh.ERROR, detail="no claude")
        self.assertEqual(len(calls), 1)
        self.assertTrue(state["error_notified"])

    def test_alive_only_once_per_day(self):
        cfg = dict(fh.DEFAULT_CONFIG)
        today = fh._today()
        state = {
            "found": False, "checks": 0, "last_alive_date": today,
            "pending_alert": False, "error_notified": False,
        }
        calls, patcher = self._capture_dispatch()
        with patcher:
            state = self._run_one_iteration(cfg, state, fh.UNAVAILABLE)
        self.assertEqual(calls, [])  # already sent today → nothing

    def test_pending_alert_redispatches_on_restart(self):
        """If pending_alert=True and now available, we should deliver and mark found."""
        cfg = dict(fh.DEFAULT_CONFIG)
        cfg["notifiers"] = ["fakechan"]
        state = {
            "found": False, "checks": 0, "last_alive_date": None,
            "pending_alert": True, "error_notified": False,
        }
        with mock.patch.dict(fh.NOTIFIERS, {"fakechan": lambda t, m, c: True}, clear=False):
            state = self._run_one_iteration(cfg, state, fh.AVAILABLE)
        self.assertTrue(state["found"])
        self.assertFalse(state["pending_alert"])


# ===========================================================================
# CLI: cmd_check exit codes
# ===========================================================================
class CmdCheckTests(unittest.TestCase):
    def test_exit_mapping(self):
        cfg = dict(fh.DEFAULT_CONFIG)
        for status, code in [
            (fh.AVAILABLE, 0),
            (fh.UNAVAILABLE, 1),
            (fh.ERROR, 2),
        ]:
            with mock.patch.object(fh, "check_fable5", return_value=(status, "detail")):
                self.assertEqual(fh.cmd_check(cfg), code)


# ===========================================================================
# CLI: self-test mode (cmd_test + --test flag)
# ===========================================================================
class CmdTestSelfTestTests(unittest.TestCase):
    def test_runs_detection_and_dispatch(self):
        """cmd_test must call both check_fable5 and dispatch."""
        cfg = dict(fh.DEFAULT_CONFIG)
        cfg["notifiers"] = ["fakechan"]
        dispatched = {}

        def fake_dispatch(title, message, c):
            dispatched["title"] = title
            dispatched["message"] = message
            return {"fakechan": True}

        with mock.patch.object(fh, "check_fable5", return_value=(fh.UNAVAILABLE, "d")) as chk, \
             mock.patch.object(fh, "dispatch", side_effect=fake_dispatch) as disp:
            code = fh.cmd_test(cfg)
        chk.assert_called_once()
        disp.assert_called_once()
        self.assertEqual(code, 0)

    def test_test_message_is_marked_and_contains_real_alert(self):
        """The TEST message must embed the real 'available' alert + TEST markers."""
        cfg = dict(fh.DEFAULT_CONFIG)
        cfg["lang"] = "en"
        captured = {}

        def fake_dispatch(title, message, c):
            captured["title"] = title
            captured["message"] = message
            return {"x": True}

        fh._locale_cache.clear()
        real_alert = fh._msg(cfg, "available")
        with mock.patch.object(fh, "check_fable5", return_value=(fh.UNAVAILABLE, "d")), \
             mock.patch.object(fh, "dispatch", side_effect=fake_dispatch):
            fh.cmd_test(cfg)
        self.assertIn("[TEST]", captured["title"])
        self.assertTrue(captured["message"].startswith("TEST -- "))
        self.assertIn("(delivery test only)", captured["message"])
        self.assertIn(real_alert, captured["message"])

    def test_exit_zero_when_at_least_one_channel_delivers(self):
        cfg = dict(fh.DEFAULT_CONFIG)
        cfg["notifiers"] = ["a", "b"]
        with mock.patch.object(fh, "check_fable5", return_value=(fh.UNAVAILABLE, "d")), \
             mock.patch.dict(fh.NOTIFIERS,
                             {"a": lambda t, m, c: False, "b": lambda t, m, c: True},
                             clear=False):
            code = fh.cmd_test(cfg)
        self.assertEqual(code, 0)

    def test_exit_one_when_no_channel_delivers(self):
        cfg = dict(fh.DEFAULT_CONFIG)
        cfg["notifiers"] = ["a", "b"]
        with mock.patch.object(fh, "check_fable5", return_value=(fh.UNAVAILABLE, "d")), \
             mock.patch.dict(fh.NOTIFIERS,
                             {"a": lambda t, m, c: False, "b": lambda t, m, c: False},
                             clear=False):
            code = fh.cmd_test(cfg)
        self.assertEqual(code, 1)

    def test_exit_one_when_no_notifiers_configured(self):
        cfg = dict(fh.DEFAULT_CONFIG)
        cfg["notifiers"] = []
        with mock.patch.object(fh, "check_fable5", return_value=(fh.UNAVAILABLE, "d")):
            code = fh.cmd_test(cfg)
        self.assertEqual(code, 1)

    def test_main_with_test_flag_no_command(self):
        """main(["--test"]) must route to cmd_test even without a command."""
        with mock.patch.object(fh, "check_fable5", return_value=(fh.UNAVAILABLE, "d")), \
             mock.patch.object(fh, "dispatch", return_value={"x": True}), \
             mock.patch.object(fh, "load_config", return_value=dict(fh.DEFAULT_CONFIG)):
            code = fh.main(["--test"])
        self.assertEqual(code, 0)

    def test_main_with_test_subcommand(self):
        """main(["test"]) must route to cmd_test."""
        with mock.patch.object(fh, "check_fable5", return_value=(fh.UNAVAILABLE, "d")), \
             mock.patch.object(fh, "dispatch", return_value={"x": True}), \
             mock.patch.object(fh, "load_config", return_value=dict(fh.DEFAULT_CONFIG)):
            code = fh.main(["test"])
        self.assertEqual(code, 0)

    def test_main_no_command_no_test_returns_2(self):
        """main([]) with neither command nor --test → help + exit 2."""
        with mock.patch.object(fh, "load_config", return_value=dict(fh.DEFAULT_CONFIG)):
            code = fh.main([])
        self.assertEqual(code, 2)

    def test_main_test_flag_returns_1_when_all_fail(self):
        """--test propagates the failure exit code from cmd_test."""
        with mock.patch.object(fh, "check_fable5", return_value=(fh.UNAVAILABLE, "d")), \
             mock.patch.object(fh, "dispatch", return_value={"x": False}), \
             mock.patch.object(fh, "load_config", return_value=dict(fh.DEFAULT_CONFIG)):
            code = fh.main(["--test"])
        self.assertEqual(code, 1)


# ===========================================================================
# Config
# ===========================================================================
class ConfigTests(unittest.TestCase):
    def test_malformed_config_falls_back_to_defaults(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "config.json"
            bad.write_text("{ this is not valid json", encoding="utf-8")
            with mock.patch.dict(os.environ, {"FABLE5_CONFIG": str(bad)}):
                cfg = fh.load_config()
        self.assertEqual(cfg["model_id"], "claude-fable-5")

    def test_deep_merge_in_load_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "config.json"
            p.write_text(
                json.dumps({"check_interval_minutes": 15}), encoding="utf-8"
            )
            with mock.patch.dict(os.environ, {"FABLE5_CONFIG": str(p)}):
                cfg = fh.load_config()
        self.assertEqual(cfg["check_interval_minutes"], 15)
        self.assertEqual(cfg["model_id"], "claude-fable-5")  # default preserved

    def test_default_notifiers_are_desktop_and_file(self):
        # When no config file is present the defaults apply.
        # We just test the DEFAULT_CONFIG directly since load_config merges from it.
        self.assertIn("desktop", fh.DEFAULT_CONFIG["notifiers"])
        self.assertIn("file", fh.DEFAULT_CONFIG["notifiers"])


# ===========================================================================
# State persistence
# ===========================================================================
class StateTests(unittest.TestCase):
    def test_state_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "state.json"
            with mock.patch.object(fh, "state_path", return_value=p):
                self.assertFalse(fh.load_state()["found"])
                fh.save_state(
                    {"found": True, "found_at": "now",
                     "last_alive_date": "2026-06-14", "checks": 5}
                )
                got = fh.load_state()
        self.assertTrue(got["found"])
        self.assertEqual(got["checks"], 5)

    def test_load_state_fills_missing_keys(self):
        """State saved by an older version (fewer keys) must be migrated."""
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "state.json"
            p.write_text(json.dumps({"found": True}), encoding="utf-8")
            with mock.patch.object(fh, "state_path", return_value=p):
                state = fh.load_state()
        # All _DEFAULT_STATE keys must be present
        for key in fh._DEFAULT_STATE:
            self.assertIn(key, state)
        self.assertTrue(state["found"])

    def test_corrupt_state_falls_back_to_defaults(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "state.json"
            p.write_text("not json", encoding="utf-8")
            with mock.patch.object(fh, "state_path", return_value=p):
                state = fh.load_state()
        self.assertFalse(state["found"])


# ===========================================================================
# Lock
# ===========================================================================
class LockTests(unittest.TestCase):
    def test_acquire_blocks_second_instance(self):
        with tempfile.TemporaryDirectory() as tmp:
            lock = Path(tmp) / "hunter.lock"
            lock.write_text("12345", encoding="utf-8")
            # Fresh file → not stale → must refuse
            with mock.patch.object(fh, "_lock_path", return_value=lock):
                result = fh.acquire_lock(stale_after=9999)
            self.assertFalse(result)

    def test_acquire_takes_over_stale_lock(self):
        with tempfile.TemporaryDirectory() as tmp:
            lock = Path(tmp) / "hunter.lock"
            lock.write_text("12345", encoding="utf-8")
            # Make it appear old by patching time.time so that (now - st_mtime) > stale_after.
            # We keep the real st_mtime but shift what acquire_lock sees as "now".
            real_mtime = lock.stat().st_mtime
            future_now = real_mtime + 9999  # 9999s after file was written → stale
            with mock.patch.object(fh, "_lock_path", return_value=lock), \
                 mock.patch.object(fh.time, "time", return_value=future_now):
                result = fh.acquire_lock(stale_after=60)
            self.assertTrue(result)

    def test_release_removes_lock(self):
        with tempfile.TemporaryDirectory() as tmp:
            lock = Path(tmp) / "hunter.lock"
            lock.write_text("123", encoding="utf-8")
            with mock.patch.object(fh, "_lock_path", return_value=lock):
                fh.release_lock()
            self.assertFalse(lock.exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
