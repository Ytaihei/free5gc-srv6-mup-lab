"""Manual ChatGPT-plan connection pilot; no coordinator, publication or paid fallback."""
from __future__ import annotations

import argparse
import base64
from contextlib import contextmanager
import fcntl
import hashlib
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from pathlib import Path
import re
import secrets
import stat
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

AUTH = 'https://auth.openai.com'
API = 'https://api.openai.com/v1'
PERMISSION = 'chatgpt.tokens.use.direct'
CLIENT = re.compile(r'oaiapp_[A-Za-z0-9_-]{1,200}')
MODEL = re.compile(r'[A-Za-z0-9_.-]{1,128}')
LIMIT = 1024 * 1024


class Refused(ValueError):
    """Fixed diagnostic; never include raw auth responses or callback URLs."""


class ConsentDeclined(Refused):
    """A state-validated authorization error ends the pending login."""


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise Refused('redirect refused')


def open_request(url, *, data=None, token=None, form=False):
    allowed = {AUTH + '/.well-known/openid-configuration', AUTH + '/.well-known/jwks.json',
               AUTH + '/api/accounts/oauth/token', API + '/models', API + '/responses'}
    if url not in allowed:
        raise Refused('endpoint refused')
    headers = {'Accept': 'application/json'}
    if token:
        headers['Authorization'] = 'Bearer ' + token
    if data is not None:
        headers['Content-Type'] = ('application/x-www-form-urlencoded' if form else 'application/json')
        data = (urllib.parse.urlencode(data) if form else json.dumps(data)).encode()
    try:
        return urllib.request.build_opener(NoRedirect()).open(
            urllib.request.Request(url, data=data, headers=headers), timeout=30)
    except urllib.error.HTTPError as exc:
        raise Refused('HTTP ' + str(exc.code) + '; no retry or billing fallback') from None
    except urllib.error.URLError:
        raise Refused('connection failed; no retry or billing fallback') from None


def fetch(url, **kwargs):
    with open_request(url, **kwargs) as response:
        payload = response.read(LIMIT + 1)
    if len(payload) > LIMIT:
        raise Refused('response too large')
    return json.loads(payload)


@contextmanager
def private_store(path):
    path = Path(path).absolute()
    if (path != path.resolve() or path in (Path('/'), Path.home())
            or path.is_relative_to(Path(__file__).resolve().parents[1])):
        raise Refused('state must be a dedicated non-symlink directory outside the checkout')
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise Refused('state directory must be owned by operator and mode 0700')
    fd = os.open(path / 'lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as lock:
        check_file(os.fstat(lock.fileno()))
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield path


def check_file(info):
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
            or info.st_mode & 0o077 or info.st_nlink != 1 or info.st_size > LIMIT):
        raise Refused('private regular operator-owned file required')


def read_record(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd) as stream:
        check_file(os.fstat(stream.fileno()))
        return json.load(stream)


def write_record(path, value):
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as stream:
        json.dump(value, stream)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def registration(root):
    path = root / 'registration.json'
    if not path.exists():
        write_record(path, {'host_id': 'urn:uuid:' + str(uuid.uuid4()), 'client_id': None})
    value = read_record(path)
    if (not isinstance(value.get('host_id'), str) or
            not re.fullmatch(r'urn:uuid:[0-9a-f-]{36}', value['host_id']) or
            (value.get('client_id') is not None and not CLIENT.fullmatch(value['client_id']))):
        raise Refused('invalid registration')
    return value


def transaction(reg, port):
    verifier = secrets.token_urlsafe(48)
    query = {'client_id': reg['client_id'] or 'dynamic_agent_client',
             'ext_agent_host_id': reg['host_id'], 'response_type': 'code',
             'redirect_uri': f'http://127.0.0.1:{port}/auth/callback',
             'scope': 'openid profile email offline_access resource.invoke ' + PERMISSION,
             'resource': API, 'state': secrets.token_urlsafe(32), 'nonce': secrets.token_urlsafe(32),
             'code_challenge_method': 'S256',
             'code_challenge': base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')}
    if not reg['client_id']:
        query['agent_name_hint'] = 'free5gc-srv6-mup-lab Issue review pilot'
    return query, verifier


def callback(target, query):
    parts = urllib.parse.urlsplit(target)
    if parts.path != '/auth/callback' or parts.fragment or len(target) > 16384:
        raise Refused('invalid callback')
    values = urllib.parse.parse_qs(parts.query, keep_blank_values=True)
    if any(len(value) != 1 for value in values.values()):
        raise Refused('duplicate callback parameter')
    values = {key: value[0] for key, value in values.items()}
    if not secrets.compare_digest(values.get('state', ''), query['state']):
        raise Refused('callback state mismatch')
    if 'error' in values:
        raise ConsentDeclined('sign-in declined; no credentials changed')
    if not values.get('code'):
        raise Refused('sign-in incomplete')
    client = values.get('client_id', query['client_id'])
    if (not CLIENT.fullmatch(client) or
            (query['client_id'] != 'dynamic_agent_client' and client != query['client_id'])):
        raise Refused('issued client mismatch or missing')
    return client, values['code']


def validate_tokens(tokens, client, nonce, jwks):
    import jwt
    token = tokens.get('id_token', '')
    header = jwt.get_unverified_header(token)
    if header.get('alg') != 'RS256':
        raise Refused('ID-token algorithm refused')
    keys = [key for key in jwks['keys'] if key.get('kid') == header.get('kid')
            and key.get('kty') == 'RSA' and key.get('use', 'sig') == 'sig']
    if len(keys) != 1:
        raise Refused('ID-token key unavailable')
    claims = jwt.decode(token, jwt.algorithms.RSAAlgorithm.from_jwk(json.dumps(keys[0])),
                        algorithms=['RS256'], audience=client, issuer=AUTH,
                        options={'require': ['iss', 'aud', 'exp', 'sub', 'nonce']})
    if (not isinstance(claims['sub'], str) or not claims['sub'] or
            not secrets.compare_digest(claims['nonce'], nonce)):
        raise Refused('ID-token identity or nonce mismatch')
    if (PERMISSION not in tokens.get('scope', '').split() or
            tokens.get('token_type', '').lower() != 'bearer' or
            not isinstance(tokens.get('access_token'), str) or not tokens['access_token'] or
            type(tokens.get('expires_in')) is not int or tokens['expires_in'] <= 0):
        raise Refused('ChatGPT plan permission or access token missing')
    return claims['sub']


def login(root, port):
    try:
        import jwt  # noqa: F401 -- dependency check before opening the listener
        import cryptography  # noqa: F401
    except ImportError:
        raise Refused('login requires PyJWT with cryptography; no packages installed automatically') from None
    reg = registration(root)
    query, verifier = transaction(reg, port)
    received = []

    class Handler(BaseHTTPRequestHandler):
        def setup(self):
            super().setup()
            self.connection.settimeout(5)

        def log_message(self, *_args):
            pass  # Never log callback codes or query strings.

        def do_GET(self):
            try:
                result = callback(self.path, query)
            except ConsentDeclined as exc:
                received.append(exc)
                self.send_error(400, 'Sign-in declined')
                return
            except (ValueError, TypeError):
                self.send_error(400, 'Invalid sign-in callback')
                return
            received.append(result)
            self.send_response(200)
            self.send_header('Content-Type', 'text/plain')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.end_headers()
            self.wfile.write(b'Callback received. Check the terminal for validation; no inference has run.')

    with HTTPServer(('127.0.0.1', port), Handler) as server:
        server.timeout = 1
        print('Continue with ChatGPT (login only; no inference):', flush=True)
        print(AUTH + '/api/accounts/authorize?' + urllib.parse.urlencode(query), flush=True)
        print('If remote, forward this loopback port over SSH before opening the URL.', flush=True)
        deadline = time.monotonic() + 600
        while not received and time.monotonic() < deadline:
            server.handle_request()
    if not received:
        raise Refused('login timed out; rerun login')
    if isinstance(received[0], ConsentDeclined):
        raise received[0]
    client, code = received[0]
    # Retain issued registration even if code exchange fails; do not create duplicate clients.
    reg['client_id'] = client
    write_record(root / 'registration.json', reg)
    tokens = fetch(AUTH + '/api/accounts/oauth/token', form=True, data={
        'grant_type': 'authorization_code', 'client_id': client, 'code': code,
        'code_verifier': verifier, 'redirect_uri': query['redirect_uri'], 'resource': API})
    subject = validate_tokens(tokens, client, query['nonce'], fetch(AUTH + '/.well-known/jwks.json'))
    previous = root / 'credentials.json'
    if previous.exists() and read_record(previous)['subject'] != subject:
        raise Refused('account changed; existing credentials preserved')
    write_record(previous, {'client_id': client, 'subject': subject, 'tokens': tokens,
                            'expires_at': time.time() + tokens['expires_in']})
    print('Sign-in verified. No inference run. Check ChatGPT Settings > Usage before smoke testing.')


def access(root):
    record = read_record(root / 'credentials.json')
    if record['expires_at'] <= time.time() + 60:
        raise Refused('access expired; rerun login (pilot does not refresh automatically)')
    if PERMISSION not in record['tokens'].get('scope', '').split():
        raise Refused('ChatGPT plan permission missing')
    return record['tokens']['access_token']


def smoke_text_matches(terminal_text, streamed_text):
    """A completed stream may omit the terminal text; conflicting sources fail."""
    if terminal_text and streamed_text and terminal_text != streamed_text:
        return False
    return (terminal_text or streamed_text).strip() == 'MUP_PILOT_OK'


def completed_stream(response, evidence=None):
    size, data, completed = 0, [], None
    streamed_text = ''
    deadline = time.monotonic() + 120
    while True:
        line = response.readline(65537)
        size += len(line)
        if len(line) > 65536 or size > LIMIT or time.monotonic() > deadline:
            raise Refused('stream bound exceeded')
        if not line:
            break
        if line.strip() == b'':
            if not data:
                continue
            event = json.loads(b'\n'.join(data))
            data = []
            kind = event.get('type')
            if kind == 'response.output_text.delta':
                streamed_text += event.get('delta', '')
            if kind in ('error', 'response.failed', 'response.incomplete'):
                raise Refused('inference failed or limited; no retry or billing fallback')
            if kind == 'response.completed':
                completed = event.get('response', {})
                break
        elif line.startswith(b'data:'):
            data.append(line[5:].strip())
    if not completed or completed.get('status') != 'completed' or completed.get('error'):
        raise Refused('no successful response.completed event')
    text = ''.join(part.get('text', '') for item in completed.get('output', [])
                   if item.get('type') == 'message' for part in item.get('content', [])
                   if part.get('type') == 'output_text')
    if evidence is not None:
        # Retain only the fixed smoke prompt's reply, never tokens/headers/full events.
        evidence({'status': 'completed', 'expected_text': smoke_text_matches(text, streamed_text),
                  'output_text': text, 'streamed_text': streamed_text, 'at': time.time()})
    if not smoke_text_matches(text, streamed_text):
        raise Refused('unexpected smoke response')
    return {'status': 'completed', 'expected_text': True}


def smoke(root, model, included_only, retry=False):
    if not included_only:
        raise Refused('first confirm app credit usage is disabled and no automatic purchase is enabled')
    if not model or not MODEL.fullmatch(model):
        raise Refused('select an exact model slug from models')
    name = 'smoke-retry' if retry else 'smoke'
    receipt = root / (name + '.json')
    if receipt.exists():
        raise Refused('one-shot pilot already attempted; inspect receipt, no automatic retry')
    if retry:
        previous = read_record(root / 'smoke.json')
        if previous.get('status') != 'attempted' or previous.get('model') != model:
            raise Refused('retry requires an inspected unsuccessful attempt with the same model')
    token = access(root)
    catalog = fetch(API + '/models', token=token)
    if model not in [entry.get('slug') for entry in catalog.get('models', [])
                     if entry.get('visibility') == 'list']:
        raise Refused('model is not in account catalog')
    write_record(receipt, {'status': 'attempted', 'model': model, 'at': time.time()})
    with open_request(API + '/responses', token=token, data={
            'model': model, 'store': False, 'stream': True,
            'input': [{'role': 'user', 'content': 'Reply with exactly MUP_PILOT_OK'}]}) as response:
        result = completed_stream(response, evidence=lambda value: write_record(
            root / (name + '-response.json'), value))
    write_record(receipt, {**result, 'model': model, 'at': time.time()})
    print('Smoke completed. Not a code review, PR test, or unattended-flow acceptance.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state', type=Path, required=True)
    parser.add_argument('command', choices=['status', 'login', 'models', 'smoke', 'retry-smoke'])
    parser.add_argument('--port', type=int, default=1465)
    parser.add_argument('--model')
    parser.add_argument('--confirm-included-only', action='store_true',
                        help='operator verified no app credit consumption or automatic purchase in ChatGPT settings')
    args = parser.parse_args()
    try:
        if os.geteuid() == 0:
            raise Refused('run as the unprivileged operator, not root')
        if not 1024 <= args.port <= 65535:
            raise Refused('invalid loopback port')
        with private_store(args.state) as root:
            if args.command == 'login':
                login(root, args.port)
            elif args.command == 'models':
                value = fetch(API + '/models', token=access(root))
                print(json.dumps([{'slug': item['slug'], 'display_name': item.get('display_name')}
                                  for item in value.get('models', []) if item.get('visibility') == 'list']))
            elif args.command in ('smoke', 'retry-smoke'):
                smoke(root, args.model, args.confirm_included_only, retry=args.command == 'retry-smoke')
            else:
                print(json.dumps({'credentials_present': (root / 'credentials.json').is_file(),
                                  'smoke_attempted': (root / 'smoke.json').is_file(),
                                  'retry_attempted': (root / 'smoke-retry.json').is_file(),
                                  'coordinator_connected': False, 'paid_fallback': False}))
    except Exception as exc:
        # JWT/HTTP errors can embed secrets; only our fixed diagnostics may be printed.
        print('Pilot stopped: ' + (str(exc) if isinstance(exc, Refused) else type(exc).__name__))
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
