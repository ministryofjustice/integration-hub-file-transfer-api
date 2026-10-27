"""Read-only OAuth2 boundary checks; credentials are supplied through environment variables.

Required: API_ENDPOINT, ENTRA_ACCESS_TOKEN, ENTRA_EXPIRED_TOKEN,
ENTRA_WRONG_AUDIENCE_TOKEN, ENTRA_WRONG_TENANT_TOKEN, ENTRA_USER_TOKEN,
ENTRA_MISSING_ROLE_TOKEN, ENTRA_UNMAPPED_APP_TOKEN.
Does not prove file transfer, cryptographic internals, or production acceptance.
"""
import base64
import json
import os
import urllib.error
import urllib.parse
import urllib.request
import uuid


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def main():
    endpoint = os.environ['API_ENDPOINT'].rstrip('/')
    parsed = urllib.parse.urlsplit(endpoint)
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.query or parsed.fragment:
        raise ValueError('API_ENDPOINT must be a trusted HTTPS API base URL')
    valid = os.environ['ENTRA_ACCESS_TOKEN']
    fixtures = [(name, os.environ[name], expected) for name, expected in (
        ('ENTRA_EXPIRED_TOKEN', 401), ('ENTRA_WRONG_AUDIENCE_TOKEN', 401),
        ('ENTRA_WRONG_TENANT_TOKEN', 401), ('ENTRA_USER_TOKEN', 403),
        ('ENTRA_MISSING_ROLE_TOKEN', 403), ('ENTRA_UNMAPPED_APP_TOKEN', 403))]
    # Change payload while preserving signature: verification must fail.
    segments = valid.split('.')
    claims = json.loads(base64.urlsafe_b64decode(segments[1] + '=' * (-len(segments[1]) % 4)))
    claims['smoke_tampered'] = str(uuid.uuid4())
    segments[1] = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip('=')
    fixtures.append(('tampered-signature', '.'.join(segments), 401))
    ticket = str(uuid.uuid4())
    routes = [('POST', '/transfer-tickets', 400),
              ('POST', f'/transfer-tickets/{ticket}/parts', 404),
              ('POST', f'/transfer-tickets/{ticket}/complete', 404),
              ('DELETE', f'/transfer-tickets/{ticket}', 404)]
    opener = urllib.request.build_opener(NoRedirect())
    failures = 0
    for method, path, success in routes:
        cases = [('valid-application', 'Bearer ' + valid, success),
                 ('missing', None, 401), ('legacy-basic', 'Basic dXNlcjpwYXNz', 401),
                 ('legacy-static-bearer', 'Bearer products-poc.token', 401)]
        cases += [(name, 'Bearer ' + token, status) for name, token, status in fixtures]
        for name, auth, expected in cases:
            headers = {'Content-Type': 'application/json'}
            if auth:
                headers['Authorization'] = auth
            request = urllib.request.Request(endpoint + path, data=b'{}' if method == 'POST' else None,
                                             headers=headers, method=method)
            try:
                with opener.open(request, timeout=30) as response:
                    status = response.status
            except urllib.error.HTTPError as error:
                status = error.code
            except urllib.error.URLError:
                status = 'connection-error'
            ok = status == expected
            failures += not ok
            print(json.dumps({'method': method, 'path': path, 'case': name,
                              'expected': expected, 'actual': status, 'passed': ok}))
    raise SystemExit(1 if failures else 0)


if __name__ == '__main__':
    main()
