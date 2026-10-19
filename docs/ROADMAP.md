# Account SDK roadmap

Target: an independent Python client for real Eitaa user accounts, with an API
experience similar to fncbale. Token sending/public scraping remains a separate
preparatory backend; it does not establish account capability.

## Current milestones

| Step | Deliverable | Status |
| --- | --- | --- |
| 1 | Public protocol research, schema, framing, login/session map | Research baseline complete, 2026-10-15; authenticated behavior unverified |
| 2 | Python TL codec, request/response handling, bounded HTTP transport | Implemented, 2026-10-15; 67 tests and live empty-token error probe passed |
| 3 | Phone/OTP/password login, atomic session storage and reconnect | User-confirmed OTP login and reconnect without OTP; live password/revocation/expiry acceptance remains |
| 4 | get_me, real resolve_peer, dialogs and chat identities | Implemented, 2026-10-17; live dialogs and public username resolution verified; multiple-page/archive live acceptance remains |
| 5 | Text sending, replies, single-message reads and paginated history | Implemented; public history/next page verified; user confirmed self-send, readback, reply linkage and newest-first self history |
| 6 | New/edit/delete events, checkpoints and gap recovery | User confirmed self-chat new/edit/delete and offline-message recovery with explicit --replay-pts 1; ordinary restart replay remains unverified |
| 7 | Edit, delete, forward and copy operations | User confirmed self-chat edit/readback, forward/readback, copy/readback and deletion/readback; copy attribution and other destinations remain |
| 8 | Media upload/download, photos, video, documents, voice, albums | User verified self-document/photo/voice/video round trips and a two-photo album with grouped display; large files, other album types and destinations remain |
| 9 | Full users/groups/channels, counts and permitted member reads | Live self/public/private-channel info, private-channel member list and self owner role verified; groups and multi-page live acceptance remain |
| 10 | Durable SQLite inbox/outbox and operation recovery | Implemented; user verified enqueue/reopen, dispatch/readback, confirmed non-resend and inbox/done persistence; live crash recovery remains |
| 11 | Packaging, examples, bilingual reference and live release acceptance | Static bilingual documentation site implemented; packaging previously checked; remaining live release gates still apply |

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
has been submitted by the development agent. The user confirmed successful OTP
login with an independent identity read, session persistence, and reconnect
without another OTP. Password, revocation and expiry paths have local coverage;
live acceptance of those paths remains outstanding.

The default async context now calls `start()`, which checks the saved session
and, when it is missing or explicitly revoked, performs terminal phone/code/
password login before entering the operation body. `auto_login=False` retains
manual authentication; `connect()` always remains prompt-free. Twenty-four
mocked tests cover startup, reconnection, cancellation, input errors, revocation,
file conflicts and uncertain-send non-retry. Live acceptance of this new startup
flow remains pending; no OTP was requested by the development agent.

A new SDK-created session can authenticate, persist safely, reconnect, and read
the account's identity without another OTP. Verify invalid/expired OTP, password
requirements, revoked sessions and token expiry. Keep account tokens distinct
from Eitaayar tokens and avoid persisting plaintext passwords or OTPs.

## Step 4 acceptance

Implementation, usage and limitations: [ACCOUNT_STEP_4.md](ACCOUNT_STEP_4.md).
Account-scoped peers preserve kind/ID/access hash. Username resolution uses
contacts.resolveUsername; dialogs use messages.getDialogs, with explicit cursors,
folder selection, pinned handling, bounded identity cache and lazy iteration.
Uncached/ambiguous IDs and unaddressable min peers fail instead of guessing.

On 2026-10-17, read-only calls with the existing SDK session returned 11 dialogs,
all 11 cached identities converted to InputPeer, and @eitaa resolved as a channel.
No private titles, messages, identifiers or tokens were recorded as evidence.
The account returned a complete page. Multi-page and archive behavior are tested
locally and still require live acceptance on an account with suitable dialogs.

## Step 5 acceptance

Step 5 implementation and local acceptance commands:
[ACCOUNT_STEP_5.md](ACCOUNT_STEP_5.md). AccountClient now supports plain-text
send/reply, exact single-message lookup and exclusive-offset history. Sends use
one random operation ID, explicit acknowledgement correlation and no automatic
retry. Interrupted or unconfirmed sends expose the operation ID as uncertain.
The development agent verified public-channel history, single-message lookup
and the next page with the existing session. The development agent sent no live
messages. The user subsequently confirmed a self-send, exact readback, a second
message linked as a reply to the first, and newest-first self history preserving
that linkage. Sending to other users, basic groups and channels has not yet
been live-verified; no broader destination coverage is claimed.

## Step 6 acceptance

Implementation, event API, recovery limits and step-by-step live checks:
[ACCOUNT_STEP_6.md](ACCOUNT_STEP_6.md). AccountUpdates polls global differences and
separately watched channels with explicit acknowledgement, atomic checkpoints,
stable event identities and bounded deduplication. Retained gaps use difference
slices; irrecoverable gaps raise UpdateGapError without advancing the cursor.
Delivery is at least once, not a transactional inbox. Unknown common deletion
peers remain None. Live read-only state/global/public-channel difference calls
and checkpoint reload passed on 2026-10-17 with no events observed. Local tests
cover new/edit/delete, raw updates, replay, cancellation, checkpoint conflicts,
handler failures, channel positions/timeouts and FLOOD_WAIT. Live production of
events was subsequently confirmed by the user for self-chat new/edit/delete.
The user's first restart catch-up test produced no event output; its cause is
unresolved. Structural diagnostics are available without acknowledging a batch
via --once --diagnose. A read-only probe recovered the missing test message by
requesting one common pts position earlier with the same date; the saved cursor
was untouched. Explicit --replay-pts 1 now supports this bounded overlap on the
first batch, then normal polling resumes after ack. The user then confirmed
delivery of a message created while the listener was stopped, using this option.
Ordinary restart replay and a subsequent restart proving the acknowledgement
remain unverified; no universal server replay guarantee is claimed.

## Step 7 acceptance

[ACCOUNT_STEP_7.md](ACCOUNT_STEP_7.md) documents edit_message, delete_messages,
forward_message(s), copy_message, receipts and uncertain outcomes. Source IDs
are verified against their peer before delete/forward/copy. Channel deletion
requires explicit shared scope. Copy requests server-side drop_author; there
is no protected-content fallback. Writes are sent once, never automatically
retried or split. Forward success requires one exact random-ID mapping for each
source ID; an incomplete acknowledgement leaves the whole operation uncertain.
Local tests cover TL/HTTP, validation, ownership, permissions, cancellation,
partial acknowledgements and CLI confirmation. The development agent performed
no live mutations. The user confirmed self-chat edit/readback, forward/readback,
copy/readback and local deletion/readback. Copy without author attribution has
not been definitively verified visually. Other permitted destinations remain.

## Step 8 acceptance

[ACCOUNT_STEP_8.md](ACCOUNT_STEP_8.md) documents bounded chunked upload, media
metadata, explicit-path atomic downloads and photo/video/document/voice/album
APIs. Custom Eitaa peer/size upload flags, role endpoints and big-file quota
announcement follow the pinned worker. Albums stage each item before one final
send; incomplete correlations remain uncertain. Uploads and sends never retry
automatically. Read retries for token/file-reference expiry are bounded.
Local tests cover binary TL/HTTP, boundaries, media attributes, album order,
cancellation, partial replies, identity changes and download cleanup/hash.
The development agent performed no live uploads, sends or media downloads.
The user verified self-document, voice and video sends, metadata reads, downloads
and matching hashes; photo send/read/download and correct display; voice/video
playback; and a two-photo album with matching grouped IDs, first-item caption
and correct grouped display. An empty cached photo preview was fixed without
discarding valid sizes. Other album types, large files and destinations remain.
There is no durable partial-transfer resume, automatic transcoding or CDN/DC
migration support in this phase.

## Step 9 acceptance

[ACCOUNT_STEP_9.md](ACCOUNT_STEP_9.md) documents typed full-user and full-chat
profiles, optional counts, member roles, filtered pages and single-member lookup.
User, basic-group and channel/supergroup requests use their own pinned RPC and
InputPeer/InputUser/InputChannel types. Full responses must match the resolved
identity before they populate caches. Forbidden lists and absent counts are
never presented as empty membership or zero. Iteration is bounded, deduplicates
overlap and rejects repeating pages without progress. Server access and flood
errors propagate; only an explicit USER_NOT_PARTICIPANT means absent membership.
Membership mutations are not implemented or advertised in this phase.
Local tests cover pinned binary TL/HTTP, validation, rights, pagination,
cancellation, identity changes and token renewal/revocation. Live read-only full
self-user and public-channel requests passed; private names/about/IDs were not
recorded. The user confirmed a private-channel member list with owner/member
roles. Eitaa's generic self-participant response initially concealed ownership;
the SDK now reads explicit current-account creator/admin flags from the same
channel profile, or reports unknown role. Live owner lookup passed after this
fix. Basic groups, supergroups, multi-page lists and further filters still need
live acceptance on suitable permitted destinations.

## Step 10 acceptance

[ACCOUNT_STEP_10.md](ACCOUNT_STEP_10.md) documents durable text/action/media
outbox jobs, immutable correlation IDs, idempotent enqueue keys, SQL claims and
explicit recovery. Possibly transmitted work becomes uncertain, never pending
automatically. Known failures require an explicit retry and respect flood waits.
Inbox events and their checkpoint commit together; handler processing is at least
once, with stable event IDs and durable done markers. SQL revision checks reject
stale consumers. Legacy JSON checkpoint import is explicit and leaves it intact.
Local tests cover every queued operation, disk/commit failure, cancellation,
file changes, concurrent claims, filtered recovery, binary HTTP and child-process
termination before/after persistence boundaries. No live queue mutation or inbox
capture was performed by the development agent. The user subsequently confirmed
enqueue/reopen persistence, one dispatch and independent readback, refusal to
resend the confirmed job, event capture/reopen and a done marker surviving a
fresh process. Live crash/network recovery on the user's machine remains.
Exactly-once remote effects, automatic uncertain retries, partial media resume,
automatic retention and hardware power-loss testing are not claimed.

## Step 11 documentation

The bilingual static site starts at [index.html](index.html). It includes guides,
CLI examples, searchable source-derived API signatures and dataclass fields,
light/dark themes, RTL/LTR, mobile navigation and an explicit validation matrix.
Run `python tools/build_docs.py` after API or content changes and use `--check`
to detect stale generated data. [SITE.md](SITE.md) explains local preview and
optional GitHub Pages setup. Preparing the site does not publish it or establish
acceptance for the outstanding live release gates.

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
