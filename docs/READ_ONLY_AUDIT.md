# Read-only AGF audit sessions

AGF now distinguishes an **offline static audit** from normal Architect/Implementer
delivery. This intentionally does not claim production-quality autonomous
research or an Owner-approved implementation.

## Supported workflow

1. Register a separate, **clean** repository checkout with no live execution
   and no delivery permissions. This is the default when using
   `agf-governed project add --name audit-repo --repository /absolute/path --json`.
2. Start a canonical session with an objective that explicitly says "audit",
   "auditoria" or "auditoría" (for example, "Auditoría profunda solo lectura de Kodi").
   `agf-governed session start --project PROJECT --goal 'Auditoría ...' --json`.
3. Run `agf-governed session audit-readonly --session SESSION --json`.
   This command is **different** from `session assess`, which requires the
   Owner-bound Architect selection and can remain BLOCKED on a new project.
   An audit-only session may be READY or BLOCKED; after its report is verified
   it receives the explicit terminal status **AUDITED** (not COMPLETED).
4. The returned `report_path` is stored under
   `~/.agf-orchestrator/artifacts/SESSION/read-only-audit.json` with an immutable
   SHA-256 bound into the session event. Re-running the same session verifies
   the hash and returns the exact original report (no fictitious new scan).
5. Inspect `report.findings` and `report.feature_evidence`; file names and
   1-based line numbers identify source evidence. Published ZIP member findings
   include `package_member`. Check original source before applying fixes.

## Trust boundary

- Only repositories with both live execution and delivery **disabled** can
  run the static audit. Origin, branch and commit must match the registered
  project and immutable session binding, and Git status must be clean both
  before and after scanning.
- No remote models, external APIs, network requests, tests, installers, ZIP
  extraction, code execution, git write operations, commits, pushes, Owner
  signing or deployment are used.
- Source reads enforce per-file, total-byte and path budgets, symlink/secret
  exclusion, exact Git index inventory and file mutation checks. ZIPs are
  inspected in memory under limits and without extraction.
- Coverage includes Python AST parsing, XML structural checks, selected static
  security/reliability patterns, Kodi Stable addons.xml references and legacy
  index checksum, bounded ZIP identity/version/path checks, and evidence of
  feature references in Stable and historical sources.
- **A matched static pattern is not proof of an exploitable vulnerability.**
  Findings in historical files or tests are explicitly downweighted.
  Feature references do not prove functioning trailers, Trakt sync, touch,
  tvOS, Android, PVR playback or performance. Runtime tests remain separate.
- A signed Architect/capability profile is required for full provider-assisted
  constitutional planning and is **not substituted** by this offline scan.

## Acceptance validation (Kodi, 2026-11-05)

Real target: `AcTweeteR/TwT-Actualizador` `main` at
`a40707fa62f2509bff8ac4364b82685e580534b8`, isolated clone
`~/Documents/AGF-Audits/Kodi-AcTweeteR`.

Previous `session assess` correctly BLOCKED because the project had no
signed eligible Architect candidate. The new explicitly chosen audit-only
disposition produced a concrete report, while preserving the separate
implementation/Owner approval boundary. A later runtime-assisted audit still
requires signed provider onboarding and physical Mac/Android/tvOS checks.
