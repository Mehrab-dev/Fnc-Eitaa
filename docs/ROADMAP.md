# Account SDK roadmap

Target: an independent Python client for real Eitaa user accounts, with an API
experience similar to fncbale. Token sending/public scraping remains a separate
preparatory backend; it does not establish account capability.

## Current milestones

| Step | Deliverable | Status |
| --- | --- | --- |
| 1 | Public protocol research, schema, framing, login/session map | Research baseline complete, 2026-10-15; authenticated behavior unverified |
| 2 | Python TL codec, request/response handling, bounded HTTP transport | Implemented, 2026-10-15; 67 tests and live empty-token error probe passed |
| 3 | Phone/OTP/password login, atomic session storage and reconnect | Experimental implementation; sendCode default-User-Agent rejection fixed and empty-phone validation live-tested; real login/reconnect acceptance pending |
| 4 | get_me, real resolve_peer, dialogs and chat identities | Pending |
| 5 | Text sending, replies, single-message reads and paginated history | Pending |
| 6 | New/edit/delete events, checkpoints and gap recovery | Pending |
| 7 | Edit, delete, forward and copy operations | Pending |
| 8 | Media upload/download, photos, video, documents, voice, albums | Pending |
| 9 | Full users/groups/channels, counts and permitted member operations | Pending |
| 10 | Durable SQLite inbox/outbox and operation recovery | Pending |
| 11 | Packaging, examples, bilingual reference and live release acceptance | Pending |

Every step includes appropriate tests and documentation. Operation identities,
idempotency and uncertain outcomes must be designed before the durable queue is
implemented. A schema entry alone is never a supported-feature claim.

## Step 1

User explanation: [ACCOUNT_STEP_1.md](ACCOUNT_STEP_1.md).
Technical baseline: [PROTOCOL.md](PROTOCOL.md).
Extracted schema, inventory and evidence: `research/protocol/`.

Acceptance: source provenance; deterministic extraction without executing remote
code; verified read-only unauthenticated error framing; documented unknowns.
This is sufficient to begin step 2. Successful authenticated requests still
require acceptance in step 3 and subsequent feature steps.

## Step 2 acceptance

Implementation and limitations: [ACCOUNT_STEP_2.md](ACCOUNT_STEP_2.md).
The recorded packet is reproduced exactly and the live transport returns the
expected 401 INVALID_LOGIN. Codec/HTTP/cancellation/correlation tests pass.
Authenticated success, OTP and token renewal remain step 3 acceptance work.

- Signed/unsigned 32-bit representation, lossless long values, UTF-8 and bytes.
- Length/padding boundaries (253/254), vectors, flags and bare/generic types.
- Decode known constructors using both namespaces and expected return types.
- Bound response size, nesting, vector length and decompressed data.
- Serialize eitaaObject and reproduce the recorded probe exactly.
- Decode direct errors and handle response/container correlation explicitly.
- No login claim, automatic send retry or browser credential extraction.

## Step 3 acceptance

Implementation and local acceptance instructions: [ACCOUNT_STEP_3.md](ACCOUNT_STEP_3.md).
`AccountClient` and `examples.account_login` implement the phone/code/password
flow, minimal JSON session, identity verification, and bounded token renewal.
Both legacy salted hashes and SRP proofs are tested locally. No real phone/OTP
has been submitted by the development agent. This milestone remains experimental
until the checks below pass on the user's own account.

A new SDK-created session can authenticate, persist safely, reconnect, and read
the account's identity without another OTP. Verify invalid/expired OTP, password
requirements, revoked sessions and token expiry. Keep account tokens distinct
from Eitaayar tokens and avoid persisting plaintext passwords or OTPs.

## Later release gates

- Test events across reconnect, replay, duplicate/out-of-order updates and gaps.
- Check permissions and uncertain outcomes for every message/member mutation.
- Verify media chunk boundaries, grouped IDs and resumability where supported.
- Queue states: accepted, in flight, confirmed, uncertain, failed; no blind retry
  of uncertain sends. Crash-test enqueue, transmission, response and commit.
- Clearly label supported and experimental features; require explicit live
  acceptance before advertising any authenticated feature.

## Preparatory token/public layer

Already implemented: send_message/send_file with Eitaayar, public get_chat_info,
get_info/get_message, public polling and a runnable command-line example.
[STEP_1.md](STEP_1.md) describes this earlier preparatory layer; its original
numbering predates the account roadmap above. Token sending is not live-tested.
