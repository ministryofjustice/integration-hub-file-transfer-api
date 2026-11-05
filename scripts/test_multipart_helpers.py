import contextlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


def load(name, file):
    spec=importlib.util.spec_from_file_location(name,Path(__file__).with_name(file))
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


helper=load('prepare','prepare-bruno-multipart.py')
uploader=load('upload','test-multipart-upload.py')


class MultipartHelpers(unittest.TestCase):
    def test_chunks_reconstruct_file_and_requests_do_not_embed_urls_or_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);collection=root/'collection';collection.mkdir()
            (collection/'bruno.json').write_text('{}')
            source=root/'NUMBER "test".bin'
            data=b'part-one'*(helper.MIN_PART//8)+b'last'
            source.write_bytes(data)
            output,count=helper.prepare(source,helper.MIN_PART,collection)
            self.assertEqual(2,count)
            self.assertEqual(data,b''.join(p.read_bytes() for p in sorted((output/'chunks').glob('*.bin'))))
            request=(output/'part-00001.bru').read_text()
            self.assertIn(json.dumps(source.name),request)
            self.assertIn('@file(_multipart-demo/chunks/part-00001.bin)',request)
            self.assertNotIn('https://',request)
            self.assertNotIn('Bearer ',request)
            manifest=json.loads((output/'manifest.json').read_text())
            self.assertEqual(len(data),manifest['sizeBytes'])
            self.assertEqual(helper.MIN_PART,manifest['partSizeBytes'])

    def test_existing_output_is_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/'bruno.json').write_text('{}')
            source=root/'source';source.write_bytes(b'hello')
            output,_=helper.prepare(source,helper.MIN_PART,root)
            (output/'keep').write_text('keep')
            with self.assertRaises(FileExistsError):helper.prepare(source,helper.MIN_PART,root)
            self.assertEqual('keep',(output/'keep').read_text())

    def test_bad_part_sizes_do_not_create_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/'bruno.json').write_text('{}')
            source=root/'source';source.write_bytes(b'hello')
            for size in (True,0,helper.MIN_PART-1,helper.MAX_PART+1):
                with self.subTest(size=size),self.assertRaises(ValueError):helper.prepare(source,size,root)
            self.assertFalse((root/'_multipart-demo').exists())

    def test_disk_shortage_removes_only_new_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/'bruno.json').write_text('{}')
            source=root/'source';source.write_bytes(b'hello')
            with patch.object(helper.shutil,'disk_usage') as disk:
                disk.return_value.free=0
                with self.assertRaises(ValueError):helper.prepare(source,helper.MIN_PART,root)
            self.assertFalse((root/'_multipart-demo').exists())
            self.assertEqual(b'hello',source.read_bytes())

    def test_uploader_reads_only_the_selected_part(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'source';path.write_bytes(b'0123456789')
            self.assertEqual(b'3456',b''.join(uploader.read_chunk(path,3,4)))
            with self.assertRaises(uploader.UploadError):list(uploader.read_chunk(path,9,3))

    def test_uploader_rejects_other_destinations_before_network(self):
        client=uploader.Client('fake-credential')
        for url in ('http://'+uploader.BUCKET_HOST+'/file','https://example.test/file'):
            with self.subTest(url=url),patch.object(client.opener,'open') as network:
                with self.assertRaises(uploader.UploadError):client.upload_part({'url':url,'method':'PUT'},Path('unused'),0,1)
                network.assert_not_called()

    def test_standalone_upload_passes_latest_etags_and_real_size_to_completion(self):
        with tempfile.TemporaryDirectory() as directory:
            source=Path(directory)/'source';source.write_bytes(b'abcdefghijkl')
            calls=[]
            class FakeClient:
                def __init__(self,secret):pass
                def api(self,path,payload):
                    calls.append((path,payload))
                    if path=='/transfer-tickets':
                        return {'transferTicket':'test-ticket','multipart':{'partSizeBytes':5,'totalParts':3,'initialParts':[{'partNumber':1}]}}
                    if path.endswith('/parts'):
                        return {'parts':[{'partNumber':payload['partNumbers'][0]}]}
                    return {'status':'completed'}
                def upload_part(self,part,path,offset,size):
                    expected=source.read_bytes()[offset:offset+size]
                    if b''.join(uploader.read_chunk(path,offset,size))!=expected:raise AssertionError('Wrong chunk bytes')
                    return '"etag-'+str(part['partNumber'])+'"'
            with patch.object(uploader,'Client',FakeClient),contextlib.redirect_stdout(io.StringIO()) as output:
                uploader.run(source,'application/octet-stream','fake-credential')
            self.assertEqual(12,calls[0][1]['sizeBytes'])
            self.assertEqual([{'partNumber':i,'eTag':'"etag-'+str(i)+'"'} for i in (1,2,3)],calls[-1][1]['parts'])
            self.assertNotIn('fake-credential',output.getvalue())
            self.assertIn('SUCCESS',output.getvalue())


if __name__=='__main__':unittest.main()
