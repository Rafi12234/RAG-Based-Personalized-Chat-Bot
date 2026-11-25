"""Encrypted immutable live-pilot bundle, readable AFTER the processing lease.

DB-selected identity and candidate digests are the authenticated AAD.
Raw processing token exists only encrypted at rest (also existing scoped
Qdrant payload); never stored in SQL or returned to the browser.
"""
from dataclasses import asdict
from hashlib import sha256
import json
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from .hierarchical_index import _canonical
from .hierarchy_manifest_store import _decode_ledger, validate_ledger, MAX_PLAINTEXT_BYTES, SAFE_ID, HEX_SHA
from .shadow_reader import _scope, _digest

MAGIC=b"DOCMIND-ADVANCED-BUNDLE-V1\0"
MAX_BYTES=MAX_PLAINTEXT_BYTES+8192


def _address(identity,generation,source,records,pin,token_hash):
    _scope(identity,generation)
    for s in (identity.organization_id,identity.knowledge_base_id,identity.document_id,
              identity.document_version_id,identity.job_id):
        if not SAFE_ID.fullmatch(s):
            raise ValueError("Invalid bundle scope")
    if any(not isinstance(x,str) or not HEX_SHA.fullmatch(x) for x in (source,records,pin,token_hash)):
        raise ValueError("Invalid bundle provenance")
    path="/".join(("docmind-private-advanced/v1",identity.organization_id,identity.knowledge_base_id,
                  identity.document_id,identity.document_version_id,identity.job_id,generation+".aesgcm"))
    aad=_canonical({"key":path,"source":source,"records":records,"pin":pin,"attempt":token_hash})
    return path,aad


def _check(guard):
    if not callable(guard) or guard() is not True:
        raise RuntimeError("Advanced bundle authorization changed")


def read_bundle(client,bucket,identity,generation,source,records,pin,token_hash,key,*,guard):
    if not isinstance(key,bytes) or len(key)!=32:
        raise ValueError("Invalid bundle key")
    path,aad=_address(identity,generation,source,records,pin,token_hash)
    _check(guard)
    response=client.get_object(Bucket=bucket,Key=path)
    body=response["Body"]
    try:
        raw=body.read(MAX_BYTES+1)
    finally:
        body.close()
    if not isinstance(raw,bytes) or len(raw)>MAX_BYTES or not raw.startswith(MAGIC) or len(raw)<len(MAGIC)+28:
        raise RuntimeError("Invalid advanced bundle")
    offset=len(MAGIC)
    data=json.loads(AESGCM(key).decrypt(raw[offset:offset+12],raw[offset+12:],aad))
    if type(data) is not dict or set(data)!={"ledger","token"}:
        raise RuntimeError("Invalid advanced bundle envelope")
    ledger=_decode_ledger(_canonical(data["ledger"]))
    m=ledger.manifest
    if ((m.document_version_id,m.digest,m.source_sha256,ledger.records_sha256)!=
            (identity.document_version_id,generation,source,records) or _digest(data["token"])!=token_hash):
        raise RuntimeError("Advanced bundle provenance mismatch")
    _check(guard)
    return ledger,data["token"]


def write_bundle(client,bucket,identity,ledger,pin,token,key,*,guard):
    validate_ledger(ledger)
    m=ledger.manifest
    if m.document_version_id!=identity.document_version_id or not isinstance(key,bytes) or len(key)!=32:
        raise ValueError("Invalid bundle scope or key")
    args=(identity,m.digest,m.source_sha256,ledger.records_sha256,pin,_digest(token),key)
    path,aad=_address(*args[:-1])
    plain=_canonical({"ledger":asdict(ledger),"token":token})
    if len(plain)+len(MAGIC)+28>MAX_BYTES:
        raise ValueError("Advanced bundle size limit")
    nonce=os.urandom(12)
    _check(guard)
    client.put_object(Bucket=bucket,Key=path,Body=MAGIC+nonce+AESGCM(key).encrypt(nonce,plain,aad),
                      ContentType="application/octet-stream",IfNoneMatch="*")
    if read_bundle(client,bucket,*args,guard=guard)!=(ledger,token):
        raise RuntimeError("Advanced bundle read-back failed")
