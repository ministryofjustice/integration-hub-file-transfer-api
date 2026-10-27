import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('smoke', Path(__file__).with_name('smoke-github-entra.py'))
smoke = importlib.util.module_from_spec(spec)
spec.loader.exec_module(smoke)


class SmokeTests(unittest.TestCase):
    def run_smoke(self, upload_url):
        ticket = {'transferTicket': '00000000-0000-0000-0000-000000000004',
                  'upload': {'method': 'PUT', 'url': upload_url,
                             'headers': {'Content-Type': 'text/plain'}}}
        responses = [(200, b'{"value":"private-assertion"}'),
                     (200, b'{"access_token":"private-token"}'),
                     (401, b''), (401, b''), (401, b''), (400, b''),
                     (201, json.dumps(ticket).encode()), (200, b'')]
        environment = {'ENTRA_TENANT_ID': '00000000-0000-0000-0000-000000000001',
                       'ENTRA_CONSUMER_CLIENT_ID': '00000000-0000-0000-0000-000000000002',
                       'ENTRA_API_CLIENT_ID': '00000000-0000-0000-0000-000000000003',
                       'ACTIONS_ID_TOKEN_REQUEST_URL': 'https://example.test/token?x=1',
                       'ACTIONS_ID_TOKEN_REQUEST_TOKEN': 'github-secret'}
        output = io.StringIO()
        with patch.dict(os.environ, environment), patch.object(smoke, 'request', side_effect=responses) as request, contextlib.redirect_stdout(output):
            smoke.main()
        return request.call_args_list, output.getvalue()

    def test_exchange_upload_and_no_credential_output(self):
        calls, output = self.run_smoke('https://integration-hub-file-transfer-development-incoming.s3.eu-west-2.amazonaws.com/test?signature=private-signature')
        self.assertIn('client_assertion=private-assertion', calls[1].args[2].decode())
        self.assertEqual(calls[-1].args[3], {'Content-Type': 'text/plain'})
        self.assertNotIn('Authorization', calls[-1].args[3])
        for secret in ('private-assertion', 'private-token', 'private-signature', 'github-secret'):
            self.assertNotIn(secret, output)

    def test_reject_unexpected_upload_destination(self):
        with self.assertRaisesRegex(RuntimeError, 'Unexpected upload destination'):
            self.run_smoke('https://example.test/upload')

    def test_entra_diagnostic_omits_sensitive_fields(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            smoke.report_entra_error(json.dumps({
                'error': 'invalid_client', 'error_codes': [700213, 'private-token'],
                'correlation_id': '00000000-0000-0000-0000-000000000001',
                'trace_id': 'private-token', 'error_description': 'private-assertion',
                'access_token': 'private-token'}).encode())
        self.assertIn('700213', output.getvalue())
        self.assertIn('invalid_client', output.getvalue())
        self.assertNotIn('private', output.getvalue())

    def test_http_redirects_are_not_followed(self):
        self.assertIsNone(smoke.NoRedirect().redirect_request(None, None, 302, '', {}, 'https://example.test'))


if __name__ == '__main__':
    unittest.main()
