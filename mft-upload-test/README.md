# Local JWT upload with Bruno

The service flow is: obtain an Entra application token, request an API upload ticket,
then upload directly to S3. API application tokens and S3 signed URLs are different
credentials; never send the Entra Bearer header to S3.

## One-time credential setup

GitHub automation already uses workload identity. Your local Bruno installation needs
its own approved consumer credential. No local client secret has been provisioned by
this PR. Ask IDAM to approve/provide a short-lived development credential for consumer
`0ded1120-7c5c-46ab-b0bb-1811a107f191`, or recommend their certificate-based approach.
A certificate requires a signed client assertion; it cannot be pasted in the secret field.
Do not add a secret resource to Terraform or store credential values in Git.

Open this collection, select the checked-in **development** environment, and add
`entra_client_secret` using Bruno's secret environment-variable facility. Keep that
value out of ordinary saved environment variables. If your Bruno version doesn't
support secret variables, use its approved secret-store integration or update Bruno.
The checked-in token request demonstrates the secret-based flow only; use it after
IDAM has approved that credential type. No browser callback or personal login is used.

## Run the sample

1. Run **Get Entra Application Token**. It stores the access token only as a runtime
   variable, `entra_access_token`. A failed token request clears any previous token.
2. Run **Get Transfer Ticket (Bearer)**. Its body matches the included
   `example-upload.csv` file (20 bytes); don't edit that file without updating both
   `sizeBytes` and its optional base64 MD5. Authentication is Bearer.
3. Run **Upload File To S3** with the included CSV selected as the file body. The URL
   is exactly `{{upload_url}}` with no added `https://`, encoding or query edits.
   Its pre-request script copies every returned signed header, including
   `x-amz-meta-declared-size-bytes`, and uses No Auth.
4. S3 HTTP 200 means the bytes were accepted. Scanning and Slack/pickup are asynchronous;
   verify them separately using the transfer ticket. A ticket response alone is not
   an upload. If the signed URL expires, obtain a new ticket.

To use your own file, set its file name, MIME type and exact byte size in the ticket
body; remove the sample contentMd5 or replace it with that file's base64 MD5. Select
that same file in the S3 request. Run the three requests in order in one collection
session so runtime variables are available. Reacquire expired tokens; client credentials
has no refresh token. Do not export tokens or signed URLs in demo screenshots/logs.

The multipart requests use the same Bearer token but require explicit part upload and
completion steps; don't use the single-file S3 request for a multipart ticket.
