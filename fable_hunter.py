#!/usr/bin/env python3
"""
fable-5-hunter
==============

Watches around the clock for **Claude Fable 5** to become available again in
the Claude Code CLI, and sends a notification the moment it is back
("HOORAY, IT'S BACK!").

Background: Claude Fable 5 (Mythos-class) was released on 2026-06-09 and
suspended on 2026-06-12 under a US export-control directive. Anthropic is
working to restore access — no timeline given. This tool tells you the second
it returns.

Detection (no API key required — works for any Claude Code user):
    claude -p "<token-prompt>" --model claude-fable-5
Available  <=> the model echoes a unique token back on stdout (rc == 0).
Not there  <=> "currently unavailable" / "issue with the selected model".

Zero-dependency: Python standard library only (Python >= 3.9).

CLI:
    fable5-hunter check          # one-shot check, prints status + exit code
    fable5-hunter run            # persistent daemon (loop, 24/7)
    fable5-hunter test-notify    # fire a test notification on all active channels
    fable5-hunter status         # show saved state and configuration

Exit codes for `check`: 0 = available, 1 = unavailable, 2 = error.
"""
from __future__ import annotations

import argparse
import base64
import datetime as _dt
import json
import locale
import logging
import os
import platform
import shutil
import signal
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

APP_NAME = "Fable 5 Hunter"
VERSION = "1.0.0"
HERE = Path(__file__).resolve().parent

# Unique echo token: only the real model returns this → no false-positive from
# error messages or fallback models.
ECHO_TOKEN = "FABLE5_HUNTER_OK_9b2f"
DETECT_PROMPT = f"Reply with only this exact token and nothing else: {ECHO_TOKEN}"

# Auth/login errors: claude cannot check at all → ERROR (no false alarm, and no
# misleading "Fable 5 not available" message).
AUTH_ERROR_MARKERS = (
    "not logged in",
    "please run /login",
    "invalid x-api-key",
    "authentication_error",
    "could not resolve account",
)

# Text fragments that signal "not available". Checked as case-insensitive
# substrings, so partial matches (e.g. "fable-mythos-access") are caught too.
UNAVAILABLE_MARKERS = (
    "is currently unavailable",
    "unavailable",
    "fable-mythos-access",
    "issue with the selected model",
    "may not exist or you may not have access",
    "model is overloaded",
    "overloaded_error",
)

# Status constants
AVAILABLE = "available"
UNAVAILABLE = "unavailable"
ERROR = "error"

log = logging.getLogger("fable.hunter")


# --------------------------------------------------------------------------- #
# i18n
# --------------------------------------------------------------------------- #
_locale_cache: dict[str, dict] = {}


def _load_locale(lang: str) -> dict:
    """Load a locale file from locales/<lang>.json; falls back to 'en'."""
    if lang in _locale_cache:
        return _locale_cache[lang]
    p = HERE / "locales" / f"{lang}.json"
    if p.is_file():
        try:
            with open(p, encoding="utf-8") as f:
                data = json.load(f)
            _locale_cache[lang] = data
            return data
        except Exception as exc:
            log.warning("Could not load locale %s: %s", lang, exc)
    if lang != "en":
        return _load_locale("en")
    return {}


def _detect_system_lang() -> str:
    """Detect system language from ENV or OS locale; returns 'en' as default."""
    for var in ("LANG", "LANGUAGE", "LC_ALL", "LC_MESSAGES"):
        val = os.environ.get(var, "")
        if val:
            code = val.split(".")[0].split("_")[0].lower()
            if code:
                return code
    try:
        loc = locale.getdefaultlocale()[0] or ""
        code = loc.split("_")[0].lower()
        if code:
            return code
    except Exception:
        pass
    return "en"


def t(key: str, lang: str, **kwargs) -> str:
    """
    Look up a translation key for the given language.
    Falls back to English if the key or language is missing.
    Supports simple {placeholder} substitution via kwargs.
    """
    data = _load_locale(lang)
    text = data.get(key)
    if text is None and lang != "en":
        text = _load_locale("en").get(key)
    if text is None:
        text = key  # last resort: return the key itself
    if kwargs:
        try:
            text = text.format(**kwargs)
        except Exception:
            pass
    return text


# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #
DEFAULT_CONFIG: dict = {
    "model_id": "claude-fable-5",
    "check_interval_minutes": 30,
    "post_found_interval_minutes": 360,
    "claude_timeout_seconds": 120,
    # Order = fallback priority. All active channels fire; the "HOORAY" alert is
    # only considered delivered once at least one returns True.
    # Default: desktop + file (work without any setup).
    "notifiers": ["desktop", "file"],
    "alert_retry_seconds": 60,
    # Language for built-in messages. "auto" = detect from system locale.
    # Supported: "en", "de". More languages can be added under locales/.
    "lang": "en",
    # Connector credentials (all optional — only needed if the notifier is listed
    # in "notifiers"). Leave empty to skip.
    "telegram": {
        "bot_token": "",
        "owner_id": ""
    },
    "discord": {
        "webhook_url": ""
    },
    "ntfy": {
        "topic": "",
        "server": "https://ntfy.sh"
    },
    # messages: override individual strings (optional). If absent, locale files
    # under locales/ are used. Keys: available, alive, gone_again.
    "messages": {}
}


def _deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge override into a copy of base."""
    out = dict(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_config() -> dict:
    """
    Load configuration from (in order):
      1. $FABLE5_CONFIG environment variable (path to a JSON file)
      2. ./config.json  (next to the script)
      3. ~/.config/fable5hunter/config.json

    Missing keys fall back to DEFAULT_CONFIG via deep merge.
    """
    candidates = []
    env_path = os.environ.get("FABLE5_CONFIG")
    if env_path:
        candidates.append(Path(env_path))
    candidates.append(HERE / "config.json")
    candidates.append(Path(os.path.expanduser("~/.config/fable5hunter/config.json")))

    cfg = dict(DEFAULT_CONFIG)
    for p in candidates:
        try:
            if p.is_file():
                with open(p, encoding="utf-8") as f:
                    cfg = _deep_merge(cfg, json.load(f))
                log.info("Config loaded: %s", p)
                break
        except Exception as e:
            log.warning("Config %s unreadable: %s", p, e)
    return cfg


def _resolve_lang(cfg: dict) -> str:
    """Return the effective language code for this session."""
    lang = cfg.get("lang", "en") or "en"
    if lang == "auto":
        lang = _detect_system_lang()
    return lang.lower()


def _msg(cfg: dict, key: str, **kwargs) -> str:
    """
    Return the message for 'key'.
    Priority: cfg["messages"][key] override → locale file → key itself.
    """
    override = cfg.get("messages", {}) or {}
    if key in override and override[key]:
        text = override[key]
        if kwargs:
            try:
                text = text.format(**kwargs)
            except Exception:
                pass
        return text
    lang = _resolve_lang(cfg)
    return t(key, lang, **kwargs)


# --------------------------------------------------------------------------- #
# State persistence
# --------------------------------------------------------------------------- #
def state_path() -> Path:
    d = Path(os.path.expanduser("~/.config/fable5hunter"))
    d.mkdir(parents=True, exist_ok=True)
    return d / "state.json"


_DEFAULT_STATE: dict = {
    "found": False,
    "found_at": None,
    "last_alive_date": None,
    "checks": 0,
    "pending_alert": False,
    "error_notified": False,
}


def load_state() -> dict:
    """Load state from disk, filling missing keys from _DEFAULT_STATE."""
    state = dict(_DEFAULT_STATE)
    p = state_path()
    if p.is_file():
        try:
            with open(p, encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                state.update(loaded)  # fill any missing keys from the default
        except Exception:
            pass
    return state


def save_state(state: dict) -> None:
    try:
        with open(state_path(), "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2, ensure_ascii=False)
    except Exception as e:
        log.warning("Could not save state: %s", e)


# --------------------------------------------------------------------------- #
# Detection
# --------------------------------------------------------------------------- #
def find_claude() -> str | None:
    """Locate the claude CLI (resolves .cmd/.ps1 shims on Windows too)."""
    return shutil.which("claude")


def check_fable5(cfg: dict) -> tuple[str, str]:
    """
    Run a single availability check.
    Returns: (status, detail) where status is one of AVAILABLE / UNAVAILABLE / ERROR.

    Detection rules (must be preserved exactly):
    - Uses `claude -p "<prompt>" --model <model_id>` — NO --fallback-model flag.
    - AVAILABLE only if ALL of the following hold:
          * returncode == 0
          * ECHO_TOKEN appears in proc.stdout (real, non-empty model response)
          * combined stdout+stderr contains NONE of the negative markers.
      The CLI startup banner or any other non-token output is NOT proof of
      availability.
    - stdout token in stderr alone does NOT count (prevents false-positives).
    - UNAVAILABLE_MARKERS are checked case-insensitively as substrings in the
      combined stdout+stderr. This catches "is currently unavailable",
      "unavailable", "fable-mythos-access", etc.
    - TimeoutExpired → UNAVAILABLE (model hung, treat as unavailable).
    - Anything without a clear positive token echo → conservatively UNAVAILABLE.
    """
    claude = find_claude()
    if not claude:
        return ERROR, "claude CLI not found in PATH (is Claude Code installed?)"

    model = cfg.get("model_id", "claude-fable-5")
    try:
        timeout = _int_cfg(cfg, "claude_timeout_seconds", 120)
    except ValueError as e:
        return ERROR, str(e)
    cmd = [claude, "-p", DETECT_PROMPT, "--model", model]
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env=env,
        )
    except subprocess.TimeoutExpired:
        return UNAVAILABLE, f"Timeout after {timeout}s (likely overloaded or hanging)"
    except FileNotFoundError:
        return ERROR, "claude CLI not executable"
    except Exception as e:
        return ERROR, f"Invocation failed: {e}"

    stdout = proc.stdout or ""
    stderr = proc.stderr or ""
    combined = stdout + "\n" + stderr
    low = combined.lower()

    # Auth/login errors first: claude cannot check → ERROR instead of a wrong
    # "Fable 5 not available".
    for marker in AUTH_ERROR_MARKERS:
        if marker in low:
            return ERROR, "claude not logged in / auth error: " + combined.strip()[:200]

    # Negative signals win over any other signal. Even if the unique token were
    # somehow echoed inside an error page/banner, an unavailable marker means
    # Fable 5 is not really reachable.
    for marker in UNAVAILABLE_MARKERS:
        if marker in low:
            return UNAVAILABLE, combined.strip()[:300]

    # Unambiguously available: the model echoed our unique token on stdout.
    # (stdout only — an echo on stderr must not cause a false-positive.)
    if ECHO_TOKEN in stdout and proc.returncode == 0:
        return AVAILABLE, "Token echo received — Fable 5 is responding."

    # rc==0 with non-token, non-negative output (e.g. the misleading startup
    # banner) → conservative UNAVAILABLE. Banner text is NOT proof.
    if proc.returncode == 0 and combined.strip():
        return UNAVAILABLE, "Unexpected response without token: " + combined.strip()[:200]

    return UNAVAILABLE, (combined.strip()[:300] or f"Exit {proc.returncode}, no output")


# --------------------------------------------------------------------------- #
# Notifiers (pluggable)
# --------------------------------------------------------------------------- #
def _resolve_telegram(cfg: dict) -> tuple[str, str]:
    """
    Resolve Telegram bot token + chat ID from (in order):
      1. cfg["telegram"]["bot_token"] / ["owner_id"]
      2. ENV TELEGRAM_BOT_TOKEN / TELEGRAM_OWNER_ID
      3. ~/.credentials/telegram_bot_token (plain-text file, optional)
      4. ~/.config/bach/telegram_chat.json (optional BACH fallback — only
         relevant if you run the BACH assistant system; most users won't have this)
    """
    tg = cfg.get("telegram", {})
    if not isinstance(tg, dict):
        tg = {}
    token = tg.get("bot_token", "")
    chat = tg.get("owner_id", "")

    if not token:
        token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    if not chat:
        chat = os.environ.get("TELEGRAM_OWNER_ID", "")

    if not token:
        tf = Path(os.path.expanduser("~/.credentials/telegram_bot_token"))
        if tf.is_file():
            try:
                token = tf.read_text(encoding="utf-8").strip()
            except Exception:
                pass
    if not chat:
        of = Path(os.path.expanduser("~/.credentials/telegram_owner_id"))
        if of.is_file():
            try:
                chat = of.read_text(encoding="utf-8").strip()
            except Exception:
                pass

    # Optional BACH fallback — only used if nothing else was found
    if (not token or not chat):
        bach_cfg = Path(os.path.expanduser("~/.config/bach/telegram_chat.json"))
        if bach_cfg.is_file():
            try:
                with open(bach_cfg, encoding="utf-8") as f:
                    data = json.load(f)
                token = token or data.get("bot_token", "")
                chat = chat or data.get("owner_id", "")
            except Exception:
                pass

    return token, chat


def notify_telegram(title: str, message: str, cfg: dict) -> bool:
    """
    Send a Telegram message via the Bot API (direct HTTP POST, no library).
    Requires bot_token + owner_id (chat ID). See README for setup instructions.
    """
    token, chat = _resolve_telegram(cfg)
    if not token or not chat:
        log.warning("Telegram: no bot_token/owner_id found — skipping.")
        return False
    text = f"*{title}*\n{message}"
    data = urllib.parse.urlencode(
        {"chat_id": chat, "text": text, "parse_mode": "Markdown"}
    ).encode()
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        req = urllib.request.Request(url, data=data)
        with urllib.request.urlopen(req, timeout=15) as resp:
            ok = resp.status == 200
        log.info("Telegram sent: %s", ok)
        return ok
    except Exception as e:
        log.warning("Telegram send failed: %s", e)
        return False


def notify_discord(title: str, message: str, cfg: dict) -> bool:
    """
    Post to a Discord channel via webhook URL.
    Set cfg["discord"]["webhook_url"] to enable.
    """
    webhook_url = (cfg.get("discord", {}) or {}).get("webhook_url", "")
    if not webhook_url:
        log.warning("Discord: no webhook_url configured — skipping.")
        return False
    content = f"**{title}**\n{message}"
    payload = json.dumps({"content": content}).encode("utf-8")
    try:
        req = urllib.request.Request(
            webhook_url,
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            ok = resp.status in (200, 204)
        log.info("Discord sent: %s", ok)
        return ok
    except Exception as e:
        log.warning("Discord send failed: %s", e)
        return False


def notify_ntfy(title: str, message: str, cfg: dict) -> bool:
    """
    Send a push notification via ntfy.sh (or a self-hosted ntfy server).
    Lowest barrier: no account needed, just pick a unique topic name.
    Set cfg["ntfy"]["topic"] to enable.

    Subscribe on any device:
      - Web: https://ntfy.sh/<topic>
      - Android/iOS: install the ntfy app, subscribe to the topic
    """
    ntfy_cfg = cfg.get("ntfy", {}) or {}
    topic = ntfy_cfg.get("topic", "")
    if not topic:
        log.warning("ntfy: no topic configured — skipping.")
        return False
    server = ntfy_cfg.get("server", "https://ntfy.sh").rstrip("/")
    url = f"{server}/{topic}"
    try:
        req = urllib.request.Request(
            url,
            data=message.encode("utf-8"),
            headers={"Title": title},
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            ok = resp.status == 200
        log.info("ntfy sent: %s", ok)
        return ok
    except Exception as e:
        log.warning("ntfy send failed: %s", e)
        return False


def notify_desktop(title: str, message: str, cfg: dict | None = None) -> bool:
    """
    Native desktop notification:
      - Windows: WinRT Toast (PowerShell) with MessageBox fallback
      - macOS:   osascript display notification
      - Linux:   notify-send
    Works out of the box — no configuration needed.
    """
    system = platform.system()
    try:
        if system == "Windows":
            return _toast_windows(title, message)
        if system == "Darwin":
            return _toast_macos(title, message)
        return _toast_linux(title, message)
    except Exception as e:
        log.warning("Desktop notify failed: %s", e)
        return False


_PS_TOAST = r"""
$ErrorActionPreference = 'Stop'
$title = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($env:F5_TITLE_B64))
$msg   = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($env:F5_MSG_B64))
try {
  [Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] > $null
  $t = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02)
  $n = $t.GetElementsByTagName('text')
  $n.Item(0).AppendChild($t.CreateTextNode($title)) > $null
  $n.Item(1).AppendChild($t.CreateTextNode($msg)) > $null
  $toast = [Windows.UI.Notifications.ToastNotification]::new($t)
  $notifier = [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('Fable 5 Hunter')
  $notifier.Show($toast)
} catch {
  Add-Type -AssemblyName System.Windows.Forms
  [System.Windows.Forms.MessageBox]::Show($msg, $title) > $null
}
"""


def _toast_windows(title: str, message: str) -> bool:
    env = dict(os.environ)
    env["F5_TITLE_B64"] = base64.b64encode(title.encode("utf-8")).decode()
    env["F5_MSG_B64"] = base64.b64encode(message.encode("utf-8")).decode()
    # Prefer Windows PowerShell 5.1; fall back to pwsh (7+)
    psexe = shutil.which("powershell") or shutil.which("pwsh")
    if not psexe:
        return False
    proc = subprocess.run(
        [psexe, "-NoProfile", "-NonInteractive", "-Command", _PS_TOAST],
        env=env, capture_output=True, text=True, timeout=30,
    )
    return proc.returncode == 0


def _toast_macos(title: str, message: str) -> bool:
    env = dict(os.environ)
    env["F5_TITLE"] = title
    env["F5_MSG"] = message
    script = ('display notification (system attribute "F5_MSG") '
              'with title (system attribute "F5_TITLE") sound name "Glass"')
    proc = subprocess.run(
        ["osascript", "-e", script],
        env=env, capture_output=True, text=True, timeout=30,
    )
    return proc.returncode == 0


def _toast_linux(title: str, message: str) -> bool:
    if not shutil.which("notify-send"):
        return False
    proc = subprocess.run(
        ["notify-send", title, message],
        capture_output=True, text=True, timeout=30,
    )
    return proc.returncode == 0


def notify_file(title: str, message: str, cfg: dict | None = None) -> bool:
    """
    Last-resort fallback: write a prominently named file to the Desktop (and
    next to this script). Even if no other channel works, you will see this
    file.

    On Windows, both ~/OneDrive/Desktop and ~/Desktop are checked, because
    the Desktop may be synced to OneDrive.

    The file name conveys the notification kind so that ONLY a genuine
    availability find produces the alarming ``FABLE5_IS_BACK.txt`` name:

    * ``"found"``  (default) → ``FABLE5_IS_BACK.txt``  — alarming; real find
    * ``"alive"``            → ``FABLE-5-HUNTER-IS-ALIVE.txt``  — heartbeat
    * ``"test"``             → ``DELIVERY-TEST.txt``  — delivery / self-test

    Callers pass the kind via the internal config key ``"_notify_kind"``
    (underscore-prefix = private, never set by users in config.json).
    """
    kind = (cfg or {}).get("_notify_kind", "found")
    _KIND_TO_FILENAME = {
        "found": "FABLE5_IS_BACK.txt",
        "alive": "FABLE-5-HUNTER-IS-ALIVE.txt",
        "test":  "DELIVERY-TEST.txt",
    }
    filename = _KIND_TO_FILENAME.get(kind, "FABLE5_IS_BACK.txt")

    stamp = _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    body = f"{title}\n{message}\n\n(Fable-5-Hunter {VERSION}, {stamp})\n"
    targets = []
    for cand in ("~/OneDrive/Desktop", "~/Desktop"):
        d = Path(os.path.expanduser(cand))
        if d.is_dir():
            targets.append(d / filename)
    targets.append(HERE / filename)
    ok = False
    for target in targets:
        try:
            target.write_text(body, encoding="utf-8")
            ok = True
        except Exception as e:
            log.warning("file notifier could not write %s: %s", target, e)
    return ok


# Notifier registry — add new channels here (function signature: notify_x(title, message, cfg) -> bool)
NOTIFIERS: dict[str, object] = {
    "telegram": notify_telegram,
    "discord": notify_discord,
    "ntfy": notify_ntfy,
    "desktop": notify_desktop,
    "file": notify_file,
}


def dispatch(title: str, message: str, cfg: dict) -> dict[str, bool]:
    """
    Fire all active notifiers and return a dict of {name: success}.
    Unknown notifier names are logged and skipped; exceptions are isolated.
    """
    results: dict[str, bool] = {}
    names = cfg.get("notifiers") or []
    if isinstance(names, str):
        names = [names]
    elif not isinstance(names, (list, tuple)):
        names = []
    for name in names:
        fn = NOTIFIERS.get(name)
        if not fn:
            log.warning("Unknown notifier: %s", name)
            continue
        try:
            results[name] = bool(fn(title, message, cfg))  # type: ignore[call-arg]
        except Exception as e:
            log.warning("Notifier %s raised: %s", name, e)
            results[name] = False
    log.info("Notification '%s' → %s", title, results)
    return results


# --------------------------------------------------------------------------- #
# Service loop
# --------------------------------------------------------------------------- #
def _today() -> str:
    return _dt.date.today().isoformat()


def _lock_path() -> Path:
    return state_path().parent / "hunter.lock"


def acquire_lock(stale_after: float) -> bool:
    """
    Heartbeat-based single-instance lock. Does NOT use os.kill (unsafe on
    Windows). Staleness is detected by the age of the lock file, which is
    refreshed every loop iteration via _touch_lock().
    """
    p = _lock_path()
    if p.exists():
        try:
            age = time.time() - p.stat().st_mtime
        except Exception:
            age = float("inf")
        if age < stale_after:
            log.error(
                "Another instance is active (lock %.0fs old) — refusing to start a second one.",
                age,
            )
            return False
        log.warning("Stale lock (%.0fs old) — taking over.", age)
    try:
        p.write_text(str(os.getpid()), encoding="utf-8")
        return True
    except Exception as e:
        log.warning("Cannot write lock (%s) — continuing without lock.", e)
        return True  # best-effort; never block on lock failure


def _touch_lock() -> None:
    """Update lock file mtime to signal this instance is still alive."""
    try:
        _lock_path().write_text(str(os.getpid()), encoding="utf-8")
    except Exception:
        pass


def release_lock() -> None:
    try:
        _lock_path().unlink()
    except Exception:
        pass


def _int_cfg(cfg: dict, key: str, default: int, minimum: int = 1) -> int:
    """Read a positive int config value, raising ValueError on bad type or value.

    Guards against config typos like "claude_timeout_seconds": "abc"/null (bad type)
    AND against non-positive values like -5 or 0 which would otherwise crash the
    daemon (time.sleep(-n) raises) or busy-loop it (time.sleep(0) hammers the CLI).
    The raw error would surface far from the config source; callers catch ValueError.
    """
    try:
        value = int(cfg.get(key, default))
    except (ValueError, TypeError) as err:
        raise ValueError(f"config: {key} must be an integer") from err
    if value < minimum:
        raise ValueError(f"config: {key} must be >= {minimum}")
    return value


def hunt(cfg: dict) -> int:
    """Entry point for the persistent daemon. Runs until interrupted."""
    try:
        interval = _int_cfg(cfg, "check_interval_minutes", 30) * 60
        post_found = _int_cfg(cfg, "post_found_interval_minutes", 360) * 60
        retry = _int_cfg(cfg, "alert_retry_seconds", 60)
    except ValueError as e:
        log.error("%s", e)
        return 1
    state = load_state()

    stale_after = max(interval, post_found) * 2 + 60
    if not acquire_lock(stale_after):
        return 1

    # Release the lock on SIGTERM/SIGINT (launchd/systemd send SIGTERM on stop and
    # on KeepAlive restart). Without this, a killed instance leaves a fresh lock
    # that blocks every restart until stale_after — the daemon could never come back.
    def _release_and_exit(signum, frame):
        release_lock()
        sys.exit(0)
    for _sig in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(_sig, _release_and_exit)
        except (ValueError, OSError):
            pass  # e.g. not in main thread / unsupported on platform

    log.info(_msg(cfg, "started", interval=interval // 60))
    try:
        return _hunt_loop(cfg, state, interval, post_found, retry)
    finally:
        release_lock()


def _hunt_loop(cfg: dict, state: dict, interval: int, post_found: int, retry: int) -> int:
    """Inner loop — separated so tests can intercept time.sleep."""
    while True:
        status, detail = check_fable5(cfg)
        state["checks"] = state.get("checks", 0) + 1
        log.info("Check #%d: %s (%s)", state["checks"], status, detail[:120])

        if status == AVAILABLE:
            state["error_notified"] = False
            if not state.get("found"):
                # Guaranteed delivery: fire all channels, mark as found only
                # when at least one succeeds; otherwise set pending_alert and
                # retry quickly (no spam once delivered).
                results = dispatch(APP_NAME, _msg(cfg, "available"), {**cfg, "_notify_kind": "found"})
                if any(results.values()):
                    state["found"] = True
                    state["pending_alert"] = False
                    state["found_at"] = _dt.datetime.now().isoformat(timespec="seconds")
                    log.info(
                        "FOUND — alert delivered via: %s",
                        [k for k, v in results.items() if v],
                    )
                    sleep_for = post_found
                else:
                    state["pending_alert"] = True
                    log.error(
                        "Fable 5 is AVAILABLE but NO channel got through — "
                        "retrying in %ds.",
                        retry,
                    )
                    sleep_for = retry
            else:
                sleep_for = post_found  # already found → slow polling

        elif status == ERROR:
            # Local/config error (e.g. claude not in PATH). Do NOT touch `found`
            # to avoid a false "gone" alarm from a transient local problem.
            # Warn once so the user knows the hunter can't check.
            log.error("Cannot check: %s", detail)
            if not state.get("found") and not state.get("error_notified"):
                dispatch(APP_NAME, _msg(cfg, "cannot_check", detail=detail), {**cfg, "_notify_kind": "alive"})
                state["error_notified"] = True
            sleep_for = interval

        else:  # UNAVAILABLE
            state["error_notified"] = False
            if state.get("found"):
                # Was available, now gone again → resume hunting.
                # Use elif so gone_again and alive never fire in the same iteration.
                dispatch(APP_NAME, _msg(cfg, "gone_again"), {**cfg, "_notify_kind": "alive"})
                state["found"] = False
            elif state.get("last_alive_date") != _today():
                # Daily heartbeat (also fires immediately on first run).
                dispatch(APP_NAME, _msg(cfg, "alive"), {**cfg, "_notify_kind": "alive"})
                state["last_alive_date"] = _today()
            sleep_for = interval

        save_state(state)
        _touch_lock()
        time.sleep(sleep_for)


# --------------------------------------------------------------------------- #
# CLI commands
# --------------------------------------------------------------------------- #
def cmd_check(cfg: dict) -> int:
    status, detail = check_fable5(cfg)
    print(f"[{status.upper()}] {detail}")
    return {AVAILABLE: 0, UNAVAILABLE: 1, ERROR: 2}[status]


def cmd_test_notify(cfg: dict) -> int:
    res = dispatch(APP_NAME, _msg(cfg, "test_notify"), {**cfg, "_notify_kind": "test"})
    ok = any(res.values())
    if ok:
        print("OK — at least one notifier succeeded:", [k for k, v in res.items() if v])
    else:
        print("No notifier succeeded — check config.json and notifier credentials.")
    return 0 if ok else 1


def cmd_test(cfg: dict) -> int:
    """
    Self-test mode: verify that BOTH the trigger/detection AND every configured
    delivery channel work.

    Steps:
      (a) Run the real detection once (check_fable5) and print the status.
      (b) Send a clearly marked TEST notification through ALL configured
          notifiers, so the user can confirm a TEST message arrives on every
          channel.

    The TEST message embeds the real "available" alert text (from locales /
    messages), prefixed with "TEST -- " and a "(delivery test only)" note, so
    the user sees exactly what the real alert will look like.

    Exit code: 0 if at least one channel delivered, otherwise 1.
    """
    # (a) Real trigger/detection
    print("== Fable 5 Hunter self-test ==")
    print("[1/2] Running real detection (check_fable5) ...")
    status, detail = check_fable5(cfg)
    print(f"      Detection result: [{status.upper()}] {detail}")

    # (b) TEST notification across all configured channels
    real_alert = _msg(cfg, "available")
    test_message = f"TEST -- {real_alert} (delivery test only)"
    test_title = f"{APP_NAME} [TEST]"

    notifiers = cfg.get("notifiers", [])
    print(f"[2/2] Sending a TEST notification to all configured channels: {notifiers}")
    results = dispatch(test_title, test_message, {**cfg, "_notify_kind": "test"})

    print("\nChannel results:")
    if not results:
        print("  (no notifiers configured — set 'notifiers' in config.json)")
    for name in notifiers:
        ok = results.get(name)
        if ok is None:
            print(f"  - {name:<10} SKIPPED (unknown notifier)")
        elif ok:
            print(f"  - {name:<10} OK")
        else:
            print(f"  - {name:<10} FAILED")

    delivered = any(results.values())
    print()
    if delivered:
        print("Self-test PASSED — at least one channel delivered:",
              [k for k, v in results.items() if v])
    else:
        print("Self-test FAILED — no channel delivered. Check config.json "
              "and notifier credentials.")
    return 0 if delivered else 1


def cmd_status(cfg: dict) -> int:
    state = load_state()
    print(json.dumps(state, indent=2, ensure_ascii=False))
    print(f"\nActive notifiers : {cfg.get('notifiers')}")
    print(f"Language         : {_resolve_lang(cfg)}")
    tok, chat = _resolve_telegram(cfg)
    print(f"Telegram         : token={'yes' if tok else 'no'}, chat={'yes' if chat else 'no'}")
    discord_url = (cfg.get("discord", {}) or {}).get("webhook_url", "")
    print(f"Discord          : {'configured' if discord_url else 'not configured'}")
    ntfy_topic = (cfg.get("ntfy", {}) or {}).get("topic", "")
    print(f"ntfy             : {'topic=' + ntfy_topic if ntfy_topic else 'not configured'}")
    print(f"claude CLI       : {find_claude() or 'NOT FOUND'}")
    return 0


def main(argv=None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    parser = argparse.ArgumentParser(
        prog="fable5-hunter",
        description=(
            "Watches for Claude Fable 5 availability and notifies you the moment it is back."
        ),
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")
    parser.add_argument(
        "--test",
        action="store_true",
        help=(
            "self-test: run the real detection once AND send a clearly marked "
            "TEST notification through ALL configured channels"
        ),
    )
    parser.add_argument(
        "command",
        nargs="?",
        default=None,
        choices=["check", "run", "test", "test-notify", "status"],
        help=(
            "check = one-shot check (exit 0/1/2), "
            "run = persistent daemon, "
            "test = self-test (trigger + notify all channels), "
            "test-notify = fire a test notification, "
            "status = show saved state"
        ),
    )
    args = parser.parse_args(argv)
    cfg = load_config()

    # --test flag OR the `test` subcommand → self-test mode
    if args.test or args.command == "test":
        return cmd_test(cfg)

    # No command and no --test → show help and signal misuse.
    if args.command is None:
        parser.print_help()
        return 2

    if args.command == "check":
        return cmd_check(cfg)
    if args.command == "run":
        try:
            return hunt(cfg)
        except KeyboardInterrupt:
            log.info("Stopped.")
            return 0
    if args.command == "test-notify":
        return cmd_test_notify(cfg)
    if args.command == "status":
        return cmd_status(cfg)
    return 0


if __name__ == "__main__":
    sys.exit(main())
