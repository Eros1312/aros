# Aros

Read `AGENTS.md` for contributor and security constraints.

- `./setup.sh`: install locked Python dependencies and compile the web client.
- `./scripts/run-local.sh`: isolated local demo, loopback only, no AI credentials required.
- `./scripts/verify.sh`: backend regression tests, web TypeScript/unit/DOM checks and build.
- `backend/app/`: FastAPI, scoped access, persistent jobs and optional connectors.
- `backend/migrations/`: Alembic schema history.
- `web/`: Next.js static client.
- `ios/`: experimental SwiftUI client; see its README for validation limits.

Never operate a production deployment while testing. Use synthetic data and temporary databases. A mocked connector is not proof of live connectivity. Never commit credentials, runtime data, logs or private media.
