#!/usr/bin/env python3
"""Generate private FreeLLMAPI bootstrap values without printing them."""
from pathlib import Path
import secrets,os,tempfile
path=Path(__file__).resolve().parents[1]/'deploy/.env'
text=path.read_text();values=dict(line.split('=',1) for line in text.splitlines() if '=' in line and not line.startswith('#'))
add={name:value for name,value in {'FREELLM_ENCRYPTION_KEY':secrets.token_hex(32),'FREELLM_ADMIN_PASSWORD':secrets.token_urlsafe(32),'FREELLM_URL':'http://freellmapi:3001'}.items() if not values.get(name,'').strip()}
lines=[line for line in text.splitlines() if line.split('=',1)[0] not in add]
content='\n'.join(lines+[f'{name}={value}' for name,value in add.items()])+'\n'
fd,temporary=tempfile.mkstemp(prefix='.env.freellm-',dir=path.parent)
try:
    with os.fdopen(fd,'w') as file:
        file.write(content);file.flush();os.fsync(file.fileno())
    os.replace(temporary,path)
finally:
    if os.path.exists(temporary):os.unlink(temporary)
print('Private FreeLLMAPI environment initialized.')
