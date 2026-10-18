"""Credential-free tests of the manual subscription pilot; no network calls."""
import base64
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
from urllib.parse import urlencode

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import issue_chatgpt_pilot as pilot


class PilotTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.reg = {'host_id': 'urn:uuid:00000000-0000-0000-0000-000000000000', 'client_id': None}
        self.query, self.verifier = pilot.transaction(self.reg, 1465)

    def target(self, **overrides):
        return '/auth/callback?' + urlencode({'state': self.query['state'], 'code': 'test-code',
                                             'client_id': 'oaiapp_test', **overrides})

    def credentials(self):
        pilot.write_record(self.root / 'credentials.json', {'expires_at': time.time() + 3600,
                           'tokens': {'access_token': 'fixture-access', 'scope': pilot.PERMISSION}})

    def stream(self, kind='response.completed', status='completed', text='MUP_PILOT_OK'):
        event = {'type': kind, 'response': {'status': status, 'output': [
            {'type': 'message', 'content': [{'type': 'output_text', 'text': text}]}]}}
        return io.BytesIO(b'data: ' + json.dumps(event).encode() + b'\n\n')

    def test_pkce_and_registration(self):
        expected = base64.urlsafe_b64encode(hashlib.sha256(self.verifier.encode()).digest()).decode().rstrip('=')
        self.assertEqual(self.query['code_challenge'], expected)
        self.assertEqual(self.query['resource'], pilot.API)
        self.assertIn(pilot.PERMISSION, self.query['scope'])
        self.assertIn('agent_name_hint', self.query)
        reg = {**self.reg, 'client_id': 'oaiapp_test'}
        returning, _ = pilot.transaction(reg, 1465)
        self.assertNotIn('agent_name_hint', returning)
        self.assertNotIn('id_token_hint', returning)

    def test_callback_valid(self):
        self.assertEqual(pilot.callback(self.target(), self.query), ('oaiapp_test', 'test-code'))

    def test_callback_rejects_invalid_state_client_error_duplicates_path(self):
        targets = [self.target(state='bad'), self.target(client_id='dynamic_agent_client'),
                   self.target(error='access_denied'), self.target(code=''),
                   self.target() + '&state=duplicate', self.target().replace('/auth/callback', '/callback')]
        for target in targets:
            with self.subTest(target=target), self.assertRaises(pilot.Refused):
                pilot.callback(target, self.query)

    def test_returning_account_cannot_replace_client(self):
        self.query['client_id'] = 'oaiapp_original'
        with self.assertRaises(pilot.Refused):
            pilot.callback(self.target(), self.query)

    def test_private_files_and_stable_host(self):
        with pilot.private_store(self.root):
            first = pilot.registration(self.root)
            self.assertEqual(first, pilot.registration(self.root))
            self.assertEqual((self.root / 'registration.json').stat().st_mode & 0o777, 0o600)

    def test_public_directory_rejected(self):
        self.root.chmod(0o755)
        with self.assertRaises(pilot.Refused), pilot.private_store(self.root):
            pass

    def test_symlink_and_hardlink_credentials_rejected(self):
        path = self.root / 'secret'
        pilot.write_record(path, {'fixture': True})
        link = self.root / 'link'
        link.symlink_to(path)
        with self.assertRaises(OSError):
            pilot.read_record(link)
        link.unlink()
        os.link(path, link)
        with self.assertRaises(pilot.Refused):
            pilot.read_record(path)

    def test_public_credentials_rejected(self):
        self.credentials()
        (self.root / 'credentials.json').chmod(0o644)
        with self.assertRaises(pilot.Refused):
            pilot.access(self.root)

    def test_expired_token_no_refresh_fallback(self):
        pilot.write_record(self.root / 'credentials.json', {'expires_at': 0})
        with patch.object(pilot, 'fetch') as network, self.assertRaises(pilot.Refused):
            pilot.access(self.root)
        network.assert_not_called()

    def test_endpoint_and_redirect_rejected(self):
        with self.assertRaises(pilot.Refused):
            pilot.open_request('https://example.invalid', token='fixture')
        with self.assertRaises(pilot.Refused):
            pilot.NoRedirect().redirect_request(None, None, 302, None, None, 'https://example.invalid')

    def test_completed_stream(self):
        self.assertTrue(pilot.completed_stream(self.stream())['expected_text'])

    def test_mismatch_retains_private_evidence_without_weakening_acceptance(self):
        self.credentials()
        with patch.object(pilot, 'fetch', return_value={
                'models': [{'slug': 'test-model', 'visibility': 'list'}]}), patch.object(
                pilot, 'open_request', return_value=self.stream(text='unexpected fixture reply')) as network:
            with self.assertRaisesRegex(pilot.Refused, 'unexpected smoke response'):
                pilot.smoke(self.root, 'test-model', True)
            with self.assertRaisesRegex(pilot.Refused, 'already attempted'):
                pilot.smoke(self.root, 'test-model', True)
        self.assertEqual(network.call_count, 1)
        path = self.root / 'smoke-response.json'
        saved = pilot.read_record(path)
        self.assertEqual(set(saved), {'status', 'expected_text', 'output_text', 'streamed_text', 'at'})
        self.assertFalse(saved['expected_text'])
        self.assertEqual(saved['output_text'], 'unexpected fixture reply')
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(pilot.read_record(self.root / 'smoke.json')['status'], 'attempted')

    def test_failure_incomplete_wrong_text_truncated_and_oversized(self):
        streams = [self.stream(kind='response.failed'), self.stream(kind='error'),
                   self.stream(kind='response.incomplete'), self.stream(status='failed'),
                   self.stream(text='not-the-challenge'), io.BytesIO(b''),
                   io.BytesIO(b'data: ' + b'x' * 70000)]
        for stream in streams:
            with self.subTest(stream=stream), self.assertRaises(pilot.Refused):
                pilot.completed_stream(stream)

    def test_smoke_requires_credit_setting_confirmation_before_network(self):
        with patch.object(pilot, 'fetch') as network, self.assertRaises(pilot.Refused):
            pilot.smoke(self.root, 'test-model', False)
        network.assert_not_called()

    def test_smoke_reserves_once_and_uses_subscription_body(self):
        self.credentials()
        catalog = {'models': [{'slug': 'test-model', 'visibility': 'list'}]}
        with patch.object(pilot, 'fetch', return_value=catalog), patch.object(
                pilot, 'open_request', return_value=self.stream()) as network:
            pilot.smoke(self.root, 'test-model', True)
            with self.assertRaises(pilot.Refused):
                pilot.smoke(self.root, 'test-model', True)
        self.assertEqual(network.call_count, 1)
        args = network.call_args.kwargs
        self.assertEqual(args['token'], 'fixture-access')
        self.assertEqual(set(args['data']), {'model', 'store', 'stream', 'input'})
        self.assertIs(args['data']['store'], False)
        self.assertIs(args['data']['stream'], True)
        self.assertIsInstance(args['data']['input'], list)
        self.assertEqual(pilot.read_record(self.root / 'smoke.json')['status'], 'completed')

    def test_failure_retains_attempt_and_no_retry(self):
        self.credentials()
        with patch.object(pilot, 'fetch', return_value={
                'models': [{'slug': 'test-model', 'visibility': 'list'}]}), patch.object(
                pilot, 'open_request', side_effect=pilot.Refused('fixture failure')) as network:
            for _ in range(2):
                with self.assertRaises(pilot.Refused):
                    pilot.smoke(self.root, 'test-model', True)
        self.assertEqual(network.call_count, 1)
        self.assertEqual(pilot.read_record(self.root / 'smoke.json')['status'], 'attempted')

    def test_unavailable_model_no_inference(self):
        self.credentials()
        with patch.object(pilot, 'fetch', return_value={'models': []}), patch.object(
                pilot, 'open_request') as network, self.assertRaises(pilot.Refused):
            pilot.smoke(self.root, 'test-model', True)
        network.assert_not_called()

    def test_explicit_retry_preserves_previous_attempt_and_is_bounded(self):
        self.credentials()
        original = {'status': 'attempted', 'model': 'test-model', 'at': 123}
        pilot.write_record(self.root / 'smoke.json', original)
        pilot.write_record(self.root / 'smoke-response.json', {'fixture': 'old-reply'})
        with patch.object(pilot, 'fetch', return_value={
                'models': [{'slug': 'test-model', 'visibility': 'list'}]}), patch.object(
                pilot, 'open_request', return_value=self.stream()) as network:
            pilot.smoke(self.root, 'test-model', True, retry=True)
            with self.assertRaisesRegex(pilot.Refused, 'already attempted'):
                pilot.smoke(self.root, 'test-model', True, retry=True)
        self.assertEqual(network.call_count, 1)
        self.assertEqual(pilot.read_record(self.root / 'smoke.json'), original)
        self.assertEqual(pilot.read_record(self.root / 'smoke-response.json'), {'fixture': 'old-reply'})
        self.assertEqual(pilot.read_record(self.root / 'smoke-retry.json')['status'], 'completed')
        self.assertTrue(pilot.read_record(self.root / 'smoke-retry-response.json')['expected_text'])

    def test_retry_cannot_change_model_or_repeat_success(self):
        for original in ({'status': 'completed', 'model': 'test-model'},
                         {'status': 'attempted', 'model': 'other-model'}):
            pilot.write_record(self.root / 'smoke.json', original)
            with patch.object(pilot, 'fetch') as network, self.assertRaises(pilot.Refused):
                pilot.smoke(self.root, 'test-model', True, retry=True)
            network.assert_not_called()

    def test_stream_deltas_retained_and_require_successful_terminal_event(self):
        delta = b'data: {"type":"response.output_text.delta","delta":"MUP_PILOT_OK"}\n\n'
        evidence = []
        result = pilot.completed_stream(io.BytesIO(delta + self.stream(text='').getvalue()), evidence.append)
        self.assertTrue(result['expected_text'])
        self.assertEqual(evidence[0]['streamed_text'], 'MUP_PILOT_OK')
        self.assertTrue(evidence[0]['expected_text'])
        for suffix in (b'', self.stream(kind='response.failed').getvalue(),
                       self.stream(status='failed').getvalue(), self.stream(text='conflict').getvalue()):
            with self.subTest(suffix=suffix), self.assertRaises(pilot.Refused):
                pilot.completed_stream(io.BytesIO(delta + suffix))

    def test_text_sources_must_agree_and_challenge_must_match(self):
        self.assertTrue(pilot.smoke_text_matches('', 'MUP_PILOT_OK'))
        self.assertTrue(pilot.smoke_text_matches('MUP_PILOT_OK', ''))
        self.assertTrue(pilot.smoke_text_matches('MUP_PILOT_OK', 'MUP_PILOT_OK'))
        self.assertFalse(pilot.smoke_text_matches('wrong', 'MUP_PILOT_OK'))
        self.assertFalse(pilot.smoke_text_matches('', 'wrong'))
        self.assertFalse(pilot.smoke_text_matches('', ''))


@unittest.skipUnless(importlib.util.find_spec('jwt') and importlib.util.find_spec('cryptography'),
                     'optional login dependencies unavailable')
class TokenTests(unittest.TestCase):
    def setUp(self):
        import jwt
        from cryptography.hazmat.primitives.asymmetric import rsa
        self.jwt = jwt
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(self.key.public_key()))
        self.jwk['kid'] = 'fixture-key'
        self.claims = {'iss': pilot.AUTH, 'aud': 'oaiapp_test', 'sub': 'test-subject',
                       'nonce': 'test-nonce', 'exp': int(time.time()) + 3600}

    def tokens(self, **claims):
        return {'id_token': self.jwt.encode({**self.claims, **claims}, self.key, algorithm='RS256',
                                            headers={'kid': 'fixture-key'}),
                'access_token': 'fixture-access', 'token_type': 'Bearer',
                'scope': pilot.PERMISSION, 'expires_in': 3600}

    def validate(self, tokens):
        return pilot.validate_tokens(tokens, 'oaiapp_test', 'test-nonce', {'keys': [self.jwk]})

    def test_signed_token(self):
        self.assertEqual(self.validate(self.tokens()), 'test-subject')

    def test_wrong_issuer_audience_nonce_expiry_or_missing_permission(self):
        for claims in ({'iss': 'https://example.invalid'}, {'aud': 'wrong'}, {'nonce': 'wrong'}, {'exp': 1}):
            with self.subTest(claims=claims), self.assertRaises((ValueError, self.jwt.PyJWTError)):
                self.validate(self.tokens(**claims))
        with self.assertRaises(pilot.Refused):
            self.validate({**self.tokens(), 'scope': 'openid'})

    def test_tampered_signature(self):
        tokens = self.tokens()
        parts = tokens['id_token'].split('.')
        parts[1] = base64.urlsafe_b64encode(json.dumps({**self.claims, 'sub': 'forged'}).encode()).decode().rstrip('=')
        with self.assertRaises(self.jwt.InvalidSignatureError):
            self.validate({**tokens, 'id_token': '.'.join(parts)})
