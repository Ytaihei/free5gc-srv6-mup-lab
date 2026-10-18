#!/usr/bin/python3
"""Trusted Git-only credential adapter; never mount it in candidate checks."""
import os
import sys
from issue_development import secret

if __name__ == '__main__':
    prompt = sys.argv[1] if len(sys.argv) == 2 else ''
    if 'Username' in prompt:
        print('x-access-token')
    elif 'Password' in prompt:
        print(secret(os.environ['ISSUE_GITHUB_TOKEN_FILE']))
    else:
        raise SystemExit('Unexpected Git credential prompt')
