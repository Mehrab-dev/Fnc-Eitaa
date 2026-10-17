# Login response decoding

The live login reached `auth.sentCode` (App, length 5) but failed with
`Unexpected trailing TL data` after code submission. This does not establish
whether the server accepted the login. No `account.session` was present when
this report was first investigated.

A later structural report identified a complete direct `auth.authorization`
and self `user` ending at byte 132 of 192 (60 bytes left). The public web
worker's no-auth-key `parseResponse` branch calls `fetchObject` once without
checking the final reader offset. The unchanged public entry and worker hashes
were checked again on October 15, 2026. Source anchors are recorded in
`research/protocol/auth_evidence.json`.

Compatibility now follows that single-object read only for direct login
authorizations returned by `auth.signIn`, `auth.checkPassword`, and
`auth.checkPassword2`. A complete nonempty token and a positive self user ID
are required. The suffix remains opaque: it is not identified as padding or
assigned invented schema fields. Its length is available as
`AccountReply.authorization_tail_bytes`. Other RPCs, general TL decoding,
compressed payloads and envelopes retain strict boundary checks.

As before, the candidate credentials are saved atomically for reconnect, then
`users.getUsers(inputUserSelf)` must independently return the same identity
before `is_authorized` becomes true or the CLI reports success. If verification
fails, the session file remains available for a later reconnect without a new
OTP; the CLI does not report successful login. File existence alone does not
establish that login was verified.

The decoder now reports the RPC name, bytes consumed, total bytes, remaining
bytes, and the last six constructor names/offsets/flag masks. It never includes
tokens, phone numbers, OTPs, user IDs, field text, or raw response bytes. These
diagnostics are also bounded for compressed responses and containers.

The CLI still requires explicit local confirmation before requesting a code.
It does not retry a decode failure automatically or accept truncated fields.
On the next user-initiated login attempt, share only the `TLCodecError` line.
Never share the session file or authentication credentials.

Validation includes a synthetic fixture matching the observed offsets and flags
(132-byte authorization plus 60-byte opaque suffix), independent identity
verification, reconnect without another OTP, identity mismatch, strict identity
response boundaries, and rejected truncated/nonself/error candidates. Test
credentials are synthetic; live login with this compatibility change has not
yet been verified.
