# Test authentication and upload recovery

Use these checks before a release to catch problems that a successful upload won't
show. Both suites run against development. Recovery uploads a 6 GiB test file, which
will go through scanning and may produce a Slack pickup message.

## Run from GitHub

Open **Actions → Development security and recovery acceptance → Run workflow**.
Choose `main`, then select `security` or `recovery`.

The workflow gets a fresh Entra token through GitHub's existing identity setup.
You don't need a client secret. Runs share a queue with the regular smoke test, so
only one of them runs at a time.

Results appear in the job log and summary. A missing test token or an unchecked
result is shown as incomplete, rather than passed:

| Exit code | Meaning |
| --- | --- |
| 0 | Every check in the report passed |
| 1 | A check failed |
| 2 | Some checks still need tokens or manual verification |

## Authentication checks

The security suite checks all four upload routes with missing, invalid, tampered and
legacy credentials. It also checks an authorised token and an attempt to upload for
a customer outside its grant.

Missing or invalid Bearer tokens should return 401. Old Basic credentials may return
401 or 403. An authorised request with an unknown multipart ticket should reach the
handler and return 404.

For the remaining cases, add real test tokens as secrets in the
`file-transfer-api-entra-development` GitHub environment:

| Secret | Token to use | Expected response |
| --- | --- | --- |
| `ENTRA_EXPIRED_TOKEN` | A token for this API that has expired | 401 |
| `ENTRA_WRONG_AUDIENCE_TOKEN` | A token for another API | 401 |
| `ENTRA_WRONG_TENANT_TOKEN` | A token from another approved test tenant | 401 |
| `ENTRA_USER_TOKEN` | A delegated user token for this API | 403 |
| `ENTRA_MISSING_ROLE_TOKEN` | An application token without `FileTransfer.Upload` | 403 |
| `ENTRA_UNMAPPED_APP_TOKEN` | An application token with the upload role, but no customer mapping | 403 |

Keep the tokens current, apart from the one used to test expiry. Some cases need
additional test applications or grants from IDAM. For example, Entra may require
an assignment before it will issue a token to the application used for the missing-role
check. An approved non-upload role can provide that assignment.

If Entra policy prevents a particular token from being issued, record that result
and agree with the reviewer how to cover the handler check. Don't enable user sign-in
or weaken the registration just to obtain a test token. Editing a JWT tests its
signature; it doesn't replace a genuine wrong-tenant or missing-role token.

## Multipart recovery checks

The recovery suite uploads a test file and checks:

- Retry after closing a part upload before it finishes.
- Uploading a part again and completing with its latest ETag.
- Correcting a rejected ETag and completing the transfer.
- Calling completion a second time; the current response is 409.
- Aborting a partially uploaded file, then checking that further operations return 409.
- Letting a part URL expire, requesting another and continuing the same session.

URL expiry and session expiry are different. The URL test uses a three-second URL
and aborts its test upload afterwards. It doesn't change the shared session lifetime.

To check an expired session, set the GitHub environment variable
`EXPIRED_TRANSFER_TICKET` to a known test ticket older than the session TTL. The
runner expects the explicit expired-session 409 response, or 404 once the record
has been deleted. A 409 caused only by an already completed or aborted session
doesn't count as an expiry check. Without an aged ticket, this check stays incomplete.

For access checks against another customer's session, add
`ENTRA_OTHER_CUSTOMER_TOKEN`. It must belong to a separate, authorised application
mapped to a different test customer. The runner first checks that this application
can reach the API, then expects 403 when it tries to presign, complete or abort the
products-poc session. An unmapped application isn't a substitute for this test.

The script uploads the source once, plus two extra first-part uploads for retry and
abort checks. It streams from disk and requests new part URLs as needed. GitHub can
also obtain a fresh API token during a long run. Local runs need a current token.
If completion times out, inspect the reported ticket before restarting; the file
may already have completed.

## Run locally

Use a fresh token from Bruno. Enter it at the hidden prompt without putting it in
shell history:

```sh
read -rs ENTRA_ACCESS_TOKEN
export ENTRA_ACCESS_TOKEN
python3 scripts/acceptance-security.py
python3 scripts/acceptance-recovery.py /path/to/sample_6gb.dat --check-expiry
unset ENTRA_ACCESS_TOKEN
```

The recovery file must really be larger than 5 GiB. Use a disposable file rather
than customer data. Both scripts use the development API address.

## Check the downloaded file

Collect the completed file through the normal pickup flow, then compare it with
the source:

```sh
shasum -a 256 /path/to/source /path/to/download
```

The hashes should match. To record the comparison in the existing recovery report:

```sh
python3 scripts/acceptance-recovery.py /path/to/source --verify-download /path/to/download
```

This command only reads the two local files. Until then, the report shows the
checksum check as unverified. Slack delivery and S3 cleanup also need separate
checks; the runner can't infer them from a successful completion response.

## Keep the results

The scripts write `security-results.json` and `recovery-results.json`. Reports include
the revision, time, case names, status codes and test ticket IDs. They leave out
response bodies, tokens, signed URLs and ETags. Local reports are ignored by Git.
Keep the reports with the download and cleanup results for the release being tested.

These suites aren't a load test. Agree a separate development test window and data
volume before increasing concurrency. Start small and measure API latency, errors,
throttling and S3 throughput as the load increases.
