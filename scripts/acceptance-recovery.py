"""Real development multipart recovery tests. Uses an actual file >5 GiB; streams it once.

May create a clean file and Slack notification. Does not verify downstream checksums.
"""
import argparse
import hashlib
import http.client
import json
import os
import time
import urllib.parse
from pathlib import Path
from acceptance_common import API, Report, S3_HOST, part_url, request, token


class Recovery:
    def __init__(self, path, bearer, report, mime):
        self.path, self.bearer, self.report, self.mime = path, bearer, report, mime
        self.active = set()

    def api(self, path, payload=None, method='POST', other=None):
        result = request(API + path, method, payload=payload, bearer='Bearer ' + (other or self.bearer))
        if result[0] == 401 and not other and os.environ.get('ACTIONS_ID_TOKEN_REQUEST_URL'):
            self.bearer = token()
            result = request(API + path, method, payload=payload, bearer='Bearer ' + self.bearer)
        return result

    def create(self, expiry=3600):
        status, body, _ = self.api('/transfer-tickets', {
            'clientId': 'products-poc', 'fileName': 'recovery-acceptance.bin',
            'contentType': self.mime, 'sizeBytes': self.path.stat().st_size,
            'requestedExpirySeconds': expiry})
        if not self.report.check('create-session', status, 200):
            raise RuntimeError('Ticket creation failed')
        ticket = json.loads(body)
        if 'multipart' not in ticket:
            raise RuntimeError('An actual multipart ticket is required')
        transfer = ticket['transferTicket']
        self.active.add(transfer)
        self.report.record('created-session/' + transfer, 'pass')
        return transfer, ticket['multipart']

    def presign(self, transfer, number):
        status, body, _ = self.api(f'/transfer-tickets/{transfer}/parts', {'partNumbers': [number]})
        if status != 200:
            raise RuntimeError('Presign failed')
        return json.loads(body)['parts'][0]

    def upload(self, part, offset, length):
        # Reopen the source so retransmissions are byte-identical and memory stays bounded.
        def chunks():
            with self.path.open('rb') as stream:
                stream.seek(offset)
                remaining = length
                while remaining:
                    block = stream.read(min(1024 * 1024, remaining))
                    if not block:
                        raise RuntimeError('Source file changed')
                    remaining -= len(block)
                    yield block
        headers = dict(part.get('headers') or {})
        headers['Content-Length'] = str(length)
        status, _, response_headers = request(part_url(part), 'PUT', data=chunks(), headers=headers)
        if status != 200 or not response_headers.get('ETag'):
            raise RuntimeError('Part PUT failed')
        return response_headers['ETag']

    def interrupted_put(self, part, length):
        # Exercise a genuinely incomplete PUT, then retry that same part normally.
        parsed = urllib.parse.urlsplit(part_url(part))
        connection = http.client.HTTPSConnection(S3_HOST, timeout=30)
        try:
            connection.putrequest('PUT', parsed.path + '?' + parsed.query)
            connection.putheader('Content-Length', str(length))
            for name, value in (part.get('headers') or {}).items():
                connection.putheader(name, value)
            connection.endheaders()
            with self.path.open('rb') as stream:
                connection.send(stream.read(min(1024 * 1024, max(1, length // 2))))
        finally:
            connection.close()
        self.report.record('interrupted-part-connection', 'pass')
        # The successful retry and completed object prove recovery, not this closed socket alone.

    def run(self, expired_url=False):
        before = self.path.stat()
        transfer, info = self.create()
        chunk, total = int(info['partSizeBytes']), int(info['totalParts'])
        size = before.st_size
        if chunk <= 0 or total != (size + chunk - 1) // chunk:
            raise RuntimeError('Unexpected part configuration')
        second = os.environ.get('ENTRA_OTHER_CUSTOMER_TOKEN')
        if second:
            status, _, _ = self.api('/transfer-tickets', {}, other=second)
            if not self.report.check('second-consumer-valid-boundary', status, 400):
                raise RuntimeError('Second consumer is not an authorised mapped application')
            for action, payload, method in [('parts', {'partNumbers': [1]}, 'POST'),
                                            ('complete', {'parts': [{'partNumber': 1, 'eTag': '"not-uploaded"'}]}, 'POST'),
                                            ('', None, 'DELETE')]:
                path = f'/transfer-tickets/{transfer}' + ('/' + action if action else '')
                status, _, _ = self.api(path, payload, method, other=second)
                self.report.check('cross-customer-session/' + (action or 'abort'), status, 403)
        else:
            self.report.record('cross-customer-session', 'missing')
        part = self.presign(transfer, 1)
        self.interrupted_put(part, min(chunk, size))
        etag = self.upload(part, 0, min(chunk, size))
        # Exercise a duplicate PUT. Completion always uses the latest returned ETag.
        etag = self.upload(part, 0, min(chunk, size))
        self.report.record('retry-and-reupload-first-part', 'pass')
        status, _, _ = self.api(f'/transfer-tickets/{transfer}/complete', {
            'parts': [{'partNumber': 1, 'eTag': '"not-the-uploaded-etag"'}]})
        self.report.check('wrong-etag', status, 400)
        parts = [{'partNumber': 1, 'eTag': etag}]
        for number in range(2, total + 1):
            offset = (number - 1) * chunk
            etag = self.upload(self.presign(transfer, number), offset, min(chunk, size - offset))
            parts.append({'partNumber': number, 'eTag': etag})
            print(f'Uploaded part {number}/{total}')
        after = self.path.stat()
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise RuntimeError('Source changed during upload')
        status, _, _ = self.api(f'/transfer-tickets/{transfer}/complete', {'parts': parts})
        if not self.report.check('complete-after-recovery', status, 200):
            raise RuntimeError('Completion failed; inspect before retrying')
        self.active.discard(transfer)
        status, _, _ = self.api(f'/transfer-tickets/{transfer}/complete', {'parts': parts})
        self.report.check('repeat-completion-is-conflict', status, 409)
        aborted, _ = self.create()
        # Leave one uploaded part, then cancel and verify every follow-up is rejected.
        self.upload(self.presign(aborted, 1), 0, min(chunk, size))
        status, _, _ = self.api(f'/transfer-tickets/{aborted}', method='DELETE')
        if self.report.check('abort-partially-uploaded-session', status, 200):
            self.active.discard(aborted)
        for action, payload, method in [('parts', {'partNumbers': [1]}, 'POST'),
                                        ('complete', {'parts': parts}, 'POST'), ('', None, 'DELETE')]:
            path = f'/transfer-tickets/{aborted}' + ('/' + action if action else '')
            status, _, _ = self.api(path, payload, method)
            self.report.check('after-abort/' + (action or 'abort-again'), status, 409)
        if expired_url:
            expired, details = self.create(expiry=3)
            old = details['initialParts'][0]
            wait = int(details['expiresInSeconds']) + 3
            if wait > 65:
                raise RuntimeError('Unexpected expiry; refusing a long wait')
            time.sleep(wait)
            # Empty PUT cannot complete the source file even if the expiry check fails.
            status, _, _ = request(part_url(old), 'PUT', data=b'', headers={'Content-Length': '0'})
            self.report.check('expired-s3-url', status, 403)
            renewed = self.presign(expired, 1)
            self.upload(renewed, 0, min(chunk, size))
            self.report.record('fresh-url-after-expiry-uploads-part', 'pass')
            status, _, _ = self.api(f'/transfer-tickets/{expired}', method='DELETE')
            if self.report.check('abort-after-url-renewal', status, 200):
                self.active.discard(expired)
        else:
            self.report.record('expired-url-and-session', 'missing')
        expired_ticket = os.environ.get('EXPIRED_TRANSFER_TICKET')
        if expired_ticket:
            import uuid
            expired_ticket = str(uuid.UUID(expired_ticket))
            for action, payload, method in [('parts', {'partNumbers': [1]}, 'POST'),
                                            ('complete', {'parts': parts}, 'POST'), ('', None, 'DELETE')]:
                path = f'/transfer-tickets/{expired_ticket}' + ('/' + action if action else '')
                status, body, _ = self.api(path, payload, method)
                # An inactive but unexpired session is also 409; check the actual expiry response.
                expired = status == 404 or (status == 409 and 'has expired' in json.loads(body).get('message', ''))
                self.report.check('expired-session/' + (action or 'abort'), expired, True)
        else:
            self.report.record('expired-session-fixture', 'missing')
        # Uploaded-byte fidelity is checked by hashing the collected file in the runbook.
        self.report.record('downloaded-file-checksum', 'unverified')

    def cleanup(self):
        for transfer in sorted(self.active):
            # Never auto-abort after ambiguous completion: operator must inspect the ticket.
            self.report.record('inspect-unfinished-session/' + transfer, 'unverified')


def verify_download(source, downloaded, report):
    def digest(path):
        value = hashlib.sha256()
        with path.open('rb') as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b''):
                value.update(block)
        return value.digest()
    report.results = [r for r in report.results if r['case'] != 'downloaded-file-checksum']
    matches = source.stat().st_size == downloaded.stat().st_size and digest(source) == digest(downloaded)
    report.check('downloaded-file-checksum', matches, True)
    return report.exit_code()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('file', type=Path)
    parser.add_argument('--content-type', default='application/octet-stream')
    parser.add_argument('--check-expiry', action='store_true', help='Verify an expired part URL, renew it within the active session and abort the test upload')
    parser.add_argument('--report', default='recovery-results.json')
    parser.add_argument('--verify-download', type=Path, help='Only compare this collected file with the source and update the existing report; no network calls')
    args = parser.parse_args()
    if not args.file.is_file() or args.file.stat().st_size <= 5 * 1024**3:
        parser.error('Supply an actual file larger than 5 GiB; do not inflate the declared size')
    report = Report(args.report)
    if args.verify_download:
        if not args.verify_download.is_file() or not report.path.is_file():
            parser.error('Download and an existing recovery report are required')
        report.results = json.loads(report.path.read_text())['results']
        return verify_download(args.file, args.verify_download, report)
    recovery = None
    try:
        recovery = Recovery(args.file, token(), report, args.content_type)
        recovery.run(args.check_expiry)
    except Exception:
        report.record('recovery-runner', 'fail')
    finally:
        if recovery:
            recovery.cleanup()
    return report.exit_code()


if __name__ == '__main__':
    raise SystemExit(main())
