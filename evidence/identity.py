"""Evidence Identity Model V1.1; URI identity belongs to raw bytes only."""
from dataclasses import fields
from .contracts import EvidenceRecord
from .archive_contracts import ArchiveIdentity, identifier, require


def validate_canonical_record(record, meta, normalized):
    require(type(record) is dict and set(record) == {f.name for f in fields(EvidenceRecord)},
            "CANONICAL_RECORD_SCHEMA_INVALID")
    identity = ArchiveIdentity.parse(meta["uri"])
    identifier(record["evidence_id"])
    require(record["canonical_id"] == record["evidence_id"] and
            record["raw_reference_id"] == identity.evidence_id and
            record["evidence_id"] != identity.evidence_id, "CANONICAL_IDENTITY_INVALID")
    predecessor = record["supersedes_evidence_id"]
    require(predecessor is None or (identifier(predecessor) != record["evidence_id"] and
            predecessor != meta["record"]["evidence_id"]), "CANONICAL_PREDECESSOR_INVALID")
    dynamic = {"evidence_id", "canonical_id", "raw_reference_id", "supersedes_evidence_id",
               "normalization_rule", "validation_result", "normalized_data_hash"}
    original = {"canonical_id": None, "raw_reference_id": None, **meta["record"]}
    require({k: v for k, v in record.items() if k not in dynamic} ==
            {k: v for k, v in original.items() if k not in dynamic}, "ARCHIVE_RECORD_MISMATCH")
    require(all(record[k] == normalized[k] for k in
                ("normalization_rule", "validation_result", "normalized_data_hash")),
            "NORMALIZED_RECORD_MISMATCH")
