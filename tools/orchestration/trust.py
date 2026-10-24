"""Operator-pinned Stable release, kept outside Development and model sandboxes.

This module consumes an external approval. It does not issue release approvals,
promote candidates, or expose an installation endpoint to the browser.
"""
from pathlib import Path
import hashlib
import json

from .common import Blocked, canonical, manifest


def overlap(a, b):
    a, b = Path(a).resolve(), Path(b).resolve()
    return a.is_relative_to(b) or b.is_relative_to(a)


class TrustedRelease:
    def __init__(self, record, expected_sha256):
        self.record = Path(record)
        if not self.record.is_absolute() or self.record.is_symlink():
            raise Blocked("Stable approval must be an explicit private regular file")
        if not self.record.is_file() or self.record.stat().st_nlink != 1 or self.record.stat().st_size > 2_000_000:
            raise Blocked("Invalid Stable approval")
        if len(expected_sha256) != 64:
            raise Blocked("Stable approval hash must be pinned by the operator")
        raw = self.record.read_bytes()
        if hashlib.sha256(raw).hexdigest() != expected_sha256:
            raise Blocked("Stable approval changed")
        self.sha256 = expected_sha256
        self.data = json.loads(raw)
        fields = {"version", "root", "git_commit", "git_tree", "candidate_sha256", "files"}
        version = self.data.get('version')
        extra = {'provenance'} if version == 2 else {'review_verdict', 'review_receipt_sha256', 'approval_ref'}
        if type(version) is not int or version not in (1, 2) or set(self.data) != fields | extra:
            raise Blocked("Unexpected Stable approval fields")
        if version == 1 and self.data["review_verdict"] != "ACCEPT":
            raise Blocked("Stable requires an externally accepted release")
        if version == 1 and (not self.data["approval_ref"] or len(self.data["review_receipt_sha256"]) != 64):
            raise Blocked("External release approval evidence is required")
        self.root = Path(self.data["root"]).resolve()
        if self.root != Path(__file__).resolve().parents[2] or overlap(self.root, self.record):
            raise Blocked("This process is not running the separately approved Stable release")
        self.provenance = None
        if version == 2:
            from .release_provenance import ReleaseProvenance
            self.provenance = ReleaseProvenance(self)
        self.verify()

    def verify(self):
        if self.provenance:
            self.provenance.read(dict(path=str(self.record), sha256=self.sha256), as_json=False)
        elif hashlib.sha256(self.record.read_bytes()).hexdigest() != self.sha256:
            raise Blocked("Stable approval changed after startup")
        entries = manifest(self.root)
        if any(entry["mode"] == "120000" for entry in entries):
            raise Blocked("Stable cannot load code through symlinks outside its frozen file bytes")
        if entries != self.data["files"] or hashlib.sha256(canonical(entries)).hexdigest() != self.data["candidate_sha256"]:
            raise Blocked("Stable code differs from the externally reviewed release")
        if self.provenance:
            self.provenance.verify(entries)

    @property
    def protected_paths(self):
        return (self.record, *(Path(p) for p in self.provenance.paths)) if self.provenance else (self.record,)

    def permits(self, source, authority):
        self.verify()
        for path in (source, authority):
            code = self.provenance.code_paths if self.provenance else ()
            if overlap(self.root, path) or any(overlap(p, path) for p in (*self.protected_paths, *code)):
                raise Blocked("Stable, Development and authority must be disjoint")
        if overlap(source, authority):
            raise Blocked("Development cannot contain runtime authority")
        return self.binding()

    def binding(self):
        return {"record": str(self.record), "sha256": self.sha256}

    @classmethod
    def bound(cls, value):
        if not isinstance(value, dict) or set(value) != {"record", "sha256"}:
            raise Blocked("Invalid stored Stable identity")
        return cls(value["record"], value["sha256"])
