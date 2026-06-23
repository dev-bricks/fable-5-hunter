<img src="assets/banner_v2.svg" width="100%" alt="fable-5-hunter - Fable 5 beobachten, ohne selbst dauernd nachzusehen">

# fable-5-hunter

> *"Hunting Fable 5, so you don't have to."*

[English](README.md) | [LLM-Metadaten](llms.txt)

[![MIT License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python 3.9+](https://img.shields.io/badge/python-3.9%2B-brightgreen.svg)](https://www.python.org/)
[![Zero dependencies](https://img.shields.io/badge/dependencies-zero-brightgreen.svg)](#)

`fable-5-hunter` ist ein kleiner lokaler Wächter für die Claude-Code-CLI. Er prüft regelmäßig,
ob **Claude Fable 5** wieder erreichbar ist, und schickt sofort eine Benachrichtigung über
Desktop, Datei, Telegram, Discord oder ntfy.

## Einstieg

| Ziel | Befehl |
|---|---|
| Einmal prüfen | `python fable_hunter.py check` |
| Benachrichtigungen testen | `python fable_hunter.py test-notify` |
| Dauerhaft im Vordergrund laufen lassen | `python fable_hunter.py run` |
| Autostart einrichten | `install/install_linux.sh`, `install/install_macos.sh` oder `install\install_windows.ps1` |

Keine Laufzeit-Abhängigkeiten: Python 3.9+ reicht. Es gibt kein Pflicht-`pip install`
und keine notwendige virtuelle Umgebung.

## Wofür ist das gedacht?

Der Hunter ist für Nutzerinnen und Nutzer gedacht, die Claude Code bereits lokal verwenden
und nicht manuell prüfen möchten, ob Fable 5 wieder verfügbar ist. Er nutzt die vorhandene
Claude-Code-Anmeldung und ruft keinen eigenen Claude-API-Key ab.

Suchkontext:

```text
Claude Fable 5 Availability Watcher
Claude Fable 5 Benachrichtigung
Claude Code Fable 5 prüfen
fable-5-hunter dev-bricks
lokaler Claude Model Watcher
ntfy Telegram Discord Claude Code Alert
```

Abgrenzung: Das Projekt ist kein gehostetes Status-Dashboard, kein Benchmark, kein allgemeiner
Claude-API-Wrapper und kein Jailbreak-Tool. Es ist ein lokaler Watcher für deine eigene
authentifizierte Claude-Code-CLI.

## Wie die Erkennung funktioniert

Der Hunter fragt die Claude-Code-CLI mit einem eindeutigen Token ab:

```bash
claude -p "<unique token>" --model claude-fable-5
```

Nur wenn die CLI mit Exit-Code `0` endet und das Token in `stdout` zurückkommt, gilt Fable 5
als verfügbar. Fehlermeldungen, Fallback-Modelle oder Auth-Probleme lösen keinen falschen
Erfolg aus.

Der Modellname muss exakt `claude-fable-5` lauten. Ein Tippfehler wie `claude-fabel-5` sieht
für die CLI ähnlich aus wie ein nicht verfügbares Modell und würde nie auslösen.

## Benachrichtigungen

| Kanal | Einrichtung | Zweck |
|---|---|---|
| `desktop` | Keine | Lokale Systembenachrichtigung |
| `file` | Keine | Fallback-Datei auf dem Desktop |
| `telegram` | Bot-Token + Chat-ID | Push aufs Smartphone |
| `discord` | Webhook-URL | Discord-Kanal oder DM |
| `ntfy` | Topic-Name | Niedrige Einstiegshürde ohne Account |

Standardmäßig sind `desktop` und `file` aktiv. Für Smartphone-Pushs sind `telegram` oder `ntfy`
am praktischsten, besonders wenn der Hunter auf einem immer laufenden Rechner läuft.

## Konfiguration

Kopiere `config.example.json` nach `config.json` und passe die Werte an.

Suchreihenfolge: `$FABLE5_CONFIG` -> `./config.json` -> `~/.config/fable5hunter/config.json`.

Wichtige Optionen:

| Schlüssel | Standard | Bedeutung |
|---|---|---|
| `model_id` | `claude-fable-5` | Modell-ID für die Prüfung |
| `check_interval_minutes` | `30` | Prüfintervall, solange Fable 5 nicht verfügbar ist |
| `post_found_interval_minutes` | `360` | Langsameres Intervall nach erfolgreichem Fund |
| `notifiers` | `["desktop","file"]` | Aktive Benachrichtigungskanäle |
| `lang` | `"en"` | Sprache: `en`, `de`, `es`, `zh`, `ja`, `ru`, `auto` |
| `alert_retry_seconds` | `60` | Wiederholungsintervall, wenn kein Kanal zugestellt hat |

## Autostart

Windows:

```powershell
powershell -ExecutionPolicy Bypass -File install\install_windows.ps1
```

macOS:

```bash
bash install/install_macos.sh
```

Linux:

```bash
bash install/install_linux.sh
```

Das Skript richtet bevorzugt einen user-scoped systemd-Dienst ein. Auf kleinen
Hosts ohne laufende systemd-User-Session kannst du den Cron-Fallback erzwingen:

```bash
bash install/install_linux.sh --cron
```

Status und Entfernung:

```bash
bash install/install_linux.sh --status
bash install/install_linux.sh --uninstall
```

Der Linux-Autostart braucht kein `sudo`; Logs liegen unter
`~/.local/state/fable5hunter/`.

## Lizenz

MIT, siehe [LICENSE](LICENSE). Copyright 2026 Lukas Geiger.
