# Aros source extraction report

Aros starts from the owner’s existing application code as a fresh, source-only release tree. The original private workspace was left untouched. No repository history or production data was copied.

## Included

- Backend API, workers, migrations and regression tests.
- Web client, source assets and UI tests.
- Experimental iOS client source and project configuration.
- Deployment templates and operator setup scripts.

Source includes backend API and workers, migrations, regression tests, web UI, SwiftUI client, text-based deployment templates, source scripts and original geometric SVG illustrations. Third-party package names and lockfile metadata remain intact.

## Excluded or generalized

- Git history, installed dependencies, builds, caches and bytecode.
- Environment secrets, SSH material, local databases, uploads, backups, logs and runtime state.
- Operational evidence, screenshots, personal planning prompts and private deployment documentation.
- Two externally authored PDF test fixtures with unverified redistribution permission and their specialized parser tests were removed; no external document is distributed.
- Specialized catalog and institution adapters were removed. Learning, events and resources use general-purpose records and synthetic examples.
- Original iOS raster icon: excluded pending an original distributable replacement.
- Deployment host removed from defaults; production setup requires an explicit operator hostname.
- Generated OpenClaw model inventory replaced by a minimal template; messaging is disabled initially.
- Application branding, project filenames, environment identifiers and storage keys use Aros. This is a fresh installation; no private-state migration is implied.

## Validation scope

The initial extraction was limited to UTF-8 source/configuration types and explicitly selected root manifests. The public gallery adds original PNG screenshots of a synthetic local demo. Release validation, licensing, documentation and content review are documented separately in `docs/VALIDATION.md` and `SANITIZATION_REPORT.md`.
