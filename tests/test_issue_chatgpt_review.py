"""Offline subscription review/refresh contracts; no real credentials or network."""
import io
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import issue_chatgpt_review as review
from issue_chatgpt_pilot import read_record, write_record, private_store


def sse(*events):
    return io.BytesIO(b''.join(b'data: ' + json.dumps(item).encode() + b'\n\n' for item in events))


def complete(text=None):
    return {'type': 'response.completed', 'response': {'status': 'completed', 'output': [] if text is None else [
        {'type': 'message', 'content': [{'type': 'output_text', 'text': text}]}]}}


class StreamTests(unittest.TestCase):
    def test_terminal_or_deltas_require_completion(self):
        delta = {'type': 'response.output_text.delta', 'delta': '{"approved":false}'}
        self.assertEqual(review.read_stream(sse(delta, complete())), '{"approved":false}')
        self.assertEqual(review.read_stream(sse(complete('hello'))), 'hello')
        self.assertEqual(review.read_stream(sse(delta, complete(delta['delta']))), delta['delta'])
        for stream in (sse(delta), sse(complete()), sse(delta, complete('wrong'))):
            with self.assertRaises(ValueError):
                review.read_stream(stream)

    def test_usage_limit_in_stream_blocks(self):
        stream = sse({'type': 'response.failed', 'response': {
            'error': {'code': 'subscription_sharing_usage_limit_exceeded'}}})
        with self.assertRaises(review.ReviewBlocked) as caught:
            review.read_stream(stream)
        self.assertEqual(caught.exception.reason, 'usage-limit')

    def test_refusal_tool_call_failed_and_incomplete_block(self):
        for kind in ('response.refusal.delta', 'response.function_call_arguments.delta',
                     'response.incomplete', 'error'):
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                review.read_stream(sse({'type': kind}, complete('text')))
        event = complete('text')
        event['response']['status'] = 'failed'
        with self.assertRaises(ValueError):
            review.read_stream(sse(event))

    def test_terminal_refusal_or_tool_output_block(self):
        for item in ({'type': 'function_call'}, {'type': 'message', 'content': [{'type': 'refusal'}]}):
            event = complete()
            event['response']['output'] = [item]
            with self.assertRaises(ValueError):
                review.read_stream(sse(event))

    def test_stream_bounds(self):
        with self.assertRaises(ValueError):
            review.read_stream(io.BytesIO(b'x' * (review.MAX_TEXT + 1)))

    def test_strict_json_refuses_duplicate_keys_and_nonfinite(self):
        self.assertEqual(review.strict_json('{"head":"abc"}'), {'head': 'abc'})
        for value in ('{"approved":false,"approved":true}', '{"x":NaN}', '{"x":Infinity}', '```json\n{}\n```'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                review.strict_json(value)

    def test_fixed_diagnostics_do_not_include_provider_messages(self):
        error = review.blocked(429, 'private-provider-text')
        self.assertEqual(error.reason, 'usage-limit')
        self.assertIsNone(error.diagnostic()['code'])
        self.assertNotIn('private-provider-text', str(error))


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.record = {'client_id': 'oaiapp_fixture', 'subject': 'fixture-subject',
                       'expires_at': time.time() + 3600, 'tokens': {
                           'access_token': 'fixture-access', 'refresh_token': 'fixture-refresh',
                           'scope': review.PERMISSION, 'token_type': 'Bearer'}}
        write_record(self.root / 'registration.json', {'client_id': 'oaiapp_fixture'})
        self.save()
        self.fresh = {'access_token': 'fixture-new-access', 'refresh_token': 'fixture-new-refresh',
                      'scope': review.PERMISSION, 'expires_in': 3600, 'token_type': 'Bearer'}

    def save(self):
        write_record(self.root / 'credentials.json', self.record)

    def expire(self):
        self.record['expires_at'] = 0
        self.save()

    def test_current_access_never_refreshes(self):
        with patch.object(review, 'fetch') as network:
            self.assertEqual(review.session_access(self.root), 'fixture-access')
        network.assert_not_called()

    def test_refresh_atomically_rotates_and_preserves_registration(self):
        self.expire()
        with private_store(self.root), patch.object(review, 'fetch', return_value=self.fresh) as network:
            self.assertEqual(review.session_access(self.root), 'fixture-new-access')
            self.assertEqual(review.session_access(self.root), 'fixture-new-access')
        self.assertEqual(network.call_count, 1)
        sent = network.call_args.kwargs['data']
        self.assertNotIn('scope', sent)
        self.assertEqual(sent['client_id'], 'oaiapp_fixture')
        self.assertEqual(sent['resource'], review.API)
        saved = read_record(self.root / 'credentials.json')
        self.assertEqual(saved['subject'], 'fixture-subject')
        self.assertEqual(saved['tokens']['refresh_token'], 'fixture-new-refresh')
        self.assertEqual((self.root / 'credentials.json').stat().st_mode & 0o777, 0o600)

    def test_missing_permission_or_identity_change_blocks(self):
        for mutate in ({'client_id': 'oaiapp_wrong'}, {'tokens': {'scope': 'openid'}}):
            write_record(self.root / 'credentials.json', {**self.record, **mutate})
            with patch.object(review, 'fetch') as network, self.assertRaises(review.ReviewBlocked):
                review.session_access(self.root)
            network.assert_not_called()

    def test_ambiguous_refresh_never_replays_rotating_token(self):
        self.expire()
        with patch.object(review, 'fetch', side_effect=review.ReviewBlocked('network-unavailable')) as network:
            for _ in range(2):
                with self.assertRaises(review.ReviewBlocked):
                    review.session_access(self.root)
        self.assertEqual(network.call_count, 1)
        self.assertEqual(read_record(self.root / 'credentials.json'), self.record)

    def test_bad_refresh_clears_unusable_tokens_not_registration(self):
        self.expire()
        with patch.object(review, 'fetch', side_effect=review.blocked(400, 'invalid_grant')):
            with self.assertRaises(review.ReviewBlocked):
                review.session_access(self.root)
        self.assertEqual(read_record(self.root / 'credentials.json')['tokens'], {})
        self.assertEqual(read_record(self.root / 'registration.json')['client_id'], 'oaiapp_fixture')

    def test_invalid_refresh_result_does_not_replace_old_credentials(self):
        self.expire()
        with patch.object(review, 'fetch', return_value={**self.fresh, 'scope': 'openid'}):
            with self.assertRaises(review.ReviewBlocked):
                review.session_access(self.root)
        self.assertEqual(read_record(self.root / 'credentials.json'), self.record)

    def test_refresh_identity_failure_keeps_old_record_and_stops(self):
        self.expire()
        with patch.object(review, 'fetch', return_value={**self.fresh, 'id_token': 'fixture-jwt'}), \
                patch.object(review, 'verify_refresh_identity', side_effect=ValueError('fixture failure')):
            with self.assertRaises(review.ReviewBlocked):
                review.session_access(self.root)
        self.assertEqual(read_record(self.root / 'credentials.json'), self.record)

    def test_transport_has_no_api_key_fallback_and_checks_model(self):
        config = {'review_session_dir': str(self.root), 'review_included_only_confirmed': True}
        body = {'model': 'fixture-model', 'store': False, 'stream': True, 'input': []}
        with patch.object(review, 'fetch', return_value={'models': [
                {'slug': 'fixture-model', 'visibility': 'list'}]}), \
                patch.object(review, 'request', return_value=sse(complete('{"fixture":true}'))) as network:
            self.assertEqual(review.review_text(body, config), '{"fixture":true}')
        self.assertEqual(network.call_args.args, (review.API + '/responses',))
        self.assertEqual(network.call_args.kwargs['token'], 'fixture-access')
        for altered in ({**config, 'review_included_only_confirmed': False}, {**config, 'review_session_dir': None}):
            with patch.object(review, 'request') as network, self.assertRaises(review.ReviewBlocked):
                review.review_text(body, altered)
            network.assert_not_called()

    def test_session_lock_blocks_second_process_context(self):
        with private_store(self.root):
            with self.assertRaises(BlockingIOError), private_store(self.root):
                pass
