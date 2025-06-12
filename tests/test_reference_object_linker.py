"""
Tests for the ReferenceObjectLinker component.
"""

import json
import pytest
from unittest.mock import Mock, patch, MagicMock
from typing import List

from bill_parser_engine.core.reference_resolver.reference_object_linker import ReferenceObjectLinker
from bill_parser_engine.core.reference_resolver.models import (
    LocatedReference,
    LinkedReference,
    ReconstructorOutput,
    ReferenceSourceType,
)


class TestReferenceObjectLinker:
    """Test suite for ReferenceObjectLinker component."""

    @pytest.fixture
    def mock_client(self):
        """Create a mock Mistral client."""
        with patch('bill_parser_engine.core.reference_resolver.reference_object_linker.Mistral') as mock_mistral:
            yield mock_mistral.return_value

    @pytest.fixture
    def linker(self, mock_client):
        """Create a ReferenceObjectLinker instance with mocked client."""
        with patch.dict('os.environ', {'MISTRAL_API_KEY': 'test-key'}):
            return ReferenceObjectLinker()

    @pytest.fixture
    def sample_located_references(self) -> List[LocatedReference]:
        """Create sample located references from the legislative bill."""
        return [
            LocatedReference(
                reference_text="aux 1° ou 2° du II",
                start_position=44,
                end_position=63,
                source=ReferenceSourceType.DELETIONAL,
                confidence=0.98
            ),
            LocatedReference(
                reference_text="du 11 de l'article 3 du règlement (CE) n° 1107/2009",
                start_position=28,
                end_position=81,
                source=ReferenceSourceType.DEFINITIONAL,
                confidence=0.99
            ),
            LocatedReference(
                reference_text="à l'article L. 253-5 du présent code",
                start_position=162,
                end_position=199,
                source=ReferenceSourceType.DEFINITIONAL,
                confidence=0.97
            )
        ]

    @pytest.fixture
    def sample_reconstructor_output(self) -> ReconstructorOutput:
        """Create sample reconstructor output with DELETIONAL and DEFINITIONAL text."""
        return ReconstructorOutput(
            deleted_or_replaced_text="incompatible avec celui des activités mentionnées aux 1° ou 2° du II ou au IV",
            intermediate_after_state_text="interdit aux producteurs au sens du 11 de l'article 3 du règlement (CE) n° 1107/2009, sauf lorsque la production concerne des produits de biocontrôle figurant sur la liste mentionnée à l'article L. 253-5 du présent code"
        )

    def _create_mock_response(self, function_name: str, arguments: dict):
        """Create a mock Mistral API response with tool call."""
        mock_response = Mock()
        mock_response.choices = [Mock()]
        mock_response.choices[0].message = Mock()
        mock_response.choices[0].message.tool_calls = [Mock()]
        mock_response.choices[0].message.tool_calls[0].function = Mock()
        mock_response.choices[0].message.tool_calls[0].function.name = function_name
        mock_response.choices[0].message.tool_calls[0].function.arguments = json.dumps(arguments)
        return mock_response

    def test_initialization(self, mock_client):
        """Test that ReferenceObjectLinker initializes correctly."""
        with patch.dict('os.environ', {'MISTRAL_API_KEY': 'test-key'}):
            linker = ReferenceObjectLinker()
            
        assert linker.client is not None
        assert linker.system_prompt is not None
        assert linker.tool_schema is not None
        assert len(linker.tool_schema) == 1
        assert linker.tool_schema[0]["function"]["name"] == "link_reference_to_object"

    def test_system_prompt_creation(self, linker):
        """Test that system prompt contains French grammatical patterns."""
        prompt = linker._create_system_prompt()
        
        # Check for key grammatical patterns
        assert "FRENCH GRAMMATICAL PATTERNS" in prompt
        assert "au sens du" in prompt
        assert "mentionnées aux" in prompt
        assert "activités" in prompt
        assert "producteurs" in prompt
        
        # Check for examples
        assert "aux 1° ou 2° du II" in prompt
        assert "du 11 de l'article 3" in prompt

    def test_tool_schema_creation(self, linker):
        """Test that tool schema is correctly structured."""
        schema = linker._create_tool_schema()
        
        assert len(schema) == 1
        function_schema = schema[0]["function"]
        
        assert function_schema["name"] == "link_reference_to_object"
        assert function_schema["description"]
        
        parameters = function_schema["parameters"]
        properties = parameters["properties"]
        
        # Check required fields
        required_fields = ["object", "agreement_analysis", "confidence"]
        assert parameters["required"] == required_fields
        
        for field in required_fields:
            assert field in properties
            assert "type" in properties[field]
            assert "description" in properties[field]

    def test_context_selection_deletional(self, linker, sample_reconstructor_output):
        """Test context selection for DELETIONAL references."""
        context = linker._select_context(ReferenceSourceType.DELETIONAL, sample_reconstructor_output)
        assert context == sample_reconstructor_output.deleted_or_replaced_text

    def test_context_selection_definitional(self, linker, sample_reconstructor_output):
        """Test context selection for DEFINITIONAL references."""
        context = linker._select_context(ReferenceSourceType.DEFINITIONAL, sample_reconstructor_output)
        assert context == sample_reconstructor_output.intermediate_after_state_text

    def test_build_grammatical_analysis_prompt(self, linker, sample_located_references):
        """Test grammatical analysis prompt building."""
        ref = sample_located_references[0]  # DELETIONAL reference
        context = "incompatible avec celui des activités mentionnées aux 1° ou 2° du II"
        
        prompt = linker._build_grammatical_analysis_prompt(ref, context)
        
        assert ref.reference_text in prompt
        assert context in prompt
        assert str(ref.start_position) in prompt
        assert str(ref.end_position) in prompt
        assert ref.source.value in prompt
        assert "French grammatical agreement" in prompt

    def test_extract_tool_call_success(self, linker):
        """Test successful tool call extraction."""
        arguments = {
            "object": "activités",
            "agreement_analysis": "Feminine plural agreement with activités",
            "confidence": 0.95
        }
        mock_response = self._create_mock_response("link_reference_to_object", arguments)
        
        result = linker._extract_tool_call(mock_response)
        
        assert result is not None
        assert result["name"] == "link_reference_to_object"
        assert result["arguments"] == arguments

    def test_extract_tool_call_no_choices(self, linker):
        """Test tool call extraction with no choices."""
        mock_response = Mock()
        mock_response.choices = []
        
        result = linker._extract_tool_call(mock_response)
        assert result is None

    def test_extract_tool_call_no_tool_calls(self, linker):
        """Test tool call extraction with no tool calls."""
        mock_response = Mock()
        mock_response.choices = [Mock()]
        mock_response.choices[0].message = Mock()
        mock_response.choices[0].message.tool_calls = None
        
        result = linker._extract_tool_call(mock_response)
        assert result is None

    def test_extract_tool_call_wrong_function(self, linker):
        """Test tool call extraction with wrong function name."""
        arguments = {"test": "value"}
        mock_response = self._create_mock_response("wrong_function", arguments)
        
        result = linker._extract_tool_call(mock_response)
        assert result is None

    def test_validate_tool_call_response_success(self, linker):
        """Test successful tool call response validation."""
        tool_call = {
            "name": "link_reference_to_object",
            "arguments": {
                "object": "activités",
                "agreement_analysis": "Feminine plural agreement",
                "confidence": 0.95
            }
        }
        
        assert linker._validate_tool_call_response(tool_call) is True

    def test_validate_tool_call_response_missing_field(self, linker):
        """Test tool call validation with missing required field."""
        tool_call = {
            "name": "link_reference_to_object",
            "arguments": {
                "object": "activités",
                "confidence": 0.95
                # Missing agreement_analysis
            }
        }
        
        assert linker._validate_tool_call_response(tool_call) is False

    def test_validate_tool_call_response_invalid_confidence(self, linker):
        """Test tool call validation with invalid confidence."""
        tool_call = {
            "name": "link_reference_to_object",
            "arguments": {
                "object": "activités",
                "agreement_analysis": "Test analysis",
                "confidence": 1.5  # Invalid confidence > 1
            }
        }
        
        assert linker._validate_tool_call_response(tool_call) is False

    def test_validate_tool_call_response_empty_string(self, linker):
        """Test tool call validation with empty string field."""
        tool_call = {
            "name": "link_reference_to_object",
            "arguments": {
                "object": "",  # Empty string
                "agreement_analysis": "Test analysis",
                "confidence": 0.95
            }
        }
        
        assert linker._validate_tool_call_response(tool_call) is False

    def test_create_linked_reference(self, linker, sample_located_references):
        """Test LinkedReference creation from tool call arguments."""
        ref = sample_located_references[0]
        arguments = {
            "object": "activités",
            "agreement_analysis": "Feminine plural agreement with activités mentioned before",
            "confidence": 0.95
        }
        
        linked_ref = linker._create_linked_reference(ref, arguments)
        
        assert isinstance(linked_ref, LinkedReference)
        assert linked_ref.reference_text == ref.reference_text
        assert linked_ref.source == ref.source
        assert linked_ref.object == "activités"
        assert linked_ref.agreement_analysis == "Feminine plural agreement with activités mentioned before"
        assert linked_ref.confidence == 0.95

    def test_link_references_success(self, linker, sample_located_references, sample_reconstructor_output, mock_client):
        """Test successful reference linking with mocked responses."""
        # Mock responses for each reference
        mock_responses = [
            self._create_mock_response("link_reference_to_object", {
                "object": "activités",
                "agreement_analysis": "Feminine plural agreement with activités",
                "confidence": 0.95
            }),
            self._create_mock_response("link_reference_to_object", {
                "object": "producteurs",
                "agreement_analysis": "Masculine plural defined by au sens du",
                "confidence": 0.98
            }),
            self._create_mock_response("link_reference_to_object", {
                "object": "la liste",
                "agreement_analysis": "Feminine singular agreement with liste mentionnée",
                "confidence": 0.92
            })
        ]
        
        mock_client.chat.complete.side_effect = mock_responses
        
        linked_refs = linker.link_references(sample_located_references, sample_reconstructor_output)
        
        assert len(linked_refs) == 3
        
        # Check first reference (DELETIONAL)
        assert linked_refs[0].reference_text == "aux 1° ou 2° du II"
        assert linked_refs[0].source == ReferenceSourceType.DELETIONAL
        assert linked_refs[0].object == "activités"
        
        # Check second reference (DEFINITIONAL)
        assert linked_refs[1].reference_text == "du 11 de l'article 3 du règlement (CE) n° 1107/2009"
        assert linked_refs[1].source == ReferenceSourceType.DEFINITIONAL
        assert linked_refs[1].object == "producteurs"
        
        # Check third reference (DEFINITIONAL)
        assert linked_refs[2].reference_text == "à l'article L. 253-5 du présent code"
        assert linked_refs[2].source == ReferenceSourceType.DEFINITIONAL
        assert linked_refs[2].object == "la liste"

    def test_link_references_with_failures(self, linker, sample_located_references, sample_reconstructor_output, mock_client):
        """Test reference linking with some failures."""
        # First call succeeds, second fails, third succeeds
        mock_responses = [
            self._create_mock_response("link_reference_to_object", {
                "object": "activités",
                "agreement_analysis": "Feminine plural agreement",
                "confidence": 0.95
            }),
            Exception("API Error"),  # This will cause an exception
            self._create_mock_response("link_reference_to_object", {
                "object": "la liste",
                "agreement_analysis": "Feminine singular agreement",
                "confidence": 0.92
            })
        ]
        
        mock_client.chat.complete.side_effect = mock_responses
        
        linked_refs = linker.link_references(sample_located_references, sample_reconstructor_output)
        
        # Should have 2 successful links despite 1 failure
        assert len(linked_refs) == 2
        assert linked_refs[0].object == "activités"
        assert linked_refs[1].object == "la liste"

    def test_link_references_empty_context(self, linker, mock_client):
        """Test reference linking with empty context."""
        # Reference with empty context
        ref = LocatedReference(
            reference_text="test reference",
            start_position=0,
            end_position=10,
            source=ReferenceSourceType.DELETIONAL,
            confidence=0.9
        )
        
        # Empty reconstructor output
        empty_output = ReconstructorOutput(
            deleted_or_replaced_text="",
            intermediate_after_state_text=""
        )
        
        linked_refs = linker.link_references([ref], empty_output)
        
        # Should return empty list due to no context
        assert len(linked_refs) == 0

    def test_link_references_invalid_input_types(self, linker):
        """Test reference linking with invalid input types."""
        with pytest.raises(ValueError, match="located_references must be a list"):
            linker.link_references("not a list", ReconstructorOutput("", ""))
        
        with pytest.raises(ValueError, match="reconstructor_output must be a ReconstructorOutput object"):
            linker.link_references([], "not a reconstructor output")

    def test_link_references_invalid_tool_call_response(self, linker, sample_located_references, sample_reconstructor_output, mock_client):
        """Test reference linking with invalid tool call response."""
        # Mock response with invalid arguments (missing required field)
        mock_response = self._create_mock_response("link_reference_to_object", {
            "object": "activités",
            # Missing agreement_analysis and confidence
        })
        
        mock_client.chat.complete.return_value = mock_response
        
        linked_refs = linker.link_references([sample_located_references[0]], sample_reconstructor_output)
        
        # Should return empty list due to invalid response
        assert len(linked_refs) == 0

    def test_tool_schema_validation(self, linker):
        """Test that the tool schema matches the expected structure from the specification."""
        schema = linker.tool_schema[0]
        
        # Verify it matches the specification exactly
        assert schema["type"] == "function"
        assert schema["function"]["name"] == "link_reference_to_object"
        assert schema["function"]["description"] == "Analyze French grammatical structure to link a legal reference to its object"
        
        parameters = schema["function"]["parameters"]
        assert parameters["type"] == "object"
        assert set(parameters["required"]) == {"object", "agreement_analysis", "confidence"}
        
        properties = parameters["properties"]
        assert properties["object"]["type"] == "string"
        assert properties["agreement_analysis"]["type"] == "string"
        assert properties["confidence"]["type"] == "number"

    def test_real_world_reference_patterns(self, linker, mock_client):
        """Test with real-world reference patterns from the legislative bill."""
        # Real examples from the bill
        references = [
            LocatedReference(
                reference_text="aux articles L. 254-6-2 et L. 254-6-3",
                start_position=6,
                end_position=43,
                source=ReferenceSourceType.DELETIONAL,
                confidence=0.99
            ),
            LocatedReference(
                reference_text="au sens du même 11",
                start_position=50,
                end_position=68,
                source=ReferenceSourceType.DEFINITIONAL,
                confidence=0.88
            )
        ]
        
        reconstructor_output = ReconstructorOutput(
            deleted_or_replaced_text="prévu aux articles L. 254-6-2 et L. 254-6-3",
            intermediate_after_state_text="de producteur au sens du même 11"
        )
        
        # Mock successful responses
        mock_responses = [
            self._create_mock_response("link_reference_to_object", {
                "object": "conseil",
                "agreement_analysis": "Masculine singular noun modified by the prévu construction",
                "confidence": 0.94
            }),
            self._create_mock_response("link_reference_to_object", {
                "object": "producteur",
                "agreement_analysis": "Masculine singular defined by au sens du construction",
                "confidence": 0.91
            })
        ]
        
        mock_client.chat.complete.side_effect = mock_responses
        
        linked_refs = linker.link_references(references, reconstructor_output)
        
        assert len(linked_refs) == 2
        assert linked_refs[0].object == "conseil"
        assert linked_refs[1].object == "producteur"
        assert all(ref.confidence > 0.9 for ref in linked_refs) 