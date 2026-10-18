#!/usr/bin/python3
"""Trusted Git-only credential adapter; never mount it in candidate checks."""
import os
import sys
from issue_development import github_token

if __name__ == '__main__':
    prompt = sys.argv[1] if len(sys.argv) == 2 else ''
    if 'Username' in prompt:
        print('x-access-token')
    elif 'Password' in prompt:
        try:
            print(github_token({'github_token_file': os.environ.get('ISSUE_GITHUB_TOKEN_FILE'),
                                'github_auth_user': os.environ.get('ISSUE_GITHUB_AUTH_USER')}))
        except (ValueError, OSError):
            raise SystemExit('GitHub authentication unavailable') from None
    else:
        raise SystemExit('Unexpected Git credential prompt')
