"""Tool-free subscription reviewer transport. No Platform key or billing fallback."""
from __future__ import annotations

import hashlib
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from issue_chatgpt_pilot import (API, AUTH, CLIENT, PERMISSION, NoRedirect,
                               private_store, read_record, write_record)

MAX_BYTES = 1024 * 1024
MAX_TEXT = 64000
KNOWN_CODES = {
    'subscription_sharing_usage_limit_exceeded', 'subscription_sharing_usage_unavailable',
    'subscription_sharing_user_not_eligible', 'subscription_sharing_invalid_user',
    'subscription_sharing_user_unavailable', 'subscription_sharing_unsupported_capability',
    'subscription_sharing_route_not_supported', 'chatpass_v2_scope_not_authorized',
    'chatpass_v2_invalid_authorization_context', 'invalid_grant', 'invalid_client',
    'invalid_refresh_token', 'token_expired', 'refresh_token_expired',
    'refresh_token_invalidated', 'refresh_token_reused'}
BAD_REFRESH = {'invalid_grant', 'invalid_refresh_token', 'token_expired',
               'refresh_token_expired', 'refresh_token_invalidated', 'refresh_token_reused'}


class ReviewBlocked(ValueError):
    def __init__(self, reason, status=None, code=None):
        self.reason, self.status = reason, status
        self.code = code if code in KNOWN_CODES else None
        super().__init__('subscription review waiting: ' + reason)

    def diagnostic(self):
        return {'reason': self.reason, 'http_status': self.status, 'code': self.code}


def blocked(status=None, code=None):
    code = code if isinstance(code, str) else None
    if status == 429 or code == 'subscription_sharing_usage_limit_exceeded':
        return ReviewBlocked('usage-limit', status, code)
    if status in (401, 403) or code in BAD_REFRESH or code == 'invalid_client':
        return ReviewBlocked('authentication-or-permission', status, code)
    return ReviewBlocked('provider-unavailable-or-request-rejected', status, code)


def request(url, *, data=None, token=None, form=False):
    if url not in {AUTH + '/api/accounts/oauth/token', AUTH + '/.well-known/jwks.json',
                   API + '/models', API + '/responses'}:
        raise ValueError('review endpoint refused')
    headers = {'Accept': 'text/event-stream' if url.endswith('/responses') else 'application/json'}
    if token:
        headers['Authorization'] = 'Bearer ' + token
    if data is not None:
        headers['Content-Type'] = 'application/x-www-form-urlencoded' if form else 'application/json'
        data = (urllib.parse.urlencode(data) if form else json.dumps(data)).encode()
    try:
        return urllib.request.build_opener(NoRedirect()).open(
            urllib.request.Request(url, data=data, headers=headers), timeout=30)
    except urllib.error.HTTPError as exc:
        code = None
        try:
            payload = json.loads(exc.read(65537))
            error = payload.get('error')
            code = error.get('code') if isinstance(error, dict) else error
        except (ValueError, AttributeError, OSError):
            pass
        if not isinstance(code, str):
            code = None
        raise blocked(exc.code, code) from None
    except (urllib.error.URLError, TimeoutError):
        raise ReviewBlocked('network-unavailable') from None


def fetch(url, **kwargs):
    with request(url, **kwargs) as response:
        value = response.read(MAX_BYTES + 1)
    if len(value) > MAX_BYTES:
        raise ValueError('review response too large')
    return json.loads(value)


def credential_fingerprint(record):
    return hashlib.sha256(json.dumps(record, sort_keys=True).encode()).hexdigest()


def verify_refresh_identity(token, client, subject):
    import jwt
    header = jwt.get_unverified_header(token)
    keys = [key for key in fetch(AUTH + '/.well-known/jwks.json')['keys']
            if key.get('kid') == header.get('kid') and key.get('kty') == 'RSA'
            and key.get('use', 'sig') == 'sig']
    if header.get('alg') != 'RS256' or len(keys) != 1:
        raise ReviewBlocked('refresh-identity-invalid')
    claims = jwt.decode(token, jwt.algorithms.RSAAlgorithm.from_jwk(json.dumps(keys[0])),
                        algorithms=['RS256'], audience=client, issuer=AUTH,
                        options={'require': ['iss', 'aud', 'exp', 'sub']})
    if claims['sub'] != subject:
        raise ReviewBlocked('refresh-identity-invalid')


def session_access(root):
    """Caller holds the same directory lock as the login CLI for the whole request."""
    record = read_record(root / 'credentials.json')
    reg = read_record(root / 'registration.json')
    client = record.get('client_id')
    if (not isinstance(client, str) or not CLIENT.fullmatch(client) or client != reg.get('client_id')
            or not isinstance(record.get('subject'), str) or not record['subject']):
        raise ReviewBlocked('session-identity-invalid')
    tokens = record['tokens']
    if PERMISSION not in tokens.get('scope', '').split():
        raise ReviewBlocked('sign-in-required')
    if record['expires_at'] > time.time() + 60:
        if not isinstance(tokens.get('access_token'), str) or not tokens['access_token']:
            raise ReviewBlocked('sign-in-required')
        return tokens['access_token']
    if not tokens.get('refresh_token'):
        raise ReviewBlocked('sign-in-required')
    marker = root / 'refresh.json'
    digest = credential_fingerprint(record)
    if marker.exists():
        pending = read_record(marker)
        if pending.get('status') == 'pending' and pending.get('credential_fingerprint') == digest:
            raise ReviewBlocked('refresh-outcome-unknown-sign-in-required')
    # Reserve before rotating a one-use token. A crash/network ambiguity must not replay it.
    write_record(marker, {'status': 'pending', 'credential_fingerprint': digest, 'at': time.time()})
    try:
        fresh = fetch(AUTH + '/api/accounts/oauth/token', form=True, data={
            'grant_type': 'refresh_token', 'client_id': client,
            'refresh_token': tokens['refresh_token'], 'resource': API})
    except ReviewBlocked as exc:
        if exc.code in BAD_REFRESH:
            write_record(root / 'credentials.json', {**record, 'tokens': {}, 'expires_at': 0})
        raise
    scope = fresh.get('scope', tokens.get('scope', ''))
    if (PERMISSION not in scope.split() or fresh.get('token_type', '').lower() != 'bearer'
            or any(not isinstance(fresh.get(key), str) or not fresh[key]
                   for key in ('access_token', 'refresh_token'))
            or type(fresh.get('expires_in')) is not int or not 0 < fresh['expires_in'] <= 86400):
        raise ReviewBlocked('refresh-response-invalid-sign-in-required')
    if fresh.get('id_token'):
        try:
            verify_refresh_identity(fresh['id_token'], client, record['subject'])
        except Exception:
            raise ReviewBlocked('refresh-identity-invalid') from None
    updated = {**record, 'tokens': {**tokens, **fresh, 'scope': scope},
               'expires_at': time.time() + fresh['expires_in']}
    write_record(root / 'credentials.json', updated)
    write_record(marker, {'status': 'completed', 'at': time.time()})
    return fresh['access_token']


def read_stream(response):
    total, data, deltas = 0, [], ''
    deadline = time.monotonic() + 180
    while True:
        line = response.readline(MAX_TEXT + 1)
        total += len(line)
        if len(line) > MAX_TEXT or total > MAX_BYTES or time.monotonic() > deadline:
            raise ValueError('review stream bound exceeded')
        if not line:
            raise ValueError('review stream ended without completion')
        if line.strip() == b'':
            if not data:
                continue
            event = json.loads(b'\n'.join(data))
            data = []
            kind = event.get('type', '')
            if kind in ('error', 'response.failed', 'response.incomplete'):
                error = event.get('response', {}).get('error') or event.get('error') or {}
                code = error.get('code') if isinstance(error, dict) else None
                raise blocked(code=code)
            if 'refusal' in kind or 'function_call' in kind:
                raise ValueError('review refused or requested tools')
            if kind == 'response.output_text.delta':
                delta = event.get('delta')
                if not isinstance(delta, str):
                    raise ValueError('invalid review text delta')
                deltas += delta
                if len(deltas.encode()) > MAX_TEXT:
                    raise ValueError('review text exceeds bound')
            if kind == 'response.completed':
                final = event.get('response', {})
                if final.get('status') != 'completed' or final.get('error'):
                    raise ValueError('review did not complete successfully')
                terminal = ''
                for item in final.get('output', []):
                    if item.get('type') == 'reasoning':
                        continue
                    if item.get('type') != 'message':
                        raise ValueError('unexpected review output item')
                    for part in item.get('content', []):
                        if part.get('type') != 'output_text':
                            raise ValueError('unexpected review content or refusal')
                        terminal += part['text']
                if terminal and deltas and terminal != deltas:
                    raise ValueError('conflicting review text sources')
                result = terminal or deltas
                if not result or len(result.encode()) > MAX_TEXT:
                    raise ValueError('missing or oversized review text')
                return result
        elif line.startswith(b'data:'):
            data.append(line[5:].strip())


def review_text(body, config):
    if not config['review_included_only_confirmed'] or not config['review_session_dir']:
        raise ReviewBlocked('included-usage-confirmation-or-session-required')
    try:
        with private_store(Path(config['review_session_dir'])) as root:
            token = session_access(root)
            catalog = fetch(API + '/models', token=token)
            if body['model'] not in [item.get('slug') for item in catalog.get('models', [])
                                      if item.get('visibility') == 'list']:
                raise ReviewBlocked('model-unavailable')
            with request(API + '/responses', data=body, token=token) as response:
                return read_stream(response)
    except (OSError, TimeoutError):
        raise ReviewBlocked('session-or-network-unavailable') from None


def strict_json(text):
    def pairs(items):
        value = {}
        for key, item in items:
            if key in value:
                raise ValueError('duplicate review JSON key')
            value[key] = item
        return value

    def invalid(_value):
        raise ValueError('non-finite review JSON value')

    return json.loads(text, object_pairs_hook=pairs, parse_constant=invalid)
