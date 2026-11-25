"""Test-only pinned reference audit for a complete isolated v2 generation.

The receipt binds the observed source/ledger/generation to exact model bytes.
It is not persisted, signed, or an authorization to register/enable traffic.
No changes to the v1 pipeline, existing generation registry or collections.
"""
from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Callable

from .auto_ingestion_service import PROTECTED_QA_KBS
from .hierarchy_manifest_store import HierarchyLedger
from .hierarchy_vector_fidelity import (
    MIN_COSINE, VectorFidelityReport, verify_shadow_vector_fidelity,
)
from .pinned_e5 import PinnedE5Model
from .qdrant_indexing import IndexIdentity


@dataclass(frozen=True)
class PinnedFidelityReceipt:
    source_sha256: str
    records_sha256: str
    artifact_pin_sha256: str
    report: VectorFidelityReport
    query_enabled: bool = False
    authorized_to_publish: bool = False


def verify_pinned_shadow(client, identity: IndexIdentity, ledger: HierarchyLedger,
                         processing_token: str, model: PinnedE5Model, *,
                         lease_guard: Callable[[], bool],
                         batch_size: int = 16) -> PinnedFidelityReceipt:
    if (os.environ.get("DOCMIND_ENABLE_PINNED_SHADOW_AUDIT") != "YES_FOR_TESTING" or
            os.environ.get("NODE_ENV", "").strip().lower() == "production"):
        raise RuntimeError("Pinned shadow audit is disabled")
    if not isinstance(identity, IndexIdentity):
        raise TypeError("MySQL-derived identity required")
    if identity.knowledge_base_id in PROTECTED_QA_KBS:
        raise RuntimeError("Protected QA knowledge base is excluded")
    if type(model) is not PinnedE5Model:
        raise TypeError("Checksum-pinned E5 reference model required")
    if not callable(lease_guard) or lease_guard() is not True:
        raise RuntimeError("Fresh processing lease required")
    pin_digest = model.pin.digest
    report = verify_shadow_vector_fidelity(
        client, identity, ledger, processing_token, model,
        lease_guard=lease_guard, batch_size=batch_size, minimum_cosine=MIN_COSINE)
    if model.pin.digest != pin_digest or lease_guard() is not True:
        raise RuntimeError("Artifact identity or processing lease changed")
    return PinnedFidelityReceipt(
        ledger.manifest.source_sha256, ledger.records_sha256, pin_digest, report)
