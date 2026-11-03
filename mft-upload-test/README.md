# Upload a file with Bruno

Open this folder as a collection in Bruno and select the **development** environment.
The environment file contains the API address and Entra application IDs. Keep your
client secret in Bruno's secret environment variable `entra_client_secret`, not in
this file or a request. Use the secret **value**, not its ID.

The development application is already registered. Add its client secret once in
Bruno, then use the token request whenever you need a fresh access token.

## 1. Get a token

Run **Get Entra Application Token**. It sends the client ID, secret and scope to the
Entra token endpoint. A successful response is HTTP 200; the response script sets
`entra_access_token` for the ticket request. Run this again when the token expires.

## 2. Request an upload ticket

Run **Get Transfer Ticket (Bearer)**. The sample body describes the included
`example-upload.csv`, which is 20 bytes. For your own file, update `fileName`,
`contentType` and `sizeBytes` to match it. On macOS, check the size with:

```sh
wc -c < /path/to/your/file
```

Use `https://` and the plural `/transfer-tickets` path. The request sends the JWT
as Bearer authentication. HTTP 200 returns the upload URL and required headers;
the script passes these to the S3 request. A ticket alone does not upload a file.

## 3. Send the file to S3

Open **Upload File To S3**, select the same file under **Body → File**, then send.
The URL is `{{upload_url}}`; don't add `https://`, edit query parameters or decode it.
Leave authentication as **No Auth**. The script forwards every header returned in
the ticket, including encryption and declared file size. Do not send the Entra token
to S3.

HTTP 200 from S3 confirms the upload was accepted. Scanning and pickup happen
asynchronously; check the Slack notification and download separately. If the upload
URL expires, get another ticket. For multipart uploads, use the multipart requests
and upload each part before completing; this single-file request is not that flow.

## Troubleshooting

- **ECONNREFUSED:** check the API address uses HTTPS.
- **Missing token or 401:** run the token request again and confirm the Bearer variable.
- **403 from the API:** check the consumer's granted role and client mapping.
- **S3 signature error:** get a fresh ticket, use its exact URL and headers, and select
  the matching file. The sample does not require an MD5 header.

Keep the client secret, access tokens and signed URLs out of Git and shared screenshots.

## Multipart upload in Bruno

The helper below splits your local file and creates a folder of Bruno requests.
You can run each part yourself, or run that folder to send all parts in order.
It doesn't call AWS or need a token. Allow enough free disk space for another copy
of the file; the 6 GiB sample needs roughly 6 GiB for its chunks.

1. Get an Entra token, then run **Create Multipart Upload**. The sample body now
   matches `sample_6gb.dat`: 6,442,450,944 bytes and `application/octet-stream`.
   Change the body if you use another file.
2. Copy `multipart.partSizeBytes` from the response. From the repository root, run:

   ```sh
   python3 scripts/prepare-bruno-multipart.py sample_6gb.dat --part-size 68157440
   ```

   `68157440` is only an example (65 MiB). Use the exact value from your ticket.
   The helper writes `_multipart-demo` inside this collection and refuses to
   overwrite an existing preparation.
3. Run **Presign Multipart Part URLs**. Its default range covers all parts in the
   current ticket. For a very large number of parts, request smaller ranges to
   keep the response manageable. The script keeps the URLs already collected.
4. Bruno should show **Prepared Multipart Parts**. Refresh the collection if needed.
   Run that folder in order, or open a part request and send it. Each request already
   selects its chunk file and part number; no file picker is needed. It checks that
   the ticket matches the prepared file and part size, then saves S3's ETag on success.
5. Run **Complete Multipart Upload** after every part has returned 200. It sends the
   captured ETags and blocks completion if any expected part is missing.

Run the generated folder only, not the whole collection: the collection also contains
create and abort requests. Keep the current ticket, URLs and ETags in the same Bruno
collection session. If a part URL expires, presign it again and retry that part.
A new ticket resets the captured ETags, so don't create another upload midway through.

The helper doesn't require Bruno's developer mode or file-system scripts. Generated
requests contain relative file paths and use the collection's runtime URL variables.
The sample file, chunks and generated requests are ignored by Git. Delete
`mft-upload-test/_multipart-demo` after finishing to recover disk space, or before
preparing a different ticket/file. Keep the original file until download verification.

The existing **Upload Multipart Part To S3** request remains available when you want
to choose a chunk and part number manually. Use **Abort Multipart Upload** to cancel
an unfinished ticket. Don't complete a partially uploaded file.

## Run the whole upload from a terminal

The standalone script handles token acquisition, chunk streaming, part uploads and
completion. It doesn't create chunk files and prompts for the client secret without
echoing it:

```sh
python3 scripts/test-multipart-upload.py sample_6gb.dat \
  --content-type application/octet-stream
```

It needs Python 3 with no extra packages and a real file larger than 5 GiB. The script
uses the development API and products-poc consumer. It refreshes tokens as needed,
retries transient part-upload failures and prints the transfer ticket. It doesn't
save tokens, secrets or signed URLs. A failed completion can be ambiguous; inspect
the ticket before starting again. Scanning, Slack delivery and download/checksum
verification still happen through the normal file-transfer flow.
