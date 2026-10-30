"""Offline schema/payload/visibility tests; no Qdrant, S3 or MySQL traffic."""
from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from unittest import TestCase, main
from unittest.mock import Mock, patch

from . import production_qdrant as production


@dataclass(frozen=True)
class FakeDataType:
    data_type: str = "keyword"


class FakeModels:
    class Distance:
        COSINE = "Cosine"
    class PayloadSchemaType:
        KEYWORD = "keyword"
    class VectorParams:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)
    class MatchValue:
        def __init__(self, *, value):
            self.value = value
    class FieldCondition:
        def __init__(self, *, key, match):
            self.key = key
            self.match = match
    class Filter:
        def __init__(self, *, must):
            self.must = must


def info(*, size=384, distance="Cosine", payload=None):
    return SimpleNamespace(
        config=SimpleNamespace(params=SimpleNamespace(vectors=SimpleNamespace(size=size, distance=distance))),
        payload_schema=payload or {},
    )


class ProductionQdrantTests(TestCase):
    def setUp(self):
        self.patch_models = patch.object(production, "_models", return_value=FakeModels)
        self.patch_models.start()
        self.addCleanup(self.patch_models.stop)
        self.client = Mock()

    def test_absent_collection_preview_never_writes(self):
        self.client.collection_exists.return_value = False
        res = production.inspect_collection(self.client)
        self.assertFalse(res.exists)
        self.assertEqual(len(res.missing_indexes), 6)
        self.client.create_collection.assert_not_called()
        self.client.create_payload_index.assert_not_called()

    def test_existing_compatible_schema(self):
        self.client.collection_exists.return_value = True
        self.client.get_collection.return_value = info(payload={x: FakeDataType() for x in production.REQUIRED_KEYWORD_INDEXES})
        res = production.inspect_collection(self.client)
        self.assertEqual(res.missing_indexes, ())
        self.assertTrue(res.compatible)

    def test_wrong_dimension_rejected(self):
        self.client.collection_exists.return_value = True
        self.client.get_collection.return_value = info(size=768)
        with self.assertRaisesRegex(ValueError, "incompatible vectors"):
            production.inspect_collection(self.client)

    def test_wrong_metric_rejected(self):
        self.client.collection_exists.return_value = True
        self.client.get_collection.return_value = info(distance="Dot")
        with self.assertRaisesRegex(ValueError, "incompatible vectors"):
            production.inspect_collection(self.client)

    def test_wrong_payload_type_rejected(self):
        self.client.collection_exists.return_value = True
        self.client.get_collection.return_value = info(payload={"organization_id": FakeDataType("integer")})
        with self.assertRaisesRegex(ValueError, "incompatible payload-index"):
            production.inspect_collection(self.client)

    def test_initialization_new_collection_only(self):
        self.client.collection_exists.side_effect = [False, True]
        self.client.get_collection.return_value = info(payload={x: FakeDataType() for x in production.REQUIRED_KEYWORD_INDEXES})
        res = production.initialize_collection(self.client)
        self.assertTrue(res.created)
        self.assertEqual(len(res.indexes_added), 6)
        self.client.create_collection.assert_called_once()
        self.assertEqual(self.client.create_collection.call_args.kwargs["collection_name"], production.PRODUCTION_COLLECTION)
        self.assertEqual(self.client.create_payload_index.call_count, 6)
        self.client.delete_collection.assert_not_called()
        self.client.upsert.assert_not_called()

    def test_existing_collection_repair_only_missing_indexes(self):
        self.client.collection_exists.return_value = True
        self.client.get_collection.side_effect = [
            info(payload={"organization_id": FakeDataType()}),
            info(payload={x: FakeDataType() for x in production.REQUIRED_KEYWORD_INDEXES}),
        ]
        res = production.initialize_collection(self.client)
        self.assertFalse(res.created)
        self.assertEqual(len(res.indexes_added), 5)
        self.client.create_collection.assert_not_called()

    def test_reinitialization_compatible_does_nothing(self):
        self.client.collection_exists.return_value = True
        self.client.get_collection.side_effect = [info(payload={x: FakeDataType() for x in production.REQUIRED_KEYWORD_INDEXES})] * 2
        res = production.initialize_collection(self.client)
        self.assertEqual(res.indexes_added, ())
        self.client.create_collection.assert_not_called()
        self.client.create_payload_index.assert_not_called()

    def test_ready_filter_contains_tenant_kb_and_state(self):
        filter_ = production.authorized_ready_filter("org1", "kb1")
        conditions = {a.key: a.match.value for a in filter_.must}
        self.assertEqual(conditions, {"organization_id": "org1", "knowledge_base_id": "kb1", "index_state": "ready"})

    def test_optional_document_id_scope(self):
        filter_ = production.authorized_ready_filter("org1", "kb1", document_id="d1")
        conditions = {a.key: a.match.value for a in filter_.must}
        self.assertEqual(conditions["document_id"], "d1")

    def test_reject_missing_scope(self):
        for args in (("", "kb1"), ("org1", "")):
            with self.assertRaises(ValueError):
                production.authorized_ready_filter(*args)
        with self.assertRaises(ValueError):
            production.authorized_ready_filter("org1", "kb1", document_id="")

    def test_staging_payload_requires_token(self):
        with patch.object(production, "_base_payload", return_value={
            "organization_id": "org", "page_number": 1, "text": "test content"
        }):
            data = production.staging_payload(object(), object(), "token123")
        self.assertEqual(data["index_state"], "staging")
        self.assertEqual(data["processing_token"], "token123")
        self.assertEqual(data["organization_id"], "org")
        self.assertEqual(data["page_number"], 1)
        with self.assertRaises(ValueError):
            production.staging_payload(object(), object(), "")


if __name__ == "__main__":
    main()
