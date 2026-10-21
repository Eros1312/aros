# Aros public-source sanitization report

**Verdict: PASS WITH WARNINGS**
Audit date: **2026-10-21 (Europe/Madrid)**
Scope: the generalized Aros source distribution and its four documentation screenshots, reviewed before the follow-up public commit.

No unresolved critical finding was identified in the targeted checks below. This finite review is not proof that every unknown secret or vulnerability has been ruled out. Functional validation and its limits are recorded separately in [docs/VALIDATION.md](docs/VALIDATION.md).

This report supersedes the earlier source-only and specialized-gallery snapshots. The current public workspaces are Aprendizaje, Eventos and Recursos; the institution adapters and 3D catalog/generation features have been removed.

## Scope and method

The extraction used a source allowlist: backend application/tests/migrations and manifests; web application/library/test code and original geometric SVGs; Swift source and text Xcode metadata; deployment templates and scripts; and release documentation, configuration examples and licenses. The private application's history, operational evidence, original documents and personal media were not imported.

The reviewed working-tree snapshot contains **240 release candidates: 236 UTF-8 text files and four PNG screenshots**. This report is included in the text count. Eligible paths come from `git ls-files --cached --others --exclude-standard`, deduplicated and filtered to files that still exist; deleted tracked paths are not counted. All eligible bytes were read. Scans checked paths/types, known credential patterns, credential-bearing URLs, private deployment references, personal home paths, consumer email patterns and old specialized identifiers. Candidate matches were inspected in context. Authentication, owner scoping, demo/provider-consent boundaries and deployment packaging received targeted manual review.

Excluded from the release and working-tree audit: `.git` internals, installed dependencies (`backend/.venv`, `web/node_modules`), generated web builds, Python/test/compiler caches, local databases and `.local`, environment state, credentials, backups, logs and generated archives. These are deliberate exclusions, not unreadable release candidates. `.gitignore` and `.dockerignore` cover the relevant categories and Dockerfiles use explicit copies. Reachable Git source objects were checked separately below.

## 1. Secrets — PASS

- No private-key block, recognizable provider secret token, original server credential or usable credential-bearing URL was identified in the selected source.
- Credential-like values are synthetic test fixtures or configuration placeholders. The URL containing a username/password is a negative origin-validation test.
- Environment examples contain no operational secrets. Production setup generates random secrets locally with exclusive creation and mode 0600; generated environment files remain excluded.
- No private AI, OAuth, SSH or messaging credentials are embedded in the screenshots or source distribution.

## 2. PII — PASS

- No private consumer email address, personal home path, original user document or identifying photograph was identified among the release candidates.
- Example account addresses use example/test/local domains. Phone-like authorization-test literals are synthetic fixtures.
- Demo data contains generic learning sessions, community-event planning, notes, documents, links and tasks. It does not import the owner's calendar, study documents, business materials, contacts or account state.
- The public GitHub maintainer handle `Eros1312` is intentional repository attribution.

## 3. Internal references — PASS

- Private deployment addresses are absent from the reviewed source. Production bootstrap requires the operator's hostname; example domains are explicit placeholders.
- Container paths and service names are deployment templates. Public service URLs belong to optional integrations or their documentation; synthetic test hosts exercise denial paths.
- Backend, web and iOS use `learning`, `events` and `resources`. Old identifiers remain intentionally in the compatibility transition and tests that verify data preservation and removal of retired endpoints. They do not re-enable the retired integrations.
- The event-design library no longer filters a user's designs by the owner's former event brand. Optional integrations require the new operator's configuration and account authorization.

## 4. Dangerous files and runtime payloads — PASS

- No database, dump, private upload, credential file, archive, log, generated PDF fixture, original media file or production screenshot is selected for release.
- No selected path is a symlink. Every selected text file decodes as UTF-8 without NUL bytes.
- Retired institution adapters, their external fixtures and synthetic parser generator, and the scraped 3D catalog/generator were removed from the current distribution.
- Original raster icons and private media remain excluded. The only selected binary files are the four explicitly reviewed synthetic-demo PNGs below.

## 5. Configuration completeness and boundaries — PASS WITH WARNINGS

- Configuration examples document required operator inputs. Optional AI transports are disabled until configured; the demo uses a separate local database, clears provider credentials and binds to loopback.
- Production checks reject demo mode and insufficient authentication/encryption/origin settings. Reviewed owner-scoped resource access, external-AI consent and one-use confirmation boundaries remain in place.
- The earlier shared-server authorization finding was fixed: restart/backup operations require the instance administrator as well as confirmation. Ordinary members cannot authorize shared-host administration themselves.
- Compatibility code preserves existing user files, disables retired sources and cancels pending work whose tool contracts changed. The distribution contains migration code and synthetic migration tests, not anyone's database.
- Source packaging retains lockfiles, reproducible dependency inputs and explicit Docker copies. Native iOS checks cover shared models/views, source syntax and project metadata; they do not establish an iPhone build.
- **Warnings:** this review does not establish a fresh Docker deployment/restore, live provider or account authorization, universal third-party compatibility, or a signed iOS device build. Operator configuration is intentionally incomplete until supplied by the installer. See the validation report for current test results and remaining operational limits.

## 6. Git history — PASS

The release repository was initialized fresh rather than importing private history. At this review its public baseline is `33ecbfb`, with **two reachable commits and 251 unique blob objects**. All those objects decoded as text; targeted secret patterns and dangerous runtime-file path checks found no candidates. The earlier public source contains the former specialized module names, but does not contain the private application's Git history, databases or original media. This report audits the current generalized working tree separately; it does not attest to later unreviewed commits.

## Documentation screenshot review — PASS

The user authorized actual project screenshots. These final images replace the superseded specialized gallery; `aros-documentos-estudio.png` is no longer a release candidate. All four final images were decoded and visually inspected after the last viewport recapture.

| Reviewed path | Dimensions | Bytes | SHA-256 |
| --- | --- | ---: | --- |
| `docs/images/aros-aprendizaje.png` | 1440 × 1160 | 132,407 | `b5c9dc1e5d377b1d206b4e05a7350bd4d8b3e650817d6df35500b8770dfe36a6` |
| `docs/images/aros-inicio-escritorio.png` | 1440 × 1160 | 373,251 | `7c810691d17b0bbc48b11504927d566043d3b98e3db5d1751881636c7663f96d` |
| `docs/images/aros-proyecto-tareas.png` | 1440 × 1160 | 122,836 | `e4b75435fa52bb782763469f7e0b5b1d0b4387fe26574f50d5a475bfb04a561c` |
| `docs/images/aros-proyectos-movil.png` | 390 × 844 | 69,864 | `cd2fa9ad962003fb89f93c2c60e28f662e17134346ff03712752262ec6c2fc6d` |

Provenance: actual browser captures of a separate local demo database containing the generic seed and three synthetic records created through the normal item API. The gallery has no simulated agent results, no external model calls and no connected personal accounts. Weather is unconfigured. The home panel displays real, non-identifying CPU/RAM/disk/uptime counters from the test host; the README discloses this.

Visual review found only the generic Aros account and sample workspace content. No private names, account email addresses, tokens, passwords, internal hostnames, local filesystem paths, browser-account chrome, original personal documents or identifying photographs are visible.

Every PNG byte was read; all chunk CRCs are valid and the files end at IEND without appended data. Each contains only **IHDR, IDAT and IEND** chunks, with no EXIF/XMP, author, location or comment metadata. Images were decoded for inspection, not treated as unreadable text. No image manipulation or metadata rewriting was performed by this audit.

## Unreadable Files

Unreadable count: 0

All 236 selected text files were opened and decoded. All four PNGs were fully read, structurally validated, decoded and visually inspected. The tally is zero for text and zero for images. The unreadable-files sidecar beside the repository does not exist. Dependencies, builds and runtime state remain explicitly outside the source-release scope.

## Release gate and limitations

The content gate is **PASS WITH WARNINGS**, with no unreadable-file exception required. Preserve the selected source and four-image allowlist when committing; rescan additions or modified screenshot bytes before publishing.

Targeted patterns and manual review cannot guarantee that arbitrary unrecognized secrets or vulnerabilities do not exist. This is not a penetration test or legal review. Dependency and functional checks are separate evidence, and do not prove complete security or external adoption. No claim of real users, community size or live service connectivity follows from this audit.
