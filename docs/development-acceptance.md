# Development security and recovery checks

These tests use the deployed development API. They are separate from unit tests and
production sign-off. The recovery suite uploads a real file, so it can trigger scanning
and the normal pickup notification.

## Security checks

From GitHub Actions, run **Development security and recovery acceptance**, choose
`security`, and use `main`. It obtains a fresh application token using the existing
GitHub trust. No client secret is needed. The job prints one result per route/case.

It checks missing, invalid, tampered, Basic and static-token rejection; the valid-token
boundary; and a customer ID outside products-poc's grant. An unknown multipart ticket
with an authorised token should reach the handler and return 404. Basic rejection can
be 401 or 403; missing/invalid Bearer tokens must return 401.

The following additional environment secrets supply real token fixtures:

| Secret | Fixture needed | Expected |
| --- | --- | --- |
| `ENTRA_EXPIRED_TOKEN` | Previously valid, now expired token for this API | 401 |
| `ENTRA_WRONG_AUDIENCE_TOKEN` | Valid Entra token for a different resource | 401 |
| `ENTRA_WRONG_TENANT_TOKEN` | Valid token issued by a different approved test tenant | 401 |
| `ENTRA_USER_TOKEN` | Valid delegated-user token for this API | 403 |
| `ENTRA_MISSING_ROLE_TOKEN` | Application token for this API without FileTransfer.Upload | 403 |
| `ENTRA_UNMAPPED_APP_TOKEN` | Application token with the upload role but no API client mapping | 403 |

Request any extra test identities/grants through IDAM. A missing-role application
must still have an allowed assignment if Entra requires assignment to issue a token;
use an approved non-upload role. Do not weaken the API registration or add user
sign-in just to manufacture a fixture. If a scenario cannot be issued by policy,
record the issuer's denial and the relevant handler unit test as separate evidence,
with reviewer agreement. Changing a JWT payload is a signature test, not a valid
wrong-tenant or missing-role fixture.

Fixtures must be current except for the expired-token case. Store no tokens in files,
reports, PRs or issue comments. Missing fixtures are reported as **missing**, not pass.
The job exits 2 for incomplete evidence and 1 for an actual failure. It is deliberately
not green when part of the acceptance set has not run.

For local execution, enter a fresh token without placing it in shell history:

```sh
read -rs ENTRA_ACCESS_TOKEN
export ENTRA_ACCESS_TOKEN
python3 scripts/acceptance-security.py
unset ENTRA_ACCESS_TOKEN
```

The script is fixed to the development API and never follows redirects with tokens.
No production endpoint override is provided.

## Recovery checks

Run the workflow with `recovery` to create a disposable 6 GiB file and exercise:

- An incomplete S3 part PUT, followed by retry of that part.
- A duplicate PUT, using the latest returned ETag at completion.
- An invalid completion ETag, then successful completion of the full source file.
- A repeated completion: the current contract is **409**, not an idempotent success.
- Abort after uploading a part; presign, completion and another abort then return 409.
- An expired S3 part URL (403), renewal within an active session, then successful
  part upload and abort.

The current main separates the session lifetime from signed URL expiry. The runner
requests a three-second URL, waits for it to expire, gets a fresh URL for the same
active session, uploads a part and aborts. It does not leave that test upload pending.

An actually expired session needs an aged fixture. Set the environment variable
`EXPIRED_TRANSFER_TICKET` to the UUID of a known controlled test session older than
its configured session TTL. Presign/complete/abort must return the explicit expired
409 response, or 404 after TTL deletion. An ordinary inactive-session 409 does not
count. Do not shorten the shared development session lifetime just for a test.
If no aged ticket is supplied, this case remains **missing**. Verify bucket cleanup
separately; DynamoDB deletion alone does not remove S3 multipart parts.

Cross-customer session checks also need `ENTRA_OTHER_CUSTOMER_TOKEN`: a current,
separately mapped upload application's token for a different approved test customer.
The runner first proves it is a valid authorised application with an empty create
request (400), then expects 403 for presign/complete/abort against products-poc's real
session. Do not use an unmapped or missing-role token as a substitute. Configure a
second test consumer/customer only after agreeing its grants and S3 prefix isolation.

Local run with the same token environment setup:

```sh
python3 scripts/acceptance-recovery.py /path/to/sample_6gb.dat --check-expiry
```

The script streams the file in small reads. It uploads the source once, plus two extra
first-part PUTs for retry/abort checks. It requests new part URLs as needed but the API
token can still expire during a slow run; obtain fresh credentials before starting.
It does not automatically restart an upload or abort after an ambiguous completion.
Inspect the reported ticket first. Never use a real customer file for fault testing.

After collection, compare the downloaded file with the source:

```sh
shasum -a 256 /path/to/source /path/to/download
```

The two hashes must match. To update the existing report without making network calls:

```sh
python3 scripts/acceptance-recovery.py /path/to/source --verify-download /path/to/download
```

Until then the JSON report leaves collection/checksum verification **unverified**,
because the runner cannot authenticate to the pickup site or inspect Slack. Keep that evidence and lifecycle verification with the run's revision/time and
tickets. A single script exit code is not the entire acceptance decision.

## Evidence and capacity

Results are written to `security-results.json` or `recovery-results.json` and printed
in the job log. They contain case names, status codes and test ticket IDs, not response
bodies, access tokens, signed URLs or ETags. Don't change the scripts to dump HTTP
objects when diagnosing a failure. Local evidence files are ignored by Git.

The job is manual, main-only and shares the existing smoke workflow's concurrency
lock. It does not generate concurrent customer traffic. Agree a separate load-test
window and cost/volume limit before capacity testing. Start with low concurrency,
measure API errors/throttles/latency and S3 throughput, then raise load gradually.
