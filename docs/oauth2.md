# Entra application authentication

This change replaces Basic and static bearer authentication on all upload operations.
It does not make the production service accepted under D07 on its own. Do not merge
or deploy until the infrastructure configuration and acceptance prerequisites below
are ready. The main-branch application workflow deploys automatically to development.

## Registration checklist for the Entra owner

1. Register a single-tenant file-transfer API and a separate `products-poc` consumer.
2. Configure the API to receive v2 access tokens (`requestedAccessTokenVersion: 2`).
3. Add the optional **access-token** claim `idtyp` so application tokens contain `app`.
4. Expose the app role `FileTransfer.Upload` with `allowedMemberTypes: ["Application"]`.
   Require assignment, grant the role to the consumer and provide admin consent.
5. Provide the tenant UUID, API application UUID (v2 `aud`), API Application ID URI,
   and consumer application UUID (`azp`). Do not send credentials in tickets or chat.
6. Choose an approved consumer credential. Prefer a certificate or workload federation
   where supported; any client secret must be stored securely, rotated and expire.

No browser callback or user sign-in is needed for this machine-to-machine flow.
Do not use the Microsoft Graph audience, a common/multi-tenant issuer, or a user token.

## Configuration and trust boundary

The infrastructure component configures the single-tenant issuer and API audience on
all four API Gateway JWT routes. It passes the approved tenant, API audience and
application-to-client mapping to the upload Lambda. The first consumer mapping is
`<products-poc application UUID>: ["products-poc"]`; wildcard grants are rejected.

API Gateway validates the JWT cryptographically. The handler never decodes a token
from a caller-supplied header or accepts the old Lambda-authorizer context. It trusts
only Gateway's `requestContext.authorizer.jwt.claims`, then checks `iss`, `tid`, `aud`,
`ver=2.0`, `idtyp=app`, absence of `scp`, exact `FileTransfer.Upload` role and mapped `azp`.
IAM permission to invoke the Lambda directly is consequently privileged and must stay
restricted. No function URL should be exposed.

Entra client-credentials tokens use application `roles`, not delegated `scp` scopes.
API Gateway route scopes are therefore not configured; the handler enforces the role.
API and consumer registrations must be different to keep ID-token and API audiences distinct.

## Obtain and use a token

Send a form-encoded POST to:

```text
https://login.microsoftonline.com/<tenant-id>/oauth2/v2.0/token
```

For a secret-based development consumer, the form fields are:

```text
grant_type=client_credentials
client_id=<consumer-application-id>
client_secret=<securely-provided-secret>
scope=<API-Application-ID-URI>/.default
```

Use the actual API URI supplied by the Entra owner (often `api://<API-application-id>`).
The response contains `access_token`, `token_type` and `expires_in`. Cache the token
until shortly before expiry, then acquire another. Client credentials does not use a
refresh token. Never log token responses or put secrets into a committed collection.

In Bruno use OAuth2 Client Credentials with those values, or acquire the token using
your approved client and set the secret variable `entra_access_token`. The checked-in
upload requests use that variable. S3 requests stay No Auth with the returned headers.
Swagger's Authorize accepts the access token itself; it does not acquire tokens.

Missing/invalid/expired JWTs receive Gateway 401; authenticated applications without
permission receive 403. On 401 refresh once if expiry is the cause. Do not keep refreshing
on 403: check consent, application role and the API's application-to-client mapping.
Removing a mapping prevents handler access on subsequent requests. Tokens already issued
can otherwise remain valid until expiry; disabling the consumer alone is not immediate
revocation of every cached token.

## Deployment order

1. Review both PRs and complete real per-environment Entra configuration. Terraform
   preconditions block deployment without valid IDs and explicit client mappings.
2. Schedule a development cutover. There are no Basic-auth consumers to migrate.
3. Deploy the new app first. Its legacy authorizer denies everything and the upload
   handler rejects old authorizer context. This creates a deliberate deny-all interval.
4. Apply the matching infrastructure: JWT authorizer/routes and handler environment.
   Terraform also replaces the old authorizer with a deny-only bootstrap and removes
   its credential-read and Gateway-invoke permissions. Stored legacy auth resources
   remain for separately reviewed cleanup; there is no authentication fallback.
5. Run acceptance tests below, then update consumers and approve promotion.

Do not deploy the old application against the new routes. A rollback must be coordinated;
prefer a deny-all outage over unintentionally restoring obsolete authentication.
Documentation routes retain their dedicated Basic authentication, independent of uploads.

## Automated and live acceptance

Unit tests exercise role/tenant/audience/app-type checks, reject legacy context and
credentials, verify unmapped and cross-client denial and preserve upload behaviour.
They do **not** claim to verify AWS's JWT signature validation.

Run `python3 scripts/smoke-oauth2.py` against development with tokens from the approved
registration (see the script's environment requirements). It tests each operation's
authentication boundary without creating an upload: authorised create with an empty
payload returns 400, and authorised follow-up against a random unknown ticket returns
404. Invalid credentials must be rejected before handler validation.

For complete acceptance additionally test real single and multipart uploads, verify
payload checksums after collection, prove KMS encryption and HTTPS-only bucket access,
and check clean-file scanning/routing. Record time, revision, environment, request IDs,
expected/actual HTTP codes and pass/fail; never retain raw tokens. Test wrong-tenant,
wrong-audience, expired, tampered, delegated-user, missing-role and unmapped-app tokens.
A deliberately tampered signature is not a substitute for all these fixtures.

D07 completion still requires production deployment, approved harmless production
connectivity/payload/encryption tests, a smoke run with zero critical errors, configured
operational alerts and explicit Buyer SecOps/Infra sign-off. Existing upload reliability
and strict-validation limitations in the consumer guide remain separate work.

## References

- [Microsoft client credentials](https://learn.microsoft.com/en-us/entra/identity-platform/v2-oauth2-client-creds-grant-flow)
- [Microsoft access-token claims](https://learn.microsoft.com/en-us/entra/identity-platform/access-token-claims-reference)
- [API Gateway JWT validation](https://docs.aws.amazon.com/apigateway/latest/developerguide/http-api-jwt-authorizer.html)

## GitHub automated development smoke test

The manual **Test Entra development authentication** workflow runs only from `main`
in the `file-transfer-api-entra-development` GitHub environment. That environment
allows only `main`, with administrator bypass disabled. Development smoke tests
do not require a separate environment reviewer approval. It is deliberately unavailable to PR branches.

The development consumer trusts issuer `https://token.actions.githubusercontent.com`,
audience `api://AzureADTokenExchange`, and subject
`repo:ministryofjustice/integration-hub-file-transfer-api:environment:file-transfer-api-entra-development`.
Branch restriction is enforced by GitHub, not by the environment-based subject.

After IDAM applies the trust, set environment variables `ENTRA_TENANT_ID`,
`ENTRA_API_CLIENT_ID` and `ENTRA_CONSUMER_CLIENT_ID` from the registration outputs.
These are identifiers, not secrets. Deploy the matching OAuth2 API configuration
and consumer mapping before running the workflow; the existing Basic-auth deployment
will not pass. No client secret or Azure subscription login is required.

The test exchanges GitHub's identity token directly with Entra, checks rejection of
missing/Basic/invalid tokens, checks authenticated request validation, then creates a
`products-poc` upload ticket and uploads a harmless text file. This can trigger the
normal scan and Slack notification flow. It does not check scan completion, download,
multipart transfer or all negative-token fixtures; retain the full acceptance suite
above. Tokens, assertions and presigned URLs are not logged or saved as artifacts.


The development API uses the tenant-qualified Application ID URI
`api://0bb413d7-160d-4839-868a-f3d46537f6af/integration-hub-file-transfer-api`.
The GitHub smoke test derives this from `ENTRA_TENANT_ID` and requests that URI
with `/.default`. This must be deployed through IDAM PR #968 before the updated
test runs. The API Gateway JWT audience and `ENTRA_API_CLIENT_ID` remain the API
application client ID; do not change them to the URI. Manual clients should use
the IDAM `token_scope` output rather than constructing it from the API client ID.

For a JWT upload demonstration, run the manual workflow with `upload_only=true`.
It obtains a fresh token, creates a products-poc upload ticket and sends the sample
file to S3, without negative authentication checks. The default remains the full
smoke test. Both modes use the same main-only development identity.
