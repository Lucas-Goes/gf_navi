from __future__ import annotations

import unittest

from pydantic import ValidationError

from app.models import FactType, Memory, Preview, Document


class TestMemoryModel(unittest.TestCase):
    def test_valid_closing_period(self):
        m = Memory(fact_type=FactType.decision, closing_period="2026-06", title="Test", description="x")
        self.assertEqual(m.closing_period, "2026-06")

    def test_invalid_closing_period_month(self):
        with self.assertRaises(ValidationError):
            Memory(fact_type=FactType.decision, closing_period="2026-13", title="Test")

    def test_invalid_closing_period_format(self):
        with self.assertRaises(ValidationError):
            Memory(fact_type=FactType.decision, closing_period="2026/06", title="Test")

    def test_invalid_confidence_score_too_high(self):
        with self.assertRaises(ValidationError):
            Preview(
                title="Test", fact_type=FactType.rule_change,
                closing_period="2026-06", description="test",
                confidence_score=1.5,
            )

    def test_invalid_confidence_score_negative(self):
        with self.assertRaises(ValidationError):
            Preview(
                title="Test", fact_type=FactType.rule_change,
                closing_period="2026-06", description="test",
                confidence_score=-0.1,
            )

    def test_valid_confidence_score(self):
        p = Preview(
            title="Test", fact_type=FactType.implementation,
            closing_period="2026-07", description="desc",
            confidence_score=0.75,
        )
        self.assertEqual(p.confidence_score, 0.75)

    def test_default_confidence_score(self):
        p = Preview(
            title="Test", fact_type=FactType.incident,
            closing_period="2026-06", description="desc",
        )
        self.assertEqual(p.confidence_score, 1.0)

    def test_default_is_active(self):
        m = Memory(fact_type=FactType.other, closing_period="2026-06", title="Test", description="x")
        self.assertTrue(m.is_active)

    def test_fact_type_enum_values(self):
        self.assertEqual(FactType("rule_change"), FactType.rule_change)
        self.assertEqual(FactType("decision"), FactType.decision)
        self.assertEqual(FactType("incident"), FactType.incident)

    def test_invalid_fact_type_raises(self):
        with self.assertRaises(ValueError):
            FactType("invalid_type")


class TestDocumentModel(unittest.TestCase):
    def test_default_chunk_index(self):
        d = Document(filename="test.txt", source_type="txt", title="Test", content="hello")
        self.assertEqual(d.chunk_index, 0)

    def test_filename_and_source(self):
        d = Document(filename="politica.pdf", source_type="pdf", title="Politica", content="x")
        self.assertEqual(d.filename, "politica.pdf")
        self.assertEqual(d.source_type, "pdf")

    def test_auto_generated_uuid(self):
        d = Document(filename="a.txt", source_type="txt", title="A", content="a")
        self.assertIsNotNone(d.id)
        self.assertGreater(len(d.id), 20)
