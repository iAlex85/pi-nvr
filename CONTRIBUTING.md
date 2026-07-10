# Contributing to Pi-NVR

Thanks for considering a contribution. This project targets a Raspberry
Pi 3 first — please keep that constraint in mind (see
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md#performance-budget-pi-3-target-4-cameras-1080p15)).
A feature that looks fine on a dev laptop can make the software unusable
on the hardware it's meant for.

## Before you start

- For anything beyond a small fix, open an issue first to discuss the
  approach — especially for anything touching the recording pipeline or
  motion detection, where CPU budget is the main design constraint.
- Check `docs/DEVELOPER.md` for project layout and conventions.

## Workflow

1. Fork the repo and create a branch off `main`.
2. Set up a dev environment (see `docs/DEVELOPER.md`).
3. Make your change. Add or update tests in `tests/`.
4. Run `pytest` and make sure it's green.
5. Update `CHANGELOG.md` under "Unreleased".
6. Open a pull request using the PR template. Explain what changed and why,
   and call out any CPU/RAM impact if the change touches recording, motion
   detection, or live view.

## Code style

- PEP 8. Type hints on public functions.
- No new hard dependency on Docker, Node.js, or any cloud service — that's
  a project-level constraint, not a style preference.
- New tunables go in `config/default_config.yaml`, not hardcoded constants.
- Keep `routes.py` thin — business logic belongs in the sibling
  manager/engine module so it's unit-testable without FastAPI.

## Reporting bugs

Open a GitHub issue with:
- Pi model (or other hardware) and OS version
- Camera make/model and protocol (RTSP/ONVIF/MJPEG)
- Relevant excerpt from `journalctl -u pi-nvr -e` or Settings > Logs
- Steps to reproduce

## Reporting security issues

Please see [`SECURITY.md`](SECURITY.md) — do not open a public issue for
anything that could be a security vulnerability.
