"""Exchange GitHub's workload identity for an API token and upload a test file.

Print only HTTP status and the transfer ticket, never tokens or presigned URLs.
This small smoke test does not replace the full OAuth2 negative-test suite.
"""
import json
import os
import urllib.error
import urllib.parse
import urllib.request
import uuid


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


OPENER = urllib.request.build_opener(NoRedirect())


def request(url, method='GET', data=None, headers=None):
    try:
        with OPENER.open(urllib.request.Request(url, data=data, headers=headers or {}, method=method), timeout=30) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        # Do not include error bodies or request URLs in logs.
        return error.code, error.read(16384)


def report_entra_error(body):
    """Only emit validated diagnostic codes; descriptions can contain assertions."""
    try:
        error = json.loads(body)
    except (ValueError, UnicodeError):
        return
    if not isinstance(error, dict):
        return
    safe = {}
    allowed = {'invalid_request', 'invalid_client', 'invalid_grant', 'invalid_scope',
               'unauthorized_client', 'access_denied', 'temporarily_unavailable', 'server_error'}
    if isinstance(error.get('error'), str) and error['error'] in allowed:
        safe['error'] = error['error']
    codes = error.get('error_codes', [])
    if isinstance(codes, list):
        safe['error_codes'] = [code for code in codes if type(code) is int and 0 <= code < 1000000000][:10]
    for field in ('correlation_id', 'trace_id'):
        try:
            safe[field] = str(uuid.UUID(error[field]))
        except (KeyError, ValueError, TypeError, AttributeError):
            pass
    print('Entra diagnostic: ' + json.dumps(safe))


def require(status, expected, label):
    print(f'{label}: HTTP {status} (expected {expected})')
    if status != expected:
        raise RuntimeError(f'{label} failed')


def main():
    tenant = str(uuid.UUID(os.environ['ENTRA_TENANT_ID']))
    consumer = str(uuid.UUID(os.environ['ENTRA_CONSUMER_CLIENT_ID']))
    # Validate the configured audience ID; the requested resource URI uses the tenant.
    uuid.UUID(os.environ['ENTRA_API_CLIENT_ID'])
    # Fixed development endpoint avoids sending an access token to an arbitrary input.
    endpoint = 'https://5yc80qz674.execute-api.eu-west-2.amazonaws.com'
    oidc_url = os.environ['ACTIONS_ID_TOKEN_REQUEST_URL']
    oidc_url += ('&' if '?' in oidc_url else '?') + urllib.parse.urlencode({'audience': 'api://AzureADTokenExchange'})
    status, body = request(oidc_url, headers={'Authorization': 'Bearer ' + os.environ['ACTIONS_ID_TOKEN_REQUEST_TOKEN']})
    require(status, 200, 'GitHub identity')
    assertion = json.loads(body)['value']
    data = urllib.parse.urlencode({
        'client_id': consumer, 'grant_type': 'client_credentials',
        'scope': f'api://{tenant}/integration-hub-file-transfer-api/.default',
        'client_assertion_type': 'urn:ietf:params:oauth:client-assertion-type:jwt-bearer',
        'client_assertion': assertion,
    }).encode()
    status, body = request(f'https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token', 'POST', data,
                           {'Content-Type': 'application/x-www-form-urlencoded'})
    if status != 200:
        report_entra_error(body)
    require(status, 200, 'Entra token exchange')
    token = json.loads(body)['access_token']
    headers = {'Content-Type': 'application/json'}
    for label, authorization in [('Missing token', None), ('Basic authentication', 'Basic dXNlcjpwYXNz'), ('Invalid token', 'Bearer invalid')]:
        check_headers = dict(headers)
        if authorization:
            check_headers['Authorization'] = authorization
        status, _ = request(endpoint + '/transfer-tickets', 'POST', b'{}', check_headers)
        if label == 'Basic authentication':
            # Gateway may return 401 or 403 for a non-Bearer credential.
            require(status in (401, 403), True, f'{label} rejected (HTTP {status})')
        else:
            require(status, 401, label)
    headers['Authorization'] = 'Bearer ' + token
    status, _ = request(endpoint + '/transfer-tickets', 'POST', b'{}', headers)
    require(status, 400, 'Authorised request validation')
    payload = b'Integration Hub automated development upload test.\n'
    data = json.dumps({'clientId': 'products-poc', 'fileName': 'automated-auth-test.txt',
                       'contentType': 'text/plain', 'sizeBytes': len(payload)}).encode()
    status, body = request(endpoint + '/transfer-tickets', 'POST', data, headers)
    if status not in (200, 201):
        require(status, 201, 'Create upload ticket')
    ticket = json.loads(body)
    upload = ticket['upload']
    parsed = urllib.parse.urlsplit(upload['url'])
    if parsed.scheme != 'https' or parsed.hostname != 'integration-hub-file-transfer-development-incoming.s3.eu-west-2.amazonaws.com' or parsed.port not in (None, 443) or parsed.username:
        raise RuntimeError('Unexpected upload destination')
    if upload['method'] != 'PUT':
        raise RuntimeError('Unexpected upload method')
    status, _ = request(upload['url'], 'PUT', payload, upload['headers'])
    require(status, 200, 'S3 upload')
    print('Transfer ticket: ' + str(uuid.UUID(ticket['transferTicket'])))
    print('Upload accepted. Scanning, Slack delivery and download require separate verification.')


if __name__ == '__main__':
    try:
        main()
    except Exception:
        # Exceptions from HTTP clients can contain credential-bearing URLs.
        print('Smoke test failed. Check the status lines and environment configuration; sensitive details suppressed.')
        raise SystemExit(1)
