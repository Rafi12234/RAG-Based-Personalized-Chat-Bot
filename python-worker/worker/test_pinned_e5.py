"""Real local tokenizer + synthetic ONNX graph; no model download/datastore."""
from __future__ import annotations

from dataclasses import replace
from hashlib import sha256
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

try:
    import numpy as np
    import onnx
    import onnxruntime
    from onnx import TensorProto, helper
    from tokenizers import Tokenizer
    from tokenizers.models import WordLevel
    from tokenizers.pre_tokenizers import Whitespace
    from tokenizers.processors import TemplateProcessing
    HAS_TEST_RUNTIME = True
except ImportError:
    HAS_TEST_RUNTIME = False

from .pinned_e5 import ModelArtifactPin, ModelPinError, PinnedE5Model
from .pinned_shadow_verification import verify_pinned_shadow
from .qdrant_indexing import IndexIdentity
from .hierarchy_vector_fidelity import VectorFidelityReport, VectorFidelityError
from .hierarchical_shadow import (SHADOW_COLLECTION, initialize_shadow_collection,
                                  iter_shadow_embeddings, stage_shadow_index)
from .test_hierarchical_shadow import MockModels
from .test_hierarchy_vector_fidelity import NumericQdrant
from .test_hierarchy_manifest_store import make_ledger
from .auto_ingestion_service import PROTECTED_QA_KBS


def sha(path):
    return sha256(path.read_bytes()).hexdigest()


def write_fixture(root):
    tok = Tokenizer(WordLevel({
        "<unk>": 0, "<pad>": 1, "<s>": 2, "</s>": 3,
        "passage": 4, "query": 5, ":": 6, "book": 7, "বাংলা": 8,
    }, unk_token="<unk>"))
    tok.pre_tokenizer = Whitespace()
    tok.post_processor = TemplateProcessing(
        single="<s> $A </s>", special_tokens=[("<s>", 2), ("</s>", 3)])
    tok.enable_truncation(max_length=8)
    tok.enable_padding(length=8, pad_id=1, pad_token="<pad>")
    tokenizer_path = root / "tokenizer.json"
    tok.save(str(tokenizer_path))
    # Feature 0 = token ID, feature 1 = 1, others = 0. Padding must not
    # influence the mean, and different inputs need not share a direction.
    weights = helper.make_tensor("weights", TensorProto.FLOAT, [1, 384],
                                 [1.] + [0.] * 383)
    bias = helper.make_tensor("bias", TensorProto.FLOAT, [384],
                              [0., 1.] + [0.] * 382)
    axes = helper.make_tensor("axes", TensorProto.INT64, [1], [2])
    graph = helper.make_graph([
        helper.make_node("Cast", ["input_ids"], ["float_ids"], to=TensorProto.FLOAT),
        helper.make_node("Unsqueeze", ["float_ids", "axes"], ["expanded"]),
        helper.make_node("MatMul", ["expanded", "weights"], ["projected"]),
        helper.make_node("Add", ["projected", "bias"], ["last_hidden_state"]),
    ], "synthetic-token-graph", [
        helper.make_tensor_value_info("input_ids", TensorProto.INT64, ["batch", "seq"]),
        helper.make_tensor_value_info("attention_mask", TensorProto.INT64, ["batch", "seq"]),
    ], [
        helper.make_tensor_value_info("last_hidden_state", TensorProto.FLOAT,
                                      ["batch", "seq", 384]),
    ], [weights, bias, axes])
    model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 13)])
    model.ir_version = 8
    onnx.checker.check_model(model)
    model_path = root / "model.onnx"
    onnx.save(model, str(model_path))
    pin = ModelArtifactPin("a" * 40, sha(model_path), sha(tokenizer_path))
    return pin, model_path, tokenizer_path


@unittest.skipUnless(HAS_TEST_RUNTIME, "Install optional pinned-model test dependencies")
class PinnedE5Tests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.pin, self.model_path, self.tokenizer_path = write_fixture(Path(folder.name))

    def load(self):
        return PinnedE5Model(self.pin, self.model_path, self.tokenizer_path)

    def test_real_local_runtime_masked_pooling_and_padding(self):
        model = self.load()
        short = "query: book"
        long = "passage: বাংলা book বাংলা book"
        outputs = model.embed([short, long], batch_size=2)
        expected = np.array([np.mean(model.encode(short).ids), 1.] + [0.] * 382)
        expected /= np.linalg.norm(expected)
        np.testing.assert_allclose(outputs[0], expected, atol=1e-7)
        np.testing.assert_allclose(outputs[0], model.embed([short])[0], atol=1e-7)
        self.assertFalse(np.allclose(outputs[0], outputs[1]))
        self.assertEqual(model.pin.digest, self.pin.digest)

    def test_checksums_before_parser_and_runtime(self):
        for target in (self.model_path, self.tokenizer_path):
            original = target.read_bytes()
            target.write_bytes(original + b"x")
            with patch("onnxruntime.InferenceSession") as runtime:
                with self.assertRaisesRegex(ModelPinError, "checksum"):
                    self.load()
                runtime.assert_not_called()
            target.write_bytes(original)

    def test_already_loaded_model_does_not_reread_mutated_files(self):
        model = self.load()
        before = model.embed(["query: book"])
        self.model_path.write_bytes(b"changed")
        self.tokenizer_path.write_bytes(b"changed")
        self.assertEqual(before, model.embed(["query: book"]))
        with self.assertRaises(ModelPinError):
            self.load()

    def test_no_silent_token_truncation_and_batch_bounds(self):
        model = self.load()
        text = "passage: " + "book " * 520
        self.assertGreater(len(model.encode(text).ids), 512)
        for batch, size in (([text], 1), (["query: "], 1), (["book"], 1),
                            (["query: book"] * 2, 1), ([], 1),
                            (["query: book"], True), (["query: book"], 33)):
            with self.subTest(size=size), self.assertRaises(ModelPinError):
                model.embed(batch, batch_size=size)

    def test_pin_changes_and_output_contract(self):
        with self.assertRaises(ModelPinError):
            replace(self.pin, revision="main")
        with self.assertRaises(ModelPinError):
            replace(self.pin, onnx_sha256="0" * 63)
        changed = replace(self.pin, revision="b" * 40)
        self.assertNotEqual(changed.digest, self.pin.digest)
        with self.assertRaisesRegex(ModelPinError, "output"):
            PinnedE5Model(replace(self.pin, output_name="wrong"),
                          self.model_path, self.tokenizer_path)

    def test_actual_shadow_proof_and_corrupted_vector(self):
        model = self.load()
        passages, ledger = make_ledger()
        identity = IndexIdentity("org1", "kb1", "doc1", "ver", "job1", "book.pdf")
        client = NumericQdrant()
        with patch.dict(os.environ, {"DOCMIND_ENABLE_PINNED_SHADOW_AUDIT": "YES_FOR_TESTING",
                                    "NODE_ENV": "test"}), patch(
            "worker.hierarchical_shadow._models", return_value=MockModels), patch(
            "worker.hierarchy_shadow_audit._models", return_value=MockModels):
            initialize_shadow_collection(client, explicit_create=True)
            stage_shadow_index(client, identity, ledger.manifest,
                               iter_shadow_embeddings(passages, model),
                               "token", lease_guard=lambda: True)
            writes = client.upserts
            receipt = verify_pinned_shadow(client, identity, ledger, "token", model,
                                           lease_guard=lambda: True, batch_size=2)
            self.assertEqual(receipt.report.points_verified, len(passages))
            self.assertGreaterEqual(receipt.report.min_cosine, .999)
            self.assertEqual(receipt.document_version_id, identity.document_version_id)
            self.assertEqual(receipt.job_id, identity.job_id)
            self.assertEqual(client.upserts, writes)
            first = next(iter(client.points.values()))
            first.vector = [0., 0., 1.] + [0.] * 381
            with self.assertRaisesRegex(VectorFidelityError, "differs"):
                verify_pinned_shadow(client, identity, ledger, "token", model,
                                     lease_guard=lambda: True, batch_size=2)
            self.assertEqual(client.upserts, writes)

    def test_receipt_bound_and_disabled_in_production(self):
        model = self.load()
        _, ledger = make_ledger()
        scope = IndexIdentity("org1", "kb1", "doc1", "ver", "job1", "book.pdf")
        report = VectorFidelityReport(
            SHADOW_COLLECTION, ledger.manifest.digest, ledger.manifest.passage_count,
            "intfloat/multilingual-e5-small", 1.0)
        args = (object(), scope, ledger, "token", model)
        with patch.dict(os.environ, {"DOCMIND_ENABLE_PINNED_SHADOW_AUDIT": "NO",
                                    "NODE_ENV": "test"}):
            with self.assertRaisesRegex(RuntimeError, "disabled"):
                verify_pinned_shadow(*args, lease_guard=lambda: True)
        with patch.dict(os.environ, {"DOCMIND_ENABLE_PINNED_SHADOW_AUDIT": "YES_FOR_TESTING",
                                    "NODE_ENV": "production"}):
            with self.assertRaisesRegex(RuntimeError, "disabled"):
                verify_pinned_shadow(*args, lease_guard=lambda: True)
        with patch.dict(os.environ, {"DOCMIND_ENABLE_PINNED_SHADOW_AUDIT": "YES_FOR_TESTING",
                                    "NODE_ENV": "test"}), patch(
            "worker.pinned_shadow_verification.verify_shadow_vector_fidelity",
            return_value=report) as audit:
            protected = replace(scope, knowledge_base_id=next(iter(PROTECTED_QA_KBS)))
            with self.assertRaisesRegex(RuntimeError, "Protected"):
                verify_pinned_shadow(object(), protected, ledger, "token", model,
                                     lease_guard=lambda: True)
            with self.assertRaisesRegex(RuntimeError, "lease"):
                verify_pinned_shadow(*args, lease_guard=lambda: False)
            audit.assert_not_called()
            receipt = verify_pinned_shadow(*args, lease_guard=lambda: True)
            self.assertEqual(receipt.artifact_pin_sha256, self.pin.digest)
            self.assertEqual(receipt.records_sha256, ledger.records_sha256)
            self.assertEqual(receipt.source_sha256, ledger.manifest.source_sha256)
            self.assertFalse(receipt.query_enabled)
            self.assertFalse(receipt.authorized_to_publish)
            self.assertEqual(audit.call_args.kwargs["minimum_cosine"], .999)
            guards = iter([True, False])
            with self.assertRaisesRegex(RuntimeError, "lease changed"):
                verify_pinned_shadow(*args, lease_guard=lambda: next(guards))


if __name__ == "__main__":
    unittest.main()
