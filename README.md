# Integration Hub File Transfer API

This repository contains the Lambda application code, OpenAPI contract, and API request collections for the Integration Hub Managed File Transfer API.

The AWS infrastructure for this API stays in `ministryofjustice/modernisation-platform-environments` under `terraform/environments/integration-hub-api`.

This repository owns deployment of the live Lambda application code. The environments repository now creates only bootstrap Lambda handlers so Terraform can stay infrastructure-only.

Upload operations require **Entra ID OAuth2 client-credentials access tokens**.
API Gateway validates JWT signatures, issuer, audience and time claims. The upload handler
requires an application-only token, the `FileTransfer.Upload` role and an explicit
application-to-client mapping. Basic and legacy static bearer credentials are rejected.

**Deployment is gated:** approved Entra registration details and live acceptance tests
are still required. See the [OAuth2 setup and rollout guide](docs/oauth2.md).
Documentation-page Basic authentication is separate and remains in place.

## Consumer documentation

Read the [complete API guide](docs/api-guide.md) for flow diagrams, every upload operation, request and response examples, HTTP codes, Bruno setup, and failure recovery.

- [Development Swagger UI](https://5yc80qz674.execute-api.eu-west-2.amazonaws.com/docs) (dedicated documentation credentials required)
- [OpenAPI source](openapi.yaml)

## What it does

1. A caller sends `POST /transfer-tickets` to API Gateway with an Entra application access token.
2. API Gateway validates the token using its JWT authorizer.
3. The upload handler checks the validated tenant, audience, application identity and role against its configured client grants.
4. The upload-ticket Lambda reads client upload configuration from DynamoDB.
5. The upload-ticket Lambda verifies that the authenticated caller is allowed to request a ticket for the requested `clientId`.
6. For files at or below the single PUT limit, the Lambda generates a short-lived pre-signed `PUT` URL for the existing Managed File Transfer upload bucket.
7. For larger files, the Lambda initiates an S3 multipart upload, persists the upload session, and returns the first batch of pre-signed part URLs plus follow-up API operations for the remaining parts, completion, and abort.
8. The client uploads directly to S3 and, for multipart flows, completes the upload through the API once all parts have been transferred.
9. After scanning, configured downstream pickup processing copies clean files to private pickup storage and sends a Slack notification containing the pickup path and Transfer web app link. Recipients sign in and need an authorised read grant to collect files; the notification does not contain a presigned download URL.

## Sample request payload

```json
{
  "clientId": "products-poc",
  "fileName": "example-upload.csv",
  "contentType": "text/csv",
  "sizeBytes": 12345,
  "requestedExpirySeconds": 900,
  "contentMd5": "CY9rzUYh03PK3k6DJie09g=="
}
```

## Example single-upload response shape

```json
{
  "transferTicket": "f2e7fd50-f0c5-4f8c-b0ad-f27c0c4d2b61",
  "clientId": "products-poc",
  "upload": {
    "method": "PUT",
    "url": "https://...",
    "headers": {
      "Content-Type": "text/csv",
      "Content-MD5": "CY9rzUYh03PK3k6DJie09g==",
      "x-amz-meta-client-id": "products-poc",
      "x-amz-meta-declared-size-bytes": "12345",
      "x-amz-meta-original-file-name": "example-upload.csv",
      "x-amz-meta-transfer-ticket": "f2e7fd50-f0c5-4f8c-b0ad-f27c0c4d2b61",
      "x-amz-server-side-encryption": "aws:kms",
      "x-amz-server-side-encryption-aws-kms-key-id": "arn:aws:kms:eu-west-2:123456789012:key/..."
    },
    "expiresInSeconds": 900
  },
  "object": {
    "bucket": "integration-hub-unscanned-...",
    "key": "products-poc/uploads/2026/06/09/uuid.csv"
  }
}
```

## Example multipart response shape

```json
{
  "transferTicket": "f2e7fd50-f0c5-4f8c-b0ad-f27c0c4d2b61",
  "clientId": "products-poc",
  "object": {
    "bucket": "integration-hub-unscanned-...",
    "key": "products-poc/uploads/2026/06/09/uuid.csv"
  },
  "multipart": {
    "uploadId": "abc123...",
    "partSizeBytes": 67108864,
    "totalParts": 160,
    "maxParts": 10000,
    "expiresInSeconds": 900,
    "initialParts": [
      {
        "partNumber": 1,
        "method": "PUT",
        "url": "https://...",
        "headers": {},
        "expiresInSeconds": 900
      }
    ],
    "operations": {
      "presignPartsPath": "/transfer-tickets/f2e7fd50-f0c5-4f8c-b0ad-f27c0c4d2b61/parts",
      "completePath": "/transfer-tickets/f2e7fd50-f0c5-4f8c-b0ad-f27c0c4d2b61/complete",
      "abortPath": "/transfer-tickets/f2e7fd50-f0c5-4f8c-b0ad-f27c0c4d2b61"
    }
  }
}
```

## Terraform commands

The development API is owned by the `integration-hub-api/file-transfer-api`
component in `modernisation-platform-environments`. It uploads to
`integration-hub-file-transfer-development-incoming` in the separate MFT account.
The incoming KMS key is resolved using its full cross-account `alias/s3/incoming`
ARN; legacy upload-bucket SSM parameters are no longer used.

Apply in this order:

1. Register `file-transfer-api` in Modernisation Platform and wait for its backend
   and `integration-hub-api-development` workspace to be provisioned.
2. Apply `terraform/environments/integration-hub-file-transfer`, workspace
   `integration-hub-file-transfer-development`, including the incoming S3/KMS grants.
3. Plan and apply `terraform/environments/integration-hub-api/file-transfer-api`,
   workspace `integration-hub-api-development`.
4. Create the GitHub environment `integration-hub-api-file-transfer-api-development`
   in this repository. Restrict deployment branches to `main`.
5. Run `deploy-development` to replace all three bootstrap Lambda packages.
6. Configure Entra identities and client grants using the OAuth2 rollout guide.
   Populate only the separate documentation credential secret through approved handling.
7. Set Bruno's `api_endpoint` to `transfer_ticket_api_endpoint` from the new
   component and verify single and multipart uploads, then confirm processing in MFT.

The old endpoint is not reused. Do not apply or destroy the legacy root stack or
move its state into the new component. The isolated replacement has a new backend
prefix `environments/members/integration-hub-api/file-transfer-api`.

## Lambda deployment

The workflow deploys into account `732169940576` using the dedicated
`integration-hub-api-file-transfer-api-app-deploy` role. Its exact OIDC subject matches the
repository's current default subject configuration:

```text
repo:ministryofjustice/integration-hub-file-transfer-api:environment:integration-hub-api-file-transfer-api-development
```

The role can read/update only the three API Lambda functions (including the retired, deny-only authorizer during cutover). Existing runtime
function names are retained in the new account. Infrastructure creates bootstrap
packages; subsequent application releases come from this repository.

## Authentication configuration

See [Entra setup, token acquisition, testing and deployment](docs/oauth2.md).

## Upload selection

The client always provides `sizeBytes`. The API uses that declared size to:

- reject requests above the configured 100 GB maximum for the client
- return a single pre-signed `PUT` upload for files up to the S3 single-request limit
- return a multipart upload session for files above the single-request limit

The declared size is stored as multipart session state and written into the uploaded object's S3 metadata for traceability.

## Example API call

Obtain an Entra access token as described in [OAuth2 setup](docs/oauth2.md), then send it
as `Authorization: Bearer <access_token>` to `POST /transfer-tickets`. Use the same
API authentication for multipart part URLs, completion and abort. S3 PUTs use only
the returned signed URL and headers.

## Multipart follow-up calls

Request more part URLs:

```bash
curl -X POST "https://<api-endpoint>/transfer-tickets/<transfer-ticket>/parts" \
  -H "authorization: Bearer <access_token>" \
  -H "content-type: application/json" \
  -d '{
    "partNumberStart": 11,
    "partNumberEnd": 20
  }'
```

Complete the multipart upload after collecting the `ETag` response header from each uploaded part:

```bash
curl -X POST "https://<api-endpoint>/transfer-tickets/<transfer-ticket>/complete" \
  -H "authorization: Bearer <access_token>" \
  -H "content-type: application/json" \
  -d '{
    "parts": [
      { "partNumber": 1, "eTag": "\"etag-for-part-1\"" },
      { "partNumber": 2, "eTag": "\"etag-for-part-2\"" }
    ]
  }'
```

Abort a multipart upload if the transfer is abandoned:

```bash
curl -X DELETE "https://<api-endpoint>/transfer-tickets/<transfer-ticket>" \
  -H "authorization: Bearer <access_token>"
```

## OpenAPI

The source contract is documented in this repository's [`openapi.yaml`](openapi.yaml).

## Local tests

Run the Lambda unit tests from this repository with:

```bash
python -m unittest discover -s lambda/request-upload-ticket -p 'test_lambda_function.py'
python -m unittest discover -s lambda/request-authorizer -p 'test_lambda_function.py'
python -m unittest discover -s lambda/request-docs -p 'test_lambda_function.py'
```

After deployment, the same API publishes a protected Swagger UI and the raw OpenAPI document:

- `terraform output transfer_ticket_api_docs_url`
- `terraform output transfer_ticket_openapi_url`
- `terraform output transfer_ticket_api_docs_basic_auth_secret_name`

The docs page and raw OpenAPI URL are protected with browser-friendly HTTP Basic auth backed by Secrets Manager. Bootstrap the live password outside Terraform state with:

```bash
terraform output transfer_ticket_api_docs_basic_auth_secret_name
scripts/bootstrap-api-credentials.sh docs --secret-id <docs-secret-name>
```

Inside Swagger UI, callers should still use the API's own security schemes for actual operations:

- an Entra application access token

## Recommended sharing pattern

The standard pattern for sharing a Swagger contract with internal and external consumers is:

1. Host the human-friendly UI close to the API itself so the contract and runtime stay in sync.
2. Protect the UI with SSO or browser-friendly basic auth if broader internet reach is required.
3. Publish the raw OpenAPI document as a separate protected URL for code generation, Postman imports, and client automation.
4. Treat the OpenAPI file as versioned source alongside the implementation so contract changes are reviewed with code changes.

This stack now follows that pattern by serving `/docs` and `/openapi.yaml` from the same HTTP API, protecting the docs URLs with dedicated basic auth, and keeping the upload operations on their Entra OAuth2 authentication and application authorisation.
