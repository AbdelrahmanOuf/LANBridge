# Security policy

LANBridge Pro is a development preview handling Windows settings and user files. Sensitive boundaries
include UAC IPC, credentials, path validation, ACL/firewall rollback and explicit source-file cleanup.

Use **Report a vulnerability** in the Security tab if private reporting is enabled. Otherwise contact the
maintainer using a contact method they explicitly publish. Do not post working privilege-escalation exploits
or private diagnostic dumps in public issues. Include commit/version, Windows build, disposable reproduction
and sanitized evidence. Never include passwords, tokens, personal files or Credential Manager exports.

- Signing/guest policy must not be weakened for connectivity.
- Discovery is unverified and never grants remote control/access.
- Frozen elevated code belongs in a protected Program Files installation.
- Source/portable code and unsigned CI artifacts are not a production security review.
- Keep backups and avoid concurrent source modifications while copying.

See docs/ARCHITECTURE.md for boundaries and rollback limits, and docs/WINDOWS_ACCEPTANCE.md for real-device acceptance.
