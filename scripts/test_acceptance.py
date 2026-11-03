import base64
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import acceptance_common as common


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(filename))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


security = load('security', 'acceptance-security.py')
recovery = load('recovery', 'acceptance-recovery.py')


class AcceptanceTests(unittest.TestCase):
    def test_report_never_treats_missing_as_pass(self):
        with tempfile.TemporaryDirectory() as folder, contextlib.redirect_stdout(io.StringIO()):
            report = common.Report(Path(folder)/'report.json')
            report.record('fixture', 'missing')
            self.assertEqual(2, report.exit_code())
            report.check('network-error', 0, 401)
            self.assertEqual(1, report.exit_code())
            self.assertEqual(2, len(json.loads(report.path.read_text())['results']))

    def test_tamper_changes_payload_but_retains_signature(self):
        body = base64.urlsafe_b64encode(b'{"aud":"api"}').decode().rstrip('=')
        original = 'header.'+body+'.signature'
        changed = security.tamper(original)
        self.assertNotEqual(original, changed)
        self.assertEqual('signature', changed.split('.')[2])

    def test_refuse_api_token_to_other_origin(self):
        with self.assertRaises(ValueError):
            common.request('https://example.test/path', bearer='Bearer private-token')

    def test_s3_destination_validation(self):
        for url in ('http://' + common.S3_HOST, 'https://example.test/file',
                    'https://user@'+common.S3_HOST+'/file', 'https://'+common.S3_HOST+':444/file'):
            with self.subTest(url=url), self.assertRaises(ValueError):
                common.part_url({'url':url, 'method':'PUT'})

    def test_security_reports_missing_real_fixtures(self):
        body = base64.urlsafe_b64encode(b'{"aud":"api"}').decode().rstrip('=')
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {}, clear=True), contextlib.redirect_stdout(io.StringIO()):
            report = common.Report(Path(folder)/'report.json')
            with patch.object(security, 'request', return_value=(401,b'',{})), patch.object(security.time,'sleep'):
                security.run('h.'+body+'.sig', report)
            missing = [r for r in report.results if r['result']=='missing']
            self.assertEqual(len(security.FIXTURES), len(missing))
            self.assertTrue(any(r['result']=='fail' for r in report.results))

    def test_download_verification_uses_actual_bytes(self):
        with tempfile.TemporaryDirectory() as folder, contextlib.redirect_stdout(io.StringIO()):
            source=Path(folder)/'source';target=Path(folder)/'download'
            source.write_bytes(b'original');target.write_bytes(b'original')
            report=common.Report(Path(folder)/'report.json')
            report.record('downloaded-file-checksum','unverified')
            self.assertEqual(0,recovery.verify_download(source,target,report))
            target.write_bytes(b'tampered')
            self.assertEqual(1,recovery.verify_download(source,target,report))

    def test_recovery_flow_includes_retries_latest_etags_abort_and_conflicts(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {}, clear=True), contextlib.redirect_stdout(io.StringIO()):
            path=Path(folder)/'input';path.write_bytes(b'a'*24)
            report=common.Report(Path(folder)/'report.json')
            worker=recovery.Recovery(path,'private-token',report,'application/octet-stream')
            sessions=[]; complete_count={}; uploads=[]
            def fake_api(route,payload=None,method='POST',other=None):
                if route=='/transfer-tickets':
                    ticket='ticket-'+str(len(sessions));sessions.append(ticket)
                    return 200,json.dumps({'transferTicket':ticket,'multipart':{'partSizeBytes':8,'totalParts':3}}).encode(),{}
                ticket=route.split('/')[2]
                if route.endswith('/parts'):
                    if ticket=='ticket-1' and method=='POST':return 409,b'',{}
                    return 200,json.dumps({'parts':[{'partNumber':payload['partNumbers'][0]}]}).encode(),{}
                if method=='DELETE':
                    complete_count['abort']=complete_count.get('abort',0)+1
                    return (200 if complete_count['abort']==1 else 409),b'',{}
                if payload['parts'][0]['eTag']=='"not-the-uploaded-etag"':return 400,b'',{}
                if ticket=='ticket-1':return 409,b'',{}
                complete_count[ticket]=complete_count.get(ticket,0)+1
                if complete_count[ticket]==1:
                    self.assertEqual('tag-2',payload['parts'][0]['eTag'])
                    self.assertEqual([1,2,3],[p['partNumber'] for p in payload['parts']])
                return (200 if complete_count[ticket]==1 else 409),b'',{}
            def fake_upload(part,offset,length):
                uploads.append((offset,length));return 'tag-'+str(len(uploads))
            # The second session needs presigning before it is aborted.
            with patch.object(worker,'api',side_effect=fake_api), patch.object(worker,'upload',side_effect=fake_upload), patch.object(worker,'interrupted_put'), patch.object(worker,'presign',return_value={'method':'PUT'}):
                worker.run()
            self.assertEqual([(0,8),(0,8),(8,8),(16,8),(0,8)],uploads)
            self.assertEqual(set(),worker.active)
            self.assertFalse(any(r['result']=='fail' for r in report.results))
            self.assertEqual(2,report.exit_code())  # Missing second consumer and unverified download.


if __name__=='__main__':
    unittest.main()
