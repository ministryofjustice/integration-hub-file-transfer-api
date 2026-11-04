#!/usr/bin/env python3
"""Split a local file and generate Bruno part requests. No credentials or network calls."""
import argparse
import json
import os
import re
import shutil
from pathlib import Path

COLLECTION = Path(__file__).resolve().parents[1] / 'mft-upload-test'
MIN_PART = 5 * 1024**2
MAX_PART = 5 * 1024**3


def request_text(number, total, size, part_size, name):
    text = '''meta {
  name: Upload Part NUMBER
  type: http
  seq: NUMBER
}

put {
  url: {{part_1_url}}
  body: file
  auth: none
}

body:file {
  file: @file(_multipart-demo/chunks/part-NUMBER_PAD.bin)
}

script:pre-request {
  const number = NUMBER;
  if (Number(bru.getVar("part_size_bytes")) !== PART_SIZE ||
      Number(bru.getVar("multipart_total_parts")) !== TOTAL ||
      Number(bru.getVar("multipart_declared_size")) !== SIZE ||
      bru.getVar("multipart_source_name") !== SOURCE_NAME) {
    throw new Error("The ticket does not match these chunks. Check file details and the API's partSizeBytes, then prepare again.");
  }
  const urls = bru.getVar("multipart_part_urls") || {};
  if (!urls[number]) {
    throw new Error("Run Presign Multipart Part URLs for all parts before uploading this folder.");
  }
  req.setUrl(urls[number]);
  req.deleteHeader("Authorization");
}

script:post-response {
  const etag = res.getHeader("etag");
  if (res.getStatus() !== 200 || !etag) {
    throw new Error("Part upload failed or S3 returned no ETag. Retry this part before completion.");
  }
  const number = NUMBER;
  const parts = (bru.getVar("multipart_uploaded_parts") || []).filter(part => part.partNumber !== number);
  parts.push({partNumber: number, eTag: etag});
  bru.setVar("multipart_uploaded_parts", parts);
}
'''
    values = {'NUMBER_PAD': f'{number:05d}', 'PART_SIZE': str(part_size),
              'TOTAL': str(total), 'SIZE': str(size), 'SOURCE_NAME': json.dumps(name),
              'NUMBER': str(number)}
    return re.sub(r'NUMBER_PAD|PART_SIZE|SOURCE_NAME|TOTAL|SIZE|NUMBER', lambda match: values[match[0]], text)


def prepare(source, part_size, collection=COLLECTION):
    if type(part_size) is not int or not MIN_PART <= part_size <= MAX_PART:
        raise ValueError('Use the API partSizeBytes value, between 5 MiB and 5 GiB')
    source = source.resolve()
    before = source.stat()
    size = before.st_size
    if not source.is_file() or size <= 0:
        raise ValueError('Choose a non-empty regular file')
    total = (size + part_size - 1) // part_size
    if total > 10000:
        raise ValueError('Too many parts; use the part size returned by the API')
    collection = collection.resolve()
    if not (collection / 'bruno.json').is_file():
        raise ValueError('Bruno collection directory not found')
    output = collection / '_multipart-demo'
    # Never overwrite chunks for an upload already in progress.
    output.mkdir(mode=0o700)
    try:
        if shutil.disk_usage(collection).free < size + 16 * 1024**2:
            raise ValueError('Not enough disk space for a second copy of the source file')
        chunks = output / 'chunks'
        chunks.mkdir(mode=0o700)
        (output / 'folder.bru').write_text('meta {\n  name: Prepared Multipart Parts\n  seq: 6\n}\n')
        with source.open('rb') as stream:
            for number in range(1, total + 1):
                length = min(part_size, size - (number - 1) * part_size)
                with (chunks / f'part-{number:05d}.bin').open('xb') as target:
                    os.chmod(target.name, 0o600)
                    remaining = length
                    while remaining:
                        block = stream.read(min(1024**2, remaining))
                        if not block:
                            raise ValueError('The source file ended early')
                        target.write(block)
                        remaining -= len(block)
                (output / f'part-{number:05d}.bru').write_text(request_text(number, total, size, part_size, source.name))
        after = source.stat()
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise ValueError('The source file changed while preparing chunks')
        (output / 'manifest.json').write_text(json.dumps({
            'fileName': source.name, 'sizeBytes': size,
            'partSizeBytes': part_size, 'totalParts': total}, indent=2)+'\n')
        return output, total
    except Exception:
        # Only remove this newly-created directory, never an existing run or source.
        shutil.rmtree(output)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('file', type=Path)
    parser.add_argument('--part-size', type=int, required=True, help='Exact partSizeBytes from the multipart ticket')
    args = parser.parse_args()
    source = args.file.expanduser()
    if not source.is_file() or source.stat().st_size <= 100 * 1024**2:
        parser.error('Choose the actual file larger than 100 MiB used in the multipart ticket')
    try:
        output, count = prepare(source, args.part_size)
    except (OSError, ValueError) as error:
        parser.error(str(error))
    print(f'Prepared {count} parts in {output}')
    print('In Bruno, presign all part URLs, then run Prepared Multipart Parts in order, followed by Complete Multipart Upload.')
    print('Chunks and generated requests are ignored by Git. Remove _multipart-demo after finishing to recover disk space.')


if __name__ == '__main__':
    main()
