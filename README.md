# Integration Hub File Transfer API

This repository contains the Lambda application code, OpenAPI contract, and API request collections for the Integration Hub Managed File Transfer API.

The AWS infrastructure for this API stays in `ministryofjustice/modernisation-platform-environments` under `terraform/environments/integration-hub-api`.

This repository owns deployment of the live Lambda application code. The environments repository now creates only bootstrap Lambda handlers so Terraform can stay infrastructure-only.

It now protects the API with:

1. Basic authentication for user-driven HTTPS uploads using Secrets Manager-held credentials.
2. Bearer token authentication for system-to-system API integrations using Secrets Manager-held tokens.
3. Role-based authorisation that maps callers to one or more permitted `clientId` values.

## What it does

1. A caller sends `POST /transfer-tickets` to API Gateway with either a Basic auth header or a Bearer token.
2. API Gateway invokes a Lambda request authorizer.
3. The authorizer validates the caller against Secrets Manager credentials or bearer tokens and resolves the caller's role mapping from DynamoDB.
4. The upload-ticket Lambda reads client upload configuration from DynamoDB.
5. The upload-ticket Lambda verifies that the authenticated caller is allowed to request a ticket for the requested `clientId`.
6. For files at or below the single PUT limit, the Lambda generates a short-lived pre-signed `PUT` URL for the existing Managed File Transfer upload bucket.
7. For larger files, the Lambda initiates an S3 multipart upload, persists the upload session, and returns the first batch of pre-signed part URLs plus follow-up API operations for the remaining parts, completion, and abort.
8. The client uploads directly to S3 and, for multipart flows, completes the upload through the API once all parts have been transferred.
9. When the file reaches the Managed File Transfer `clean` bucket, the downstream notifier publishes a client-facing SNS event containing the `clientId`, `transferTicket`, file details, and a presigned download URL.

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
6. Populate the new account's user/bearer/docs secrets from the Terraform outputs
   using approved secret handling. The `replace-me` values are rejected by the app.
7. Set Bruno's `api_endpoint` to `transfer_ticket_api_endpoint` from the new
   component and verify single and multipart uploads, then confirm processing in MFT.

The old endpoint is not reused. Do not apply or destroy the legacy root stack or
move its state into the new component. The isolated replacement has a new backend
prefix `environments/members/integration-hub-api/file-transfer-api`.

## Lambda deployment

The workflow deploys into account `732169940576` using the dedicated
`integration-hub-api-platform-app-deploy` role. Its exact OIDC subject matches the
repository's current default subject configuration:

```text
repo:ministryofjustice/integration-hub-file-transfer-api:environment:integration-hub-api-file-transfer-api-development
```

The role can read/update only the three API Lambda functions. Existing runtime
function names are retained in the new account. Infrastructure creates bootstrap
packages; subsequent application releases come from this repository.

## Authentication configuration

`application_variables.json` now supports an `auth_configuration` block per environment:

```json
{
  "auth_configuration": {
    "roles": {
      "products-poc-upload": {
        "allowed_client_ids": ["products-poc"]
      }
    },
    "users": {
      "products-poc-user": {
        "enabled": true,
        "role_name": "products-poc-upload"
      }
    },
    "system_principals": {
      "products-poc-api": {
        "enabled": true,
        "role_name": "products-poc-upload"
      }
    }
  }
}
```

After apply, retrieve the created secret names from the Terraform outputs:

```bash
terraform output user_auth_secret_names
terraform output system_auth_secret_names
```

Terraform creates the secret containers, but the live secret values are managed operationally in AWS Secrets Manager and ignored by Terraform after creation. The authorizer resolves the current secret value from Secrets Manager at request time using the stored secret name.

For repeatable credential bootstrapping outside Terraform state, use:

```bash
scripts/bootstrap-api-credentials.sh user --secret-id <user-secret-name>
scripts/bootstrap-api-credentials.sh system --secret-id <system-secret-name>
```

The script generates the live credential locally, writes it to Secrets Manager, and prints only the one-time handover value to stdout.

Populate a user secret with JSON in this shape:

```json
{
  "username": "products-poc-user",
  "password": "replace-with-password",
  "roleName": "products-poc-upload"
}
```

Populate a system principal secret with JSON in this shape:

```json
{
  "tokenId": "products-poc-api",
  "bearerToken": "replace-with-token",
  "roleName": "products-poc-upload"
}
```

The bearer token supplied to the API is:

```text
<tokenId>.<bearerToken>
```

Example bootstrap flow for a user secret:

```bash
terraform output user_auth_secret_names
scripts/bootstrap-api-credentials.sh user --secret-id integration-hub-api-platform-development-user-products-poc-user
```

Example bootstrap flow for a system principal:

```bash
terraform output system_auth_secret_names
scripts/bootstrap-api-credentials.sh system --secret-id integration-hub-api-platform-development-system-products-poc-api
```

## Upload selection

The client always provides `sizeBytes`. The API uses that declared size to:

- reject requests above the configured 100 GB maximum for the client
- return a single pre-signed `PUT` upload for files up to the S3 single-request limit
- return a multipart upload session for files above the single-request limit

The declared size is stored as multipart session state and written into the uploaded object's S3 metadata for traceability.

## Example API call with Basic auth

Replace `<api-endpoint>` with the `transfer_ticket_api_endpoint` Terraform output after apply.

```bash
curl -X POST "https://<api-endpoint>/transfer-tickets" \
  -u "<username>:<password>" \
  -H "content-type: application/json" \
  -d '{
    "clientId": "products-poc",
    "fileName": "example-upload.csv",
    "contentType": "text/csv",
    "sizeBytes": 12345,
    "requestedExpirySeconds": 900,
    "contentMd5": "CY9rzUYh03PK3k6DJie09g=="
  }'
```

## Example API call with Bearer auth

```bash
curl -X POST "https://<api-endpoint>/transfer-tickets" \
  -H "authorization: Bearer <tokenId>.<token>" \
  -H "content-type: application/json" \
  -d '{
    "clientId": "products-poc",
    "fileName": "example-upload.csv",
    "contentType": "text/csv",
    "sizeBytes": 12345,
    "requestedExpirySeconds": 900,
    "contentMd5": "CY9rzUYh03PK3k6DJie09g=="
  }'
```

## Multipart follow-up calls

Request more part URLs:

```bash
curl -X POST "https://<api-endpoint>/transfer-tickets/<transfer-ticket>/parts" \
  -H "authorization: Bearer <tokenId>.<token>" \
  -H "content-type: application/json" \
  -d '{
    "partNumberStart": 11,
    "partNumberEnd": 20
  }'
```

Complete the multipart upload after collecting the `ETag` response header from each uploaded part:

```bash
curl -X POST "https://<api-endpoint>/transfer-tickets/<transfer-ticket>/complete" \
  -H "authorization: Bearer <tokenId>.<token>" \
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
  -H "authorization: Bearer <tokenId>.<token>"
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

- Basic auth credentials backed by Secrets Manager, or
- a Bearer token in the form `<tokenId>.<bearerToken>`

## Recommended sharing pattern

The standard pattern for sharing a Swagger contract with internal and external consumers is:

1. Host the human-friendly UI close to the API itself so the contract and runtime stay in sync.
2. Protect the UI with SSO or browser-friendly basic auth if broader internet reach is required.
3. Publish the raw OpenAPI document as a separate protected URL for code generation, Postman imports, and client automation.
4. Treat the OpenAPI file as versioned source alongside the implementation so contract changes are reviewed with code changes.

This stack now follows that pattern by serving `/docs` and `/openapi.yaml` from the same HTTP API, protecting the docs URLs with dedicated basic auth, and keeping the upload operations on their existing Basic and Bearer authentication controls.
