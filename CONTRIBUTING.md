# Beitragsrichtlinie / Contributing Guide

## Deutsch

Vielen Dank für Ihr Interesse, zu diesem Projekt beizutragen.

### Wie Sie beitragen können

1. **Bug melden:** Erstellen Sie ein Issue mit einer klaren Reproduktion.
2. **Feature vorschlagen:** Beschreiben Sie den Usecase und warum der Hunter ihn braucht.
3. **Code beitragen:** Öffnen Sie einen Pull Request mit einem kleinen, klar abgegrenzten Paket.

### Pull Requests

1. Forken Sie das Repository
2. Erstellen Sie einen Feature-Branch: `git checkout -b feature/mein-feature`
3. Führen Sie die lokalen Checks aus
4. Committen Sie Ihre Änderungen: `git commit -m "Beschreibung der Änderung"`
5. Pushen Sie den Branch und eröffnen Sie einen Pull Request

### Lokale Checks

```bash
python -m pytest -q
python -m ruff check .
python -m py_compile fable_hunter.py
```

### Projektregeln

- UTF-8 für alle Dateien
- Keine Secrets oder echte Zugangsdaten committen
- Änderungen an Erkennung oder Benachrichtigung nur mit nachvollziehbarem Test
- Windows-Startpfade (`START.bat`, Install-Skripte) bei CLI-Änderungen mitprüfen

---

## English

Thank you for your interest in contributing to this project.

### How to Contribute

1. **Report a bug:** Open an issue with a clear reproduction.
2. **Suggest a feature:** Explain the use case and why the hunter needs it.
3. **Contribute code:** Open a pull request with one small, well-bounded change set.

### Pull Requests

1. Fork the repository
2. Create a feature branch: `git checkout -b feature/my-feature`
3. Run the local checks
4. Commit your changes: `git commit -m "Description of change"`
5. Push the branch and open a pull request

### Local Checks

```bash
python -m pytest -q
python -m ruff check .
python -m py_compile fable_hunter.py
```

### Project Rules

- Use UTF-8 for all files
- Never commit secrets or real credentials
- Any detection or notification change needs a reproducible test
- Recheck Windows launcher paths (`START.bat`, install scripts) when the CLI changes
