"""Explicit local advanced RAG pilot configuration and local Ollama adapter.

Private operator-owned config. No model download, remote URL, tool calls,
or hidden fallback. Restart both processes after changing configuration.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
import json
import os
import re
from urllib.request import Request, build_opener, HTTPRedirectHandler, ProxyHandler

from .auto_ingestion_service import PROTECTED_QA_KBS
from .pinned_e5 import ModelArtifactPin, PinnedE5Model, _checked_bytes, MAX_TOKENIZER_BYTES
from .local_pair_model import PairPin, LocalPairModel
from .advanced_grounding import SYSTEM
from .remote_document_shards import decode_checkpoint_key

FLAG="DOCMIND_ENABLE_ADVANCED_LIVE"
VALUE="YES_FOR_TESTING"
KB_ENV="DOCMIND_ADVANCED_LIVE_KB_ID"
CONFIG_ENV="DOCMIND_ADVANCED_LIVE_CONFIG"
ENGINE="phase4.8-advanced-local-v1"


def selected_kb():
    if os.environ.get(FLAG)!=VALUE:
        return None
    kb=os.environ.get(KB_ENV,"")
    if (os.environ.get("NODE_ENV","").strip().lower()!="development" or
            not re.fullmatch(r"[a-z0-9]{10,128}",kb) or kb in PROTECTED_QA_KBS):
        raise RuntimeError("Advanced local pilot requires a new disposable KB")
    if (os.environ.get("DOCMIND_ENABLE_SHADOW_READER")!=VALUE or
            os.environ.get("DOCMIND_ENABLE_SHADOW_REGISTRY")!=VALUE or
            os.environ.get("DOCMIND_ENABLE_PINNED_SHADOW_AUDIT")!=VALUE):
        raise RuntimeError("Advanced local safety flags are incomplete")
    return kb


def enabled_for(kb):
    return selected_kb()==kb


@lru_cache(maxsize=1)
def _config(path):
    p=Path(path)
    if not p.is_absolute() or not p.is_file():
        raise RuntimeError("Absolute private advanced config path required")
    with p.open("rb") as stream:
        data=stream.read(64*1024+1)
    if len(data)>64*1024:
        raise RuntimeError("Advanced config too large")
    c=json.loads(data)
    if type(c) is not dict or set(c)!={"e5","reranker","nli","generator"}:
        raise RuntimeError("Invalid advanced config")
    return c


def config():
    if selected_kb() is None:
        raise RuntimeError("Advanced pilot disabled")
    return _config(os.environ.get(CONFIG_ENV,""))


def _path(value):
    p=Path(value)
    if not p.is_absolute():
        raise RuntimeError("Absolute local model artifact path required")
    return p


@lru_cache(maxsize=1)
def e5_model(path):
    c=_config(path)["e5"]
    return PinnedE5Model(ModelArtifactPin(c["revision"],c["onnx_sha256"],
                         c["tokenizer_sha256"],c.get("output_name","last_hidden_state")),
                         _path(c["onnx_path"]),_path(c["tokenizer_path"]))


def embedding_model():
    config()
    return e5_model(os.environ[CONFIG_ENV])


@lru_cache(maxsize=2)
def _pair(path,kind):
    c=_config(path)[kind]
    pin=PairPin(c["model_id"],c["revision"],c["onnx_sha256"],c["tokenizer_sha256"],
                tuple(c["labels"]),c.get("output_name","logits"))
    if (kind=="reranker" and pin.labels!=("relevance",)) or (kind=="nli" and set(pin.labels)!={"entailment","neutral","contradiction"}):
        raise RuntimeError("Incorrect pair classifier label contract")
    return LocalPairModel(pin,_path(c["onnx_path"]),_path(c["tokenizer_path"]))


def bundle_key():
    return decode_checkpoint_key(os.environ.get("DOCMIND_ADVANCED_BUNDLE_KEY_B64",""))


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):
        raise RuntimeError("Redirect refused")


def _ollama(path,body=None):
    # Fixed loopback URL, no proxies/redirects or external tool execution.
    op=build_opener(ProxyHandler({}),_NoRedirect())
    req=Request("http://127.0.0.1:11434"+path,
                data=None if body is None else json.dumps(body).encode("utf-8"),
                headers={"Content-Type":"application/json"})
    with op.open(req,timeout=90 if body is not None else 10) as response:
        raw=response.read(2*1024*1024+1)
    if len(raw)>2*1024*1024:
        raise RuntimeError("Local model response too large")
    return json.loads(raw)


class LocalGenerator:
    def __init__(self,c):
        from tokenizers import Tokenizer
        self.model=c["model"]
        self.digest=c["digest"]
        self.context=c.get("context",4096)
        self.predict=c.get("predict",384)
        self.offset=c.get("special_token_offset",0)
        if (not isinstance(self.model,str) or not re.fullmatch(r"[A-Za-z0-9_.:/-]{1,150}",self.model) or
                not isinstance(self.digest,str) or not re.fullmatch(r"(sha256:)?[a-f0-9]{64}",self.digest) or
                type(self.context) is not int or not 2048<=self.context<=8192 or
                type(self.predict) is not int or not 128<=self.predict<=1024 or
                type(self.offset) is not int or not 0<=self.offset<=4):
            raise RuntimeError("Invalid pinned generator settings")
        self.tokenizer=Tokenizer.from_str(_checked_bytes(_path(c["tokenizer_path"]),
                 c["tokenizer_sha256"],MAX_TOKENIZER_BYTES).decode("utf-8"))
        self.tokenizer.no_padding()
        self.tokenizer.no_truncation()

    def _tokens(self,prompt):
        return len(self.tokenizer.encode(prompt,add_special_tokens=False).ids)+self.offset

    def count_tokens(self,question,evidence):
        prompt=SYSTEM+"\n"+json.dumps({"question":question,"evidence":json.loads(evidence)},
                                     ensure_ascii=False,separators=(",",":"))
        return self._tokens(prompt)+self.predict

    def _verify_model(self):
        tags=_ollama("/api/tags")
        matches=[m for m in tags.get("models",[]) if m.get("name")==self.model]
        if len(matches)!=1 or matches[0].get("digest","").removeprefix("sha256:")!=self.digest.removeprefix("sha256:"):
            raise RuntimeError("Configured Ollama model digest changed or missing")

    def __call__(self,prompt):
        count=self._tokens(prompt)
        if count+self.predict>self.context:
            raise RuntimeError("Generator context budget exceeded")
        self._verify_model()
        result=_ollama("/api/generate",{"model":self.model,"prompt":prompt,"raw":True,
                      "stream":False,"format":"json","options":{"temperature":0,
                      "num_ctx":self.context,"num_predict":self.predict},"keep_alive":"5m"})
        self._verify_model()
        # Refuse truncated/uncalibrated prompts and incomplete JSON output.
        if (result.get("done") is not True or result.get("done_reason")!="stop" or
                type(result.get("prompt_eval_count")) is not int or result["prompt_eval_count"]!=count or
                not isinstance(result.get("response"),str)):
            raise RuntimeError("Local generation or exact tokenizer verification failed")
        return result["response"]


@lru_cache(maxsize=1)
def _generator(path):
    return LocalGenerator(_config(path)["generator"])


def answer_models():
    config()
    path=os.environ[CONFIG_ENV]
    return _pair(path,"reranker"),_pair(path,"nli"),_generator(path)
