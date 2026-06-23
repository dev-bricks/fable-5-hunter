# Changelog

All notable changes to this project are documented here.

## [Unreleased]

### Added

- Add German README with setup, notification and discovery context.
- Add `llms.txt` for LLM and search-engine discoverability.
- Add `START.bat` as a Windows-friendly launcher that defaults to `run` and forwards optional CLI arguments.
- Add `install/install_linux.sh` for user-scoped Linux autostart via systemd or cron fallback.
- Add Linux installer contract tests.
- Add GitHub community health files: `CODE_OF_CONDUCT.md`, `SECURITY.md`, and `CONTRIBUTING.md`.

### Changed

- Add README start-here and discovery sections for clearer user onboarding.
- Update package metadata keywords and repository URLs for the `dev-bricks` home.
- Exclude `.SOFTWARE` lock and task-control files from the public repository.
- Document the Windows launcher path in `README.md`.
- Document the Linux autostart path in `README.md` and `README_de.md`.

## [1.0.0] - 2026-06-16

### Added

- Initial public release of the zero-dependency Claude Fable 5 availability watcher.
