#!/usr/bin/env python3
"""Read mounted runtime secrets, then replace this process with the service."""
import os
from pathlib import Path
import sys
from urllib.parse import quote


def environment():
    env = dict(os.environ)
    password_file = env.get('FOREST_DATABASE_PASSWORD_FILE')
    if password_file:
        password = Path(password_file).read_text().strip()
        if len(password) < 24:
            raise ValueError('Deployment database password must contain at least 24 characters')
        host = env.get('FOREST_DATABASE_HOST', 'postgres')
        env['FOREST_DATABASE_URL'] = 'postgresql+psycopg://forest:' + quote(password, safe='') + '@' + host + ':5432/forest'
    token_file = env.get('FOREST_OWNER_TOKEN_FILE')
    if token_file:
        env['FOREST_OWNER_TOKEN'] = Path(token_file).read_text().strip()
        if len(env['FOREST_OWNER_TOKEN']) < 24:
            raise ValueError('Deployment owner token must contain at least 24 characters')
    return env


if __name__ == '__main__':
    if len(sys.argv) < 2:
        raise SystemExit('A service command is required')
    os.execvpe(sys.argv[1], sys.argv[1:], environment())
