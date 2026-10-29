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
