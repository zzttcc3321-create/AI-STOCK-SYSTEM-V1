# Evidence Pipeline Foundation

The `evidence` package is an offline, in-memory foundation. It performs no
network or database I/O and deliberately exposes no update or delete operation.

## Contracts and integrity

`EvidenceRecord` is frozen and contains the twelve frozen metadata fields plus
SHA-256 hashes for the raw response, normalized data, and analysis output. A
correction is a new record whose `supersedes_evidence_id` points at the retained
old record. Request parameters are recursively copied into read-only mappings;
token, password, cookie, session, and API-key keys are discarded rather than
masked.

`EvidenceBuilder` accepts provider and normalizer contracts without changing
them. It converts request timestamps to ISO-8601 UTC+8, enforces the raw archive
path hierarchy, and emits a collection-task-linked `EvidenceMissingEvent` when
construction fails. Its optional `raw_response_hash` input permits the fetch
layer's digest to be retained; otherwise it hashes the provider payload.

## Confirmation, tracing, and review

Confirmation changes are separate append-only ledger events. Only the three
frozen transitions are accepted, and correcting a confirmed item requires a
reason. Analysis output cannot be built unless it is confirmed.

`EvidenceTrace` performs read-only report lookup and fails closed if a required
link is absent or evidence is rejected. `replay_review` reads raw snapshots via
an injected offline reader, verifies hashes, reruns injected normalization and
calculation functions, and returns a new review record. It never edits evidence
or conclusions. `evidence_index` emits only the five fields used by a standard
analysis data packet.

Database persistence, operational notifications, and severity classification
are intentionally deferred to later phases.
