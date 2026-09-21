# WhatsApp v9.9 release gate

## Current state

The clean-room WhatsApp v9.9 implementation is code-complete for the planned Postmaster surface:

- explicit QR pairing and encrypted auth persistence;
- authenticated reconnect over the current WhatsApp Web companion transport;
- direct multi-device text send/receive;
- encrypted direct media upload/send and inbound media download;
- canonical Postmaster Stored File handoff for media;
- participating group listing;
- Signal SenderKey v3 group text send/receive;
- group media send and inbound group media Stored File handoff;
- remote receipt observation;
- asymmetric read-receipt policy: local reads do not emit receipts, explicit reply actions may;
- local WebGUI pairing QR rendering without remote QR services.

Automatic CI also performs live unauthenticated/current-protocol probes through ClientFinish and
encrypted pair-device traffic without pairing an account.

Implementation completeness is **not** the same as controlled-account interoperability acceptance.
The stable v9.9 release remains gated until the manual controlled-account workflow passes.

## No personal WhatsApp account is required

Do not use a maintainer's personal WhatsApp account for release acceptance.

Use dedicated test identities. The full gate is designed for:

1. one dedicated WhatsApp account that Postmaster pairs as the companion under test;
2. one separate controlled peer WhatsApp account;
3. one test group containing both controlled accounts.

The peer can be operated manually or by a separate controlled test device. The release gate needs a
distinct peer because it verifies remote multi-device fan-out, remote receipts and inbound direct/group
decryption rather than only self-device synchronization.

WhatsApp Web/linked-device operation requires an active WhatsApp account on a primary phone. There is
no repository fallback that bypasses this requirement, and the release process must not substitute
unofficial number-verification or disposable-account services.

## GitHub Environment

Create a protected GitHub Actions environment named:

`whatsapp-v990-controlled`

Configure these environment secrets:

- `POSTMASTER_WA_AUTH_DB_B64`: Base64 of a previously paired controlled auth SQLite database;
- `POSTMASTER_WA_AUTH_KEY_B64`: Base64 of the corresponding 32-byte auth-store key;
- `POSTMASTER_WA_TEST_JID`: the separate controlled peer JID;
- `POSTMASTER_WA_TEST_GROUP_JID`: a controlled `g.us` group containing the test identities.

The workflow never prints those values. Its evidence artifact contains only counts and booleans, never
JIDs, QR refs, auth bytes, private keys, session material or media payloads.

## Release-qualifying workflow

Run:

`.github/workflows/whatsapp-v990-controlled-interop.yml`

For a release-qualifying run, leave all full-profile inputs enabled:

- direct inbound echo required;
- group inbound echo required;
- inbound group media required;
- remote receipt required;
- media send enabled.

The controlled peer follows the in-band probe messages. In particular, it echoes the direct/group
tokens and sends one small media attachment to the controlled group with the requested token in its
caption.

The run is release-qualifying only when the non-secret evidence ends with all of these true:

- `protocol_interop_observed`;
- `signal_multidevice_observed`;
- `group_sender_key_interop_observed`;
- `media_interop_observed`;
- `receipt_policy_observed`;
- `controlled_account_interop_observed`.

A diagnostic run with any full-profile requirement disabled is useful for debugging but must not
qualify a stable release.

## Promotion sequence

Before the controlled-account gate passes:

- keep `protocol_interop_verified = false`;
- keep `signal_multidevice_verified = false`;
- keep `controlled_account_interop_verified = false`;
- keep the project VERSION on the current stable release;
- do not create the v9.9.0 stable tag/release.

After a reviewed full-profile pass:

1. set the three acceptance flags to true;
2. run the complete repository regression suite again;
3. update VERSION/CHANGELOG/release metadata to v9.9.0;
4. verify the public single-YAML bootstrap and dependency install from a clean environment;
5. merge the release branch;
6. create the v9.9.0 tag and stable GitHub release.

This separation allows development and CI hardening to reach release-candidate quality without
requiring any maintainer's personal WhatsApp identity.
