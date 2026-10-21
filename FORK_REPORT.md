# Aros source extraction report

Aros starts from the owner’s existing application code as a fresh, source-only release tree. The original private workspace was left untouched. No repository history or production data was copied.

## Included

- backend: 97 source/configuration files.
- web: 69 source/configuration files.
- ios: 29 source/configuration files.
- deploy: 13 source/configuration files.
- scripts: 10 source/configuration files.

Source includes backend API and workers, migrations, regression tests, web UI, SwiftUI client, text-based deployment templates, source scripts and original geometric SVG illustrations. Third-party package names and lockfile metadata remain intact.

## Excluded or generalized

- Git history, installed dependencies, builds, caches and bytecode.
- Environment secrets, SSH material, local databases, uploads, backups, logs and runtime state.
- Operational evidence, screenshots, personal planning prompts and private deployment documentation.
- Two externally authored PDF test fixtures with unverified redistribution permission. Parser tests now generate original synthetic PDF fixtures from source code; no external document is distributed.
- Scraped third-party 3D catalog seed content: replaced by an empty collection. Live operator-initiated discovery remains in source.
- Original iOS raster icon: excluded pending an original distributable replacement.
- Deployment host removed from defaults; production setup requires an explicit operator hostname.
- Generated OpenClaw model inventory replaced by a minimal template; messaging is disabled initially.
- Eros branding, project filenames, environment identifiers and application storage keys renamed to Aros. This is a fresh installation; no private-state migration is implied.

## Validation scope

Extraction was limited to UTF-8 source/configuration types and explicitly selected root manifests. No dependencies were installed and no runtime tests were executed during extraction. Release validation, licensing, documentation and security review are separate steps.
