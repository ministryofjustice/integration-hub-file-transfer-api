"""Development-only acceptance helpers. Reports never contain HTTP bodies or tokens."""
import importlib.util
from datetime import datetime, timezone
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

API = 'https://5yc80qz674.execute-api.eu-west-2.amazonaws.com'
S3_HOST = 'integration-hub-file-transfer-development-incoming.s3.eu-west-2.amazonaws.com'


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


OPENER = urllib.request.build_opener(NoRedirect())


def token():
    if os.environ.get('ENTRA_ACCESS_TOKEN'):
        return os.environ['ENTRA_ACCESS_TOKEN']
    spec = importlib.util.spec_from_file_location('github_smoke', Path(__file__).with_name('smoke-github-entra.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.acquire_token()


def request(url, method='POST', payload=None, bearer=None, data=None, headers=None):
    headers = dict(headers or {})
    if payload is not None:
        data = json.dumps(payload).encode()
        headers['Content-Type'] = 'application/json'
    if bearer is not None:
        if not url.startswith(API + '/'):
            raise ValueError('Refusing to send an API token to another origin')
        headers['Authorization'] = bearer
    req = urllib.request.Request(url, method=method, data=data, headers=headers)
    try:
        with OPENER.open(req, timeout=300) as response:
            body = response.read(1024 * 1024)
            return response.status, body, response.headers
    except urllib.error.HTTPError as error:
        return error.code, error.read(16384), error.headers
    except (urllib.error.URLError, TimeoutError, ConnectionError):
        return 0, b'', {}


def part_url(part):
    parsed = urllib.parse.urlsplit(part['url'])
    if parsed.scheme != 'https' or parsed.hostname != S3_HOST or parsed.port not in (None, 443) or parsed.username or part.get('method') != 'PUT':
        raise ValueError('Unexpected S3 part destination')
    return part['url']


class Report:
    def __init__(self, path):
        self.path = Path(path)
        self.results = []
        self.started = datetime.now(timezone.utc).isoformat()
        self.revision = os.environ.get("GITHUB_SHA", "local-unrecorded")

    def record(self, name, status, actual=None, expected=None):
        row = {'case': name, 'result': status}
        if actual is not None:
            row['actual'] = actual
        if expected is not None:
            row['expected'] = expected
        self.results.append(row)
        print(json.dumps(row))
        self.path.write_text(json.dumps({'development_only': True, 'started_at': self.started, 'revision': self.revision, 'results': self.results}, indent=2) + '\n')

    def check(self, name, actual, expected):
        allowed = expected if isinstance(expected, (list, tuple)) else [expected]
        self.record(name, 'pass' if actual in allowed else 'fail', actual, list(allowed))
        return actual in allowed

    def exit_code(self):
        if any(r['result'] == 'fail' for r in self.results):
            return 1
        return 2 if any(r['result'] in ('missing', 'unverified') for r in self.results) else 0
