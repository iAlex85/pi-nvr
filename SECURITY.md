# Security Policy

Pi-NVR handles camera credentials and, potentially, footage from inside
your home. We take security issues seriously.

## Reporting a vulnerability

Please **do not** open a public GitHub issue for security vulnerabilities.
Instead, use GitHub's private "Report a vulnerability" feature under the
Security tab of this repository, or email the maintainers listed in the
repository's contact information.

Please include:
- A description of the vulnerability and its potential impact
- Steps to reproduce (or a proof of concept)
- Affected version(s)

We aim to acknowledge reports within 5 business days.

## Supported versions

Security fixes are made against the latest release. Given the small scope
of this project, older releases are not separately patched — please stay
current.

## Design notes relevant to security review

- No default credentials are ever shipped; `install.sh` requires creating
  an admin account interactively.
- Session cookies are signed (itsdangerous), `httponly`, `samesite=lax`,
  and `secure` when HTTPS is enabled. Sessions are stateless (no session
  table), so there's nothing to invalidate server-side on logout beyond
  clearing the cookie — rotate `PI_NVR_SESSION_SECRET` in
  `/etc/pi-nvr/environment` to invalidate all existing sessions at once.
- Camera and ONVIF passwords are encrypted at rest (Fernet/AES) using a
  key in `/etc/pi-nvr/environment`, separate from the database file.
- All mutating API endpoints require authentication; most require the
  `is_admin` flag.
- CSRF: state-changing endpoints are POST/PUT/DELETE only and rely on
  `samesite=lax` cookies; there is no separate CSRF token today. If you
  need this app reachable from a context where that's insufficient (e.g.
  embedded in a third-party page), please open an issue.
- The systemd unit runs as a dedicated non-root `pi-nvr` user with
  `ProtectSystem=strict`, `NoNewPrivileges`, and `PrivateTmp`.
