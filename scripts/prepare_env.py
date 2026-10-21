#!/usr/bin/env python3
"""Create a production configuration without sending or printing credentials."""
import argparse, base64, os, secrets
from pathlib import Path
p=argparse.ArgumentParser()
p.add_argument('--domain',required=True,help='Public hostname for your own deployment')
p.add_argument('--email',default='aros@local')
a=p.parse_args()
import re
if not re.fullmatch(r'(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?',a.domain): p.error('Invalid hostname')
if any(c in a.email for c in '\n\r\"\''): p.error('Invalid email')
path=Path(__file__).resolve().parent.parent/'deploy/.env'
password=secrets.token_urlsafe(30); database=secrets.token_hex(24)
content=f'''AROS_DOMAIN={a.domain}
APP_ENV=production
DEMO_MODE=false
PUBLIC_ORIGIN=https://{a.domain}
ADMIN_EMAIL={a.email}
ADMIN_PASSWORD={password}
POSTGRES_PASSWORD={database}
DATABASE_URL=postgresql+psycopg://aros:{database}@database:5432/aros
ENCRYPTION_KEY={base64.urlsafe_b64encode(os.urandom(32)).decode()}
WEB_DIR=/web/out
SESSION_DAYS=7
GOOGLE_CLIENT_ID=
GOOGLE_CLIENT_SECRET=
OPENAI_API_KEY=
OPENAI_MODEL=
OPENCLAW_URL=
OPENCLAW_TOKEN=
OPENCLAW_MODEL=
OPENCLAW_DESIGN_MODEL=
RSS_URLS=
SERVER_AGENT_URL=
SERVER_AGENT_TOKEN=
'''
fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
with os.fdopen(fd,'w') as f: f.write(content)
print(f'Configuration created at {path}. Read ADMIN_PASSWORD privately to sign in. Existing files are never overwritten.')
