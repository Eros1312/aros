# Aros public-source sanitization report

**Verdict: PASS WITH WARNINGS**
Audit date: **2026-10-21 (Europe/Madrid)**
Scope: the source distribution prepared for the first public Aros release.

No unresolved critical finding was identified in the targeted source sanitization checks below. This is a finite source review, not proof that every unknown secret or vulnerability has been ruled out. Operational readiness and test coverage are reported separately in [docs/VALIDATION.md](docs/VALIDATION.md).

## Scope and method

The initial extraction used a source allowlist: backend application/tests/migrations and manifests; web application/library/test code, package manifests and geometric SVG resources; Swift source and text Xcode metadata; deployment templates and scripts; and release documentation, configuration examples and license files. The private application's history, operational evidence, original documents and personal media were not imported.

The final pre-publication scan read **252 eligible files**, including this report, as UTF-8. The selected tree contains approximately 2.02 MB of text source, configuration and documentation. Eligibility was obtained from `git ls-files --cached --others --exclude-standard`, so the same source/configuration/documentation candidates can be staged for release. The scan checked readable content, paths, file types, known credential patterns, credential-bearing URLs, private deployment references, personal email patterns, phone-like literals and old branding. Candidate matches were inspected in context. Relevant authentication, demo, provider-consent and deployment boundaries were also reviewed manually.

Excluded from the release and this source audit: `.git` internals, installed dependencies (`backend/.venv`, `web/node_modules`), generated web builds (`web/.next`, `web/out`), Python/test/compiler caches, local runtime databases and `.local`, credentials and environment state, backups, logs, generated archives and artifacts. These excluded environments are not claimed to have been scanned as public source. `.gitignore` and `.dockerignore` protect the relevant generated and runtime categories; Dockerfile copies are explicit.

## 1. Secrets — PASS

- No private-key block, provider-style secret token, original server credential or real credential-bearing URL was identified in eligible source.
- Credentials are supplied through environment variables or generated locally. The production generator uses random secrets, exclusive creation and file mode 0600.
- Credential-like values in tests are synthetic fixtures. The two URL credential matches are negative URL-validation tests, not usable account credentials.
- Optional FreeLLM bootstrap was corrected to replace missing **or empty** example values atomically, and a regression verifies idempotence. No generated `.env` is part of the release candidates.

## 2. PII — PASS

- No private consumer email address, personal home path, original user documents or production screenshot was identified in release candidates.
- Example emails use example/test/local domains. Phone-like literals occur in synthetic WhatsApp authorization tests and use repeated-digit fixtures.
- Owner-specific event and personal reminder examples were generalized. The public GitHub maintainer identity `Eros1312` is intentional attribution, not redacted data.

## 3. Internal references — PASS

- The original deployment IP and hostname are absent. Production bootstrap requires the operator's own hostname; examples use `aros.example.com`.
- Container-local paths such as `/home/node` and service names such as `database` are deployment templates, not private infrastructure references.
- Public third-party service URLs remain where needed for integrations. Existing PRISMA/UV area names describe retained specialized modules; they do not grant access to private material.
- Application branding and namespaces use Aros. The historical brand appears only in the extraction report's provenance explanation.

## 4. Dangerous files and runtime payloads — PASS

- No database, dump, credential file, archive, log, uploaded document, image/video capture or binary fixture was present among eligible release files.
- No selected file was a symlink or contained NUL bytes.
- The external university PDFs were replaced by an original source generator that creates synthetic grid PDFs in memory during tests. Those generated PDFs are not release files.
- The scraped 3D catalog is an empty seed. The release does not claim fabricated publisher observations or distribute uncertain third-party images.
- Original media and the private iOS icon were omitted. Text SVG geometry and empty experimental icon metadata remain in source.

## 5. Configuration completeness and boundaries — PASS WITH WARNINGS

- Root and deployment `.env.example` files document inputs without secrets. Optional AI transports require explicit configuration; the local demo blanks provider credentials and uses its own database on loopback.
- Production startup rejects demo mode, an insufficient password, missing/invalid encryption and a non-HTTPS origin. Primary authentication, owner-scoped resource access, CSRF handling and external-AI consent boundaries were reviewed.
- A shared-server authorization gap found during review was fixed: restart/backup mutations now require the instance administrator in addition to a one-use confirmation. A member's own confirmation cannot authorize administration of the shared host.
- CI now invokes the Swift check with its required zsh interpreter. pnpm versions agree across setup, package metadata, Docker and CI; backend setup and Docker consume the lockfile.
- Independent focused regression run: **10 tests passed**, covering shared-server administration, authentication/CSRF, resource-owner isolation, production demo denial, external-AI consent and public-release configuration. Earlier synthetic calendar/catalog checks passed **32 tests**. Dependency deprecation warnings remain.
- **Warnings:** this source review does not establish a fresh Docker deployment/restore, live third-party account authorization, provider compatibility, or a signed iOS device build. Configuration examples intentionally require operator input. See the release validation document for the completed checks and their limits.

## 6. Git history — PASS

The destination repository was initialized fresh. At this scan it had **0 reachable commits**, no remote, and no imported private history. The first public commit must contain only the reviewed source candidates; installed/runtime files remain ignored. This finding does not attest to commits or manual additions made after the audit.

## Unreadable Files

Unreadable count: 0

Every selected source/configuration/documentation file was opened and decoded successfully. There is no unreadable-files sidecar next to the repository. The tally by type is zero for all selected types. Installed dependencies, builds, runtime state and Git object storage are deliberate scope exclusions, not silently unreadable release files.

## Release gate and limitations

The source sanitization gate is **PASS WITH WARNINGS**, with no unreadable-file exception required. Preserve the source-only staging boundary when committing or archiving. Re-run checks if additional files, operational evidence, credentials or imported history are added.

This sanitization scan uses targeted patterns and manual review. It is not an exhaustive penetration test, legal review, or guarantee that arbitrary unrecognized secrets cannot exist. Separate dependency audits and the final 599-test backend validation are documented in the release validation report; their results are not treated as proof of complete security. No claim of external adoption, community size or service availability follows from these checks.
