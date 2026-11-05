#!/usr/bin/env python3
"""Local development multipart upload. Python 3; no extra packages required."""
import argparse
import getpass
import json
import mimetypes
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

TENANT = '0bb413d7-160d-4839-868a-f3d46537f6af'
CONSUMER = '0ded1120-7c5c-46ab-b0bb-1811a107f191'
SCOPE = f'api://{TENANT}/integration-hub-file-transfer-api/.default'
API = 'https://5yc80qz674.execute-api.eu-west-2.amazonaws.com'
BUCKET_HOST = 'integration-hub-file-transfer-development-incoming.s3.eu-west-2.amazonaws.com'


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class UploadError(Exception):
    pass


class Client:
    def __init__(self, secret):
        self.secret = secret
        self.token = None
        self.expiry = 0
        self.opener = urllib.request.build_opener(NoRedirect())

    def token_value(self):
        if self.token and time.monotonic() < self.expiry:
            return self.token
        data = urllib.parse.urlencode({
            'grant_type': 'client_credentials', 'client_id': CONSUMER,
            'client_secret': self.secret, 'scope': SCOPE,
        }).encode()
        req = urllib.request.Request(
            f'https://login.microsoftonline.com/{TENANT}/oauth2/v2.0/token',
            data=data, headers={'Content-Type': 'application/x-www-form-urlencoded'})
        try:
            with self.opener.open(req, timeout=60) as response:
                result = json.load(response)
        except urllib.error.HTTPError as error:
            codes = []
            try:
                codes = json.loads(error.read(16384)).get('error_codes', [])
            except (ValueError, UnicodeError, AttributeError):
                pass
            codes = [c for c in codes if type(c) is int] if isinstance(codes, list) else []
            raise UploadError(f'Entra token request: HTTP {error.code}; error codes {codes}') from None
        self.token = result['access_token']
        self.expiry = time.monotonic() + max(0, int(result.get('expires_in', 3600)) - 60)
        print('Fresh Entra token obtained.')
        return self.token

    def api(self, path, payload=None, method='POST'):
        if not path.startswith('/transfer-tickets'):
            raise UploadError('Unexpected API operation path')
        for attempt in range(2):
            data = json.dumps(payload).encode() if payload is not None else None
            req = urllib.request.Request(API + path, data=data, method=method,
                headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ' + self.token_value()})
            try:
                with self.opener.open(req, timeout=120) as response:
                    return json.load(response)
            except urllib.error.HTTPError as error:
                if error.code == 401 and attempt == 0:
                    self.token = None
                    continue
                # Avoid printing bodies or URLs which may contain credentials.
                raise UploadError(f'API {method} {path}: HTTP {error.code}') from None
        raise UploadError('API authentication failed')

    def upload_part(self, part, path, offset, size):
        url = part['url']
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme != 'https' or parsed.hostname != BUCKET_HOST or parsed.port not in (None, 443) or parsed.username:
            raise UploadError('Unexpected S3 upload destination')
        if part.get('method') != 'PUT':
            raise UploadError('Expected an S3 PUT part URL')
        headers = dict(part.get('headers') or {})
        if any(k.lower() == 'authorization' for k in headers):
            raise UploadError('Unexpected Authorization header for S3')
        headers['Content-Length'] = str(size)
        for attempt in range(3):
            req = urllib.request.Request(url, data=read_chunk(path, offset, size), method='PUT', headers=headers)
            try:
                with self.opener.open(req, timeout=300) as response:
                    response.read()
                    etag = response.headers.get('ETag')
                    if response.status != 200 or not etag:
                        raise UploadError('S3 did not return HTTP 200 and an ETag')
                    return etag
            except urllib.error.HTTPError as error:
                if error.code not in (429, 500, 502, 503, 504) or attempt == 2:
                    raise UploadError(f'S3 part PUT: HTTP {error.code}; request a fresh URL if expired') from None
            except (urllib.error.URLError, TimeoutError, ConnectionError):
                if attempt == 2:
                    raise UploadError('S3 part PUT connection failed after three attempts') from None
            time.sleep(2 ** attempt)
        raise UploadError('Part upload failed')


def read_chunk(path, offset, size):
    """Stream one part, using at most 1 MiB per read. No chunk files created."""
    with path.open('rb') as source:
        source.seek(offset)
        remaining = size
        while remaining:
            block = source.read(min(1024 * 1024, remaining))
            if not block:
                raise UploadError('File ended before the declared size; do not modify it during upload')
            remaining -= len(block)
            yield block


def run(path, content_type, secret):
    before = path.stat()
    client = Client(secret)
    ticket = client.api('/transfer-tickets', {
        'clientId': 'products-poc', 'fileName': path.name,
        'contentType': content_type, 'sizeBytes': before.st_size,
        'requestedExpirySeconds': 3600,
    })
    if 'multipart' not in ticket:
        raise UploadError('API selected single PUT. For multipart use a real file at least 1 GiB; do not inflate sizeBytes.')
    transfer = ticket['transferTicket']
    info = ticket['multipart']
    chunk_size = int(info['partSizeBytes'])
    total = int(info['totalParts'])
    if chunk_size <= 0 or total != (before.st_size + chunk_size - 1) // chunk_size:
        raise UploadError('Unexpected multipart size configuration')
    print(f'Transfer ticket: {transfer}')
    print(f'Uploading {before.st_size:,} bytes as {total} parts of up to {chunk_size:,} bytes.')
    urls = {int(p['partNumber']): p for p in info.get('initialParts', [])}
    completed = []
    try:
        for number in range(1, total + 1):
            # Re-presign after a long upload so an old initial URL is not reused.
            part = urls.get(number)
            if part is None or number > 1:
                result = client.api(f'/transfer-tickets/{transfer}/parts', {'partNumbers': [number]})
                part = next(p for p in result['parts'] if int(p['partNumber']) == number)
            offset = (number - 1) * chunk_size
            length = min(chunk_size, before.st_size - offset)
            etag = client.upload_part(part, path, offset, length)
            completed.append({'partNumber': number, 'eTag': etag})
            print(f'Part {number}/{total} uploaded ({length:,} bytes).')
        after = path.stat()
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise UploadError('File changed while uploading; refusing completion')
        print('Completing multipart upload...')
        result = client.api(f'/transfer-tickets/{transfer}/complete', {'parts': completed})
        if result.get('status') != 'completed':
            raise UploadError('Completion did not return completed status')
        print('SUCCESS: multipart upload completed.')
        print(f'Transfer ticket: {transfer}')
        print('Scanning, Slack notification and pickup/download are separate checks.')
    except Exception:
        print(f'Upload stopped. Keep this transfer ticket for investigation: {transfer}')
        print('A failed/timed-out completion can be ambiguous. Check before starting another upload.')
        print('To cancel a confirmed unfinished upload, use Bruno Abort Multipart Upload with this ticket.')
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('file', type=Path, help='Actual file to upload; at least 1 GiB for multipart')
    parser.add_argument('--content-type', help='MIME type allowed for products-poc; inferred from filename by default')
    args = parser.parse_args()
    path = args.file.expanduser().resolve()
    if not path.is_file():
        parser.error('File does not exist or is not a regular file')
    if path.stat().st_size < 1024 ** 3:
        parser.error('Choose an actual file at least 1 GiB for the current development multipart threshold')
    mime = args.content_type or mimetypes.guess_type(path.name)[0] or 'application/octet-stream'
    print(f'File: {path.name}; bytes: {path.stat().st_size:,}; content type: {mime}')
    secret = getpass.getpass('Development consumer client secret (hidden): ')
    if not secret:
        parser.error('Client secret is required')
    try:
        run(path, mime, secret)
    except UploadError as error:
        print(f'ERROR: {error}')
        return 1
    except Exception:
        print('ERROR: upload failed; sensitive exception details suppressed. Check API logs using the transfer ticket.')
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
