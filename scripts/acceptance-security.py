"""Deployed development JWT boundaries. Missing fixture tokens are incomplete, not passes."""
import argparse
import base64
import json
import os
import time
import uuid
from acceptance_common import API, Report, request, token

FIXTURES = {
    'expired': ('ENTRA_EXPIRED_TOKEN', 401),
    'wrong-audience': ('ENTRA_WRONG_AUDIENCE_TOKEN', 401),
    'wrong-tenant': ('ENTRA_WRONG_TENANT_TOKEN', 401),
    'delegated-user': ('ENTRA_USER_TOKEN', 403),
    'missing-role': ('ENTRA_MISSING_ROLE_TOKEN', 403),
    'unmapped-consumer': ('ENTRA_UNMAPPED_APP_TOKEN', 403),
}


def tamper(value):
    pieces = value.split('.')
    claims = json.loads(base64.urlsafe_b64decode(pieces[1] + '=' * (-len(pieces[1]) % 4)))
    claims['acceptance_tampered'] = str(uuid.uuid4())
    pieces[1] = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip('=')
    return '.'.join(pieces)


def run(valid, report):
    ticket = str(uuid.uuid4())
    routes = [('POST', '/transfer-tickets', 400),
              ('POST', f'/transfer-tickets/{ticket}/parts', 404),
              ('POST', f'/transfer-tickets/{ticket}/complete', 404),
              ('DELETE', f'/transfer-tickets/{ticket}', 404)]
    cases = [('missing', None, 401), ('basic', 'Basic dXNlcjpwYXNz', [401, 403]),
             ('invalid', 'Bearer not-a-jwt', 401), ('static-bearer', 'Bearer products-poc.token', 401), ('tampered', 'Bearer ' + tamper(valid), 401)]
    for name, (variable, expected) in FIXTURES.items():
        if os.environ.get(variable):
            cases.append((name, 'Bearer ' + os.environ[variable], expected))
        else:
            report.record(name, 'missing')
    for index, (method, path, expected) in enumerate(routes):
        for name, bearer, status in [('valid-boundary', 'Bearer ' + valid, expected), *cases]:
            actual, _, _ = request(API + path, method, payload={} if method == 'POST' else None, bearer=bearer)
            report.check(f'route-{index}/{name}', actual, status)
            time.sleep(0.15)
    # A syntactically valid customer outside the current consumer's grant.
    # Fail if it has accidentally become authorised; do not silently create a ticket.
    actual, _, _ = request(API + '/transfer-tickets', payload={
        'clientId': 'acceptance-denied-customer', 'fileName': 'denied.txt',
        'contentType': 'text/plain', 'sizeBytes': 1}, bearer='Bearer ' + valid)
    report.check('cross-customer-create', actual, 403)
    # Existing session access is exercised by acceptance-recovery with a real second token.
    return report.exit_code()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report', default='security-results.json')
    args = parser.parse_args()
    report = Report(args.report)
    try:
        return run(token(), report)
    except Exception:
        report.record('security-runner', 'fail')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
