"""
Tests for the ResolutionOrchestrator component.
"""

import pytest
from unittest.mock import Mock, patch, MagicMock
from typing import List
from collections import deque

from bill_parser_engine.core.reference_resolver.resolution_orchestrator import ResolutionOrchestrator
from bill_parser_engine.core.reference_resolver.models import (
    LinkedReference,
    ResolutionResult,
    ResolvedReference,
    ReferenceSourceType,
    LocatedReference,
    ReconstructorOutput,
)


class TestResolutionOrchestrator:
    """Test suite for ResolutionOrchestrator component."""

    @pytest.fixture
    def mock_text_retriever(self):
        """Create a mock text retriever."""
        return Mock()

    @pytest.fixture
    def mock_reference_locator(self):
        """Create a mock reference locator."""
        return Mock()

    @pytest.fixture
    def mock_reference_linker(self):
        """Create a mock reference linker."""
        return Mock()

    @pytest.fixture
    def orchestrator(self, mock_text_retriever, mock_reference_locator, mock_reference_linker):
        """Create a ResolutionOrchestrator instance with mocked dependencies."""
        return ResolutionOrchestrator(
            text_retriever=mock_text_retriever,
            reference_locator=mock_reference_locator,
            reference_linker=mock_reference_linker,
            max_depth=3
        )

    @pytest.fixture
    def sample_linked_references(self) -> List[LinkedReference]:
        """Create sample linked references from the legislative bill example."""
        return [
            LinkedReference(
                reference_text="aux 1° ou 2° du II",
                source=ReferenceSourceType.DELETIONAL,
                object="activités",
                agreement_analysis="Feminine plural agreement with activités",
                confidence=0.95
            ),
            LinkedReference(
                reference_text="du 11 de l'article 3 du règlement (CE) n° 1107/2009",
                source=ReferenceSourceType.DEFINITIONAL,
                object="producteurs",
                agreement_analysis="Defines the sense/meaning of producteurs",
                confidence=0.98
            ),
            LinkedReference(
                reference_text="à l'article L. 253-5 du présent code",
                source=ReferenceSourceType.DEFINITIONAL,
                object="la liste",
                agreement_analysis="Feminine singular agreement with la liste",
                confidence=0.97
            )
        ]

    def test_initialization_default_components(self):
        """Test that ResolutionOrchestrator initializes with default components."""
        orchestrator = ResolutionOrchestrator()
        
        assert orchestrator.text_retriever is not None
        assert orchestrator.reference_locator is not None
        assert orchestrator.reference_linker is not None
        assert orchestrator.max_depth == 3

    def test_initialization_custom_components(self, mock_text_retriever, mock_reference_locator, mock_reference_linker):
        """Test initialization with custom components."""
        orchestrator = ResolutionOrchestrator(
            text_retriever=mock_text_retriever,
            reference_locator=mock_reference_locator,
            reference_linker=mock_reference_linker,
            max_depth=5
        )
        
        assert orchestrator.text_retriever is mock_text_retriever
        assert orchestrator.reference_locator is mock_reference_locator
        assert orchestrator.reference_linker is mock_reference_linker
        assert orchestrator.max_depth == 5

    def test_create_reference_signature(self, orchestrator):
        """Test reference signature creation for cycle detection."""
        ref = LinkedReference(
            reference_text="aux 1° ou 2° du II",
            source=ReferenceSourceType.DELETIONAL,
            object="activités",
            agreement_analysis="Test analysis",
            confidence=0.95
        )
        
        signature = orchestrator._create_reference_signature(ref)
        expected = "DELETIONAL:activités:aux 1° ou 2° du II"
        assert signature == expected

    def test_assess_relevance_always_true(self, orchestrator):
        """Test that relevance assessment returns True for all references (current heuristic)."""
        ref = LinkedReference(
            reference_text="test reference",
            source=ReferenceSourceType.DEFINITIONAL,
            object="test object",
            agreement_analysis="Test analysis",
            confidence=0.8
        )
        
        assert orchestrator._assess_relevance(ref) is True

    def test_classify_reference_eu_regulation(self, orchestrator):
        """Test EU regulation reference classification."""
        ref_text = "du 11 de l'article 3 du règlement (CE) n° 1107/2009"
        classification = orchestrator._classify_reference(ref_text)
        assert classification == "eu_regulation"

    def test_classify_reference_french_code(self, orchestrator):
        """Test French code reference classification."""
        ref_text = "à l'article L. 253-5 du présent code"
        classification = orchestrator._classify_reference(ref_text)
        assert classification == "french_code"

    def test_classify_reference_internal(self, orchestrator):
        """Test internal reference classification."""
        ref_text = "aux 1° ou 2° du II"
        classification = orchestrator._classify_reference(ref_text)
        assert classification == "internal_reference"

    def test_classify_reference_other(self, orchestrator):
        """Test unclassified reference."""
        ref_text = "some unknown reference format"
        classification = orchestrator._classify_reference(ref_text)
        assert classification == "other"

    def test_parse_french_code_reference_with_present_code(self, orchestrator):
        """Test parsing French code reference with 'présent code'."""
        ref_text = "à l'article L. 253-5 du présent code"
        code, article = orchestrator._parse_french_code_reference(ref_text)
        
        assert code == "code rural et de la pêche maritime"
        assert article == "L. 253-5"

    def test_parse_french_code_reference_with_explicit_code(self, orchestrator):
        """Test parsing French code reference with explicit code name."""
        ref_text = "à l'article L. 411-2 du code de l'environnement"
        code, article = orchestrator._parse_french_code_reference(ref_text)
        
        assert code == "code de l'environnement"
        assert article == "L. 411-2"

    def test_parse_french_code_reference_default_code(self, orchestrator):
        """Test parsing French code reference with default code."""
        ref_text = "à l'article L. 254-1"
        code, article = orchestrator._parse_french_code_reference(ref_text)
        
        assert code == "code rural et de la pêche maritime"
        assert article == "L. 254-1"

    def test_parse_french_code_reference_invalid(self, orchestrator):
        """Test parsing invalid French code reference."""
        ref_text = "some invalid reference"
        code, article = orchestrator._parse_french_code_reference(ref_text)
        
        assert code is None
        assert article is None

    def test_retrieve_french_code_reference_success(self, orchestrator, mock_text_retriever):
        """Test successful French code reference retrieval."""
        ref = LinkedReference(
            reference_text="à l'article L. 253-5 du présent code",
            source=ReferenceSourceType.DEFINITIONAL,
            object="la liste",
            agreement_analysis="Test analysis",
            confidence=0.97
        )
        
        mock_text_retriever.fetch_article_text.return_value = (
            "Article L. 253-5 content...",
            {"source": "pylegifrance", "success": True}
        )
        
        content, metadata = orchestrator._retrieve_french_code_reference(ref)
        
        assert content == "Article L. 253-5 content..."
        assert metadata["reference_type"] == "french_code"
        assert metadata["parsed_code"] == "code rural et de la pêche maritime"
        assert metadata["parsed_article"] == "L. 253-5"
        
        mock_text_retriever.fetch_article_text.assert_called_once_with(
            "code rural et de la pêche maritime", "L. 253-5"
        )

    def test_retrieve_french_code_reference_parse_error(self, orchestrator):
        """Test French code reference retrieval with parse error."""
        ref = LinkedReference(
            reference_text="invalid reference",
            source=ReferenceSourceType.DEFINITIONAL,
            object="test",
            agreement_analysis="Test analysis",
            confidence=0.5
        )
        
        content, metadata = orchestrator._retrieve_french_code_reference(ref)
        
        assert content == ""
        assert metadata["source"] == "parse_error"
        assert metadata["success"] is False

    def test_retrieve_eu_regulation_reference_fallback(self, orchestrator):
        """Test EU regulation reference retrieval fallback."""
        ref = LinkedReference(
            reference_text="du 11 de l'article 3 du règlement (CE) n° 1107/2009",
            source=ReferenceSourceType.DEFINITIONAL,
            object="producteurs",
            agreement_analysis="Test analysis",
            confidence=0.98
        )
        
        # Mock the web search to return None (typical fallback case)
        orchestrator.text_retriever._search_web_for_article = Mock(return_value=None)
        
        content, metadata = orchestrator._retrieve_eu_regulation_reference(ref)
        
        assert content == ""
        assert metadata["reference_type"] == "eu_regulation"
        assert metadata["success"] is False

    def test_retrieve_internal_reference_placeholder(self, orchestrator):
        """Test internal reference retrieval returns placeholder."""
        ref = LinkedReference(
            reference_text="aux 1° ou 2° du II",
            source=ReferenceSourceType.DELETIONAL,
            object="activités",
            agreement_analysis="Test analysis",
            confidence=0.95
        )
        
        content, metadata = orchestrator._retrieve_internal_reference(ref)
        
        assert content == ""
        assert metadata["reference_type"] == "internal_reference"
        assert metadata["success"] is False
        assert "context" in metadata["error"]

    def test_discover_sub_references_success(self, orchestrator, mock_reference_locator, mock_reference_linker):
        """Test successful sub-reference discovery."""
        content = "Some legal text with references..."
        
        # Mock located references
        located_refs = [
            LocatedReference(
                reference_text="test reference",
                start_position=10,
                end_position=25,
                source=ReferenceSourceType.DEFINITIONAL,
                confidence=0.9
            )
        ]
        mock_reference_locator.locate.return_value = located_refs
        
        # Mock linked references
        linked_refs = [
            LinkedReference(
                reference_text="test reference",
                source=ReferenceSourceType.DEFINITIONAL,
                object="test object",
                agreement_analysis="Test analysis",
                confidence=0.9
            )
        ]
        mock_reference_linker.link_references.return_value = linked_refs
        
        result = orchestrator._discover_sub_references(content)
        
        assert len(result) == 1
        assert result[0].reference_text == "test reference"
        assert mock_reference_locator.locate.called
        assert mock_reference_linker.link_references.called

    def test_discover_sub_references_failure(self, orchestrator, mock_reference_locator):
        """Test sub-reference discovery with component failure."""
        content = "Some legal text..."
        
        mock_reference_locator.locate.side_effect = Exception("Test error")
        
        result = orchestrator._discover_sub_references(content)
        assert result == []

    def test_resolve_references_invalid_input(self, orchestrator):
        """Test resolve_references with invalid input."""
        with pytest.raises(ValueError, match="linked_references must be a list"):
            orchestrator.resolve_references("not a list")

    def test_resolve_references_empty_list(self, orchestrator):
        """Test resolve_references with empty list."""
        result = orchestrator.resolve_references([])
        
        assert isinstance(result, ResolutionResult)
        assert len(result.resolved_deletional_references) == 0
        assert len(result.resolved_definitional_references) == 0
        assert len(result.unresolved_references) == 0

    def test_resolve_references_success_no_recursion(self, orchestrator, sample_linked_references, mock_text_retriever):
        """Test successful resolution without recursion."""
        # Mock successful content retrieval for all references
        def mock_retrieve(ref):
            return "Retrieved content for test", {"source": "test", "success": True}
        
        orchestrator._retrieve_reference_content = Mock(side_effect=mock_retrieve)
        
        # Mock empty sub-reference discovery
        orchestrator._discover_sub_references = Mock(return_value=[])
        
        result = orchestrator.resolve_references(sample_linked_references)
        
        assert isinstance(result, ResolutionResult)
        assert len(result.resolved_deletional_references) == 1  # One DELETIONAL ref
        assert len(result.resolved_definitional_references) == 2  # Two DEFINITIONAL refs
        assert len(result.unresolved_references) == 0

    def test_resolve_references_with_failures(self, orchestrator, sample_linked_references):
        """Test resolution with some reference failures."""
        # Mock the retrieve method to fail for certain references
        def mock_retrieve(ref):
            if "1107/2009" in ref.reference_text:
                return "", {"success": False, "error": "Retrieval failed"}
            return "Retrieved content", {"success": True}
        
        orchestrator._retrieve_reference_content = Mock(side_effect=mock_retrieve)
        orchestrator._discover_sub_references = Mock(return_value=[])
        
        result = orchestrator.resolve_references(sample_linked_references)
        
        assert len(result.resolved_deletional_references) == 1
        assert len(result.resolved_definitional_references) == 1  # One failed
        assert len(result.unresolved_references) == 1

    def test_resolve_references_cycle_detection(self, orchestrator):
        """Test cycle detection prevents infinite recursion."""
        # Create a reference that would create a cycle
        ref = LinkedReference(
            reference_text="test reference",
            source=ReferenceSourceType.DEFINITIONAL,
            object="test object",
            agreement_analysis="Test analysis",
            confidence=0.9
        )
        
        # Mock retrieval to succeed
        orchestrator._retrieve_reference_content = Mock(return_value=(
            "Retrieved content", {"success": True}
        ))
        
        # Mock sub-reference discovery to return the same reference (cycle)
        orchestrator._discover_sub_references = Mock(return_value=[ref])
        
        result = orchestrator.resolve_references([ref])
        
        # Should resolve the first instance but detect cycle on second
        assert len(result.resolved_definitional_references) == 1
        assert result.resolution_tree["total_processed"] >= 1

    def test_resolve_references_max_depth_control(self, orchestrator):
        """Test max depth control prevents excessive recursion."""
        # Create nested references that would exceed max depth
        refs = [
            LinkedReference(
                reference_text=f"reference {i}",
                source=ReferenceSourceType.DEFINITIONAL,
                object=f"object {i}",
                agreement_analysis="Test analysis",
                confidence=0.9
            )
            for i in range(5)
        ]
        
        # Mock retrieval to succeed
        orchestrator._retrieve_reference_content = Mock(return_value=(
            "Retrieved content", {"success": True}
        ))
        
        # Mock sub-reference discovery to return next reference in chain
        def mock_discover(content):
            # Simulate finding the next reference in the chain
            return [refs[1]] if len(refs) > 1 else []
        
        orchestrator._discover_sub_references = Mock(side_effect=mock_discover)
        
        result = orchestrator.resolve_references([refs[0]])
        
        # Should respect max_depth=3
        assert result.resolution_tree["total_processed"] <= 4  # Initial + 3 levels

    def test_resolve_references_resolution_tree_structure(self, orchestrator, sample_linked_references):
        """Test that resolution tree is properly structured."""
        # Mock successful resolution
        orchestrator._retrieve_reference_content = Mock(return_value=(
            "Retrieved content", {"success": True}
        ))
        orchestrator._discover_sub_references = Mock(return_value=[])
        
        result = orchestrator.resolve_references(sample_linked_references)
        
        # Check resolution tree structure
        tree = result.resolution_tree
        assert "depth" in tree
        assert "nodes" in tree
        assert "total_processed" in tree
        
        # Check nodes structure
        nodes = tree["nodes"]
        assert len(nodes) == 3  # deletional, definitional, unresolved
        
        node_types = [node["type"] for node in nodes]
        assert "deletional" in node_types
        assert "definitional" in node_types
        assert "unresolved" in node_types
        
        for node in nodes:
            assert "count" in node
            assert "references" in node

    def test_resolve_references_real_world_pattern(self, orchestrator):
        """Test resolution with real-world reference patterns from legislative bill."""
        # Create references matching the patterns from the legislative bill example
        real_refs = [
            LinkedReference(
                reference_text="aux articles L. 254-6-2 et L. 254-6-3",
                source=ReferenceSourceType.DELETIONAL,
                object="conseil",
                agreement_analysis="References to specific articles about council",
                confidence=0.96
            ),
            LinkedReference(
                reference_text="du 11 de l'article 3 du règlement (CE) n° 1107/2009",
                source=ReferenceSourceType.DEFINITIONAL,
                object="producteurs",
                agreement_analysis="Defines the meaning of producteurs",
                confidence=0.98
            ),
            LinkedReference(
                reference_text="à l'article L. 253-5 du présent code",
                source=ReferenceSourceType.DEFINITIONAL,
                object="la liste",
                agreement_analysis="References the biocontrol product list",
                confidence=0.97
            )
        ]
        
        # Mock different retrieval strategies for different reference types
        def mock_retrieve(ref):
            if "règlement (CE)" in ref.reference_text:
                return "", {"success": False, "error": "EU regulation not implemented"}
            elif "L. 253-5" in ref.reference_text:
                return "Article L. 253-5 - Liste des produits de biocontrôle...", {"success": True}
            elif "L. 254-6" in ref.reference_text:
                return "Articles L. 254-6-2 et L. 254-6-3 - Conseil obligations...", {"success": True}
            return "", {"success": False}
        
        orchestrator._retrieve_reference_content = Mock(side_effect=mock_retrieve)
        orchestrator._discover_sub_references = Mock(return_value=[])
        
        result = orchestrator.resolve_references(real_refs)
        
        # Check that different reference types are handled appropriately
        assert len(result.resolved_deletional_references) == 1  # L. 254-6 articles
        assert len(result.resolved_definitional_references) == 1  # L. 253-5
        assert len(result.unresolved_references) == 1  # EU regulation

        # Verify metadata indicates different retrieval strategies were used
        resolved_refs = result.resolved_deletional_references + result.resolved_definitional_references
        for resolved_ref in resolved_refs:
            assert resolved_ref.retrieval_metadata["success"] is True