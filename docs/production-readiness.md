# Production readiness

Development JWT authentication and a real single-file S3 upload have been verified.
That establishes the development path, not production acceptance. Local Bruno clients
still need an IDAM-approved credential; the GitHub consumer uses workload identity.

## This hardening change

Create-ticket requests reject non-string identifiers/metadata and control characters.
JSON numeric fields must be integers: booleans, fractions and numeric strings are
rejected with 400 rather than silently converted. Existing valid integer requests
and expiry clamping are preserved. Multipart part URL requests validate integer
values, list length and range bounds before constructing ranges or signing URLs.
Malformed base64 request bodies return 400.

This does not prove that uploaded bytes match declared size, validate all filename
semantics or checksum formats, or change multipart completion/retry behaviour.

## Work before production activation

| Gate | Required action and evidence | Owner |
| --- | --- | --- |
| Consumer identity | Separate production API/consumer registrations, app-only upload role, explicit client mappings and approved credential lifecycle. Use workload identity or certificates where suitable; review any secret exception. | IDAM and consumer team |
| Infrastructure | Register/onboard production components/accounts, bucket/KMS grants and state. Use actual approved IDs; don't copy DEVL identities or invent account details. Review production plans for replacements and permission scope. | Infrastructure team |
| Security acceptance | Automated valid/expired/wrong-tenant/wrong-audience/tampered token, delegated-user, missing-role, unmapped-client and cross-customer checks across all upload operations. Keep tokens out of reports. | API team |
| Upload integrity and resilience | Single/multipart payload comparison, mismatched declared sizes, checksum behaviour, interrupted transfers, retry/timeout ambiguity, completion/abort races, expiry and orphan cleanup. Agree idempotency semantics before adding retries that can duplicate transfers. | API and file-transfer teams |
| Operations | Configure approved alarm destinations for API errors/latency/throttling, Lambda failures and downstream queue age/DLQ. Exercise alerts and document runbooks, ownership and retention. | Infrastructure and operations |
| Full processing | Trace a harmless transfer through upload, malware scanning, clean routing, pickup notification, authorised collection and retention. | File-transfer team |
| Encryption/connectivity | Production evidence for HTTPS-only access, KMS at rest, least-privilege cross-account access and denied unauthorised access. | Security and infrastructure |
| Release acceptance | Coordinated deployment/rollback, harmless production smoke with zero critical errors, evidence recorded against revisions/request IDs and Buyer SecOps/Infra sign-off. | Buyer leads and service team |

Do not mark a gate complete from unit tests alone. The existing GitHub smoke has an
upload-only mode and a limited negative-test mode; neither replaces this full suite.
Do not enable production using the development products-poc identity.

## Suggested sequence

1. Merge and deploy input hardening to development; rerun JWT single/multipart tests.
2. Resolve transfer integrity and retry behaviour with focused implementation PRs and
   meaningful tests, then implement the full security/processing acceptance suite.
3. Agree production consumer identity, account onboarding and operational destinations.
4. Prepare production infrastructure and deployment changes from those actual inputs.
5. Review plans, deploy in an agreed window, run production acceptance and obtain sign-off.
