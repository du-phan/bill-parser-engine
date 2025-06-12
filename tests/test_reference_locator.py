"""
Tests for the ReferenceLocator component.
"""

import pytest
from unittest.mock import Mock, patch
import json

from bill_parser_engine.core.reference_resolver.reference_locator import ReferenceLocator
from bill_parser_engine.core.reference_resolver.models import (
    LocatedReference,
    ReconstructorOutput,
    ReferenceSourceType,
)


class TestReferenceLocator:
    """Test cases for the ReferenceLocator component."""

    def setup_method(self):
        """Set up test fixtures."""
        self.locator = ReferenceLocator(api_key="test_key")

    def test_init(self):
        """Test ReferenceLocator initialization."""
        assert self.locator.client is not None
        assert self.locator.system_prompt is not None
        assert self.locator.min_confidence == 0.7
        assert "DELETIONAL" in self.locator.system_prompt
        assert "DEFINITIONAL" in self.locator.system_prompt

    def test_create_user_prompt(self):
        """Test user prompt creation."""
        fragments = {
            "DELETIONAL": "aux 1° ou 2° du II",
            "DEFINITIONAL": "à l'article L. 253-5"
        }
        
        prompt = self.locator._create_user_prompt(fragments)
        parsed = json.loads(prompt)
        
        assert parsed["deleted_or_replaced_text"] == "aux 1° ou 2° du II"
        assert parsed["intermediate_after_state_text"] == "à l'article L. 253-5"

    def test_validate_response_success(self):
        """Test response validation with valid content."""
        content = {
            "located_references": [
                {
                    "reference_text": "aux 1° ou 2° du II",
                    "start_position": 0,
                    "end_position": 18,
                    "source": "DELETIONAL",
                    "confidence": 0.95
                }
            ]
        }
        
        # Should not raise any exception
        self.locator._validate_response(content)

    def test_validate_response_missing_field(self):
        """Test response validation with missing required field."""
        content = {
            "invalid_field": []
        }
        
        with pytest.raises(ValueError, match="Missing required field: located_references"):
            self.locator._validate_response(content)

    def test_validate_response_invalid_type(self):
        """Test response validation with invalid list type."""
        content = {
            "located_references": "not a list"
        }
        
        with pytest.raises(ValueError, match="located_references must be a list"):
            self.locator._validate_response(content)

    def test_validate_reference_positioning_success(self):
        """Test reference positioning validation with valid data."""
        ref_data = {
            "reference_text": "aux 1°",
            "start_position": 0,
            "end_position": 6,
            "source": "DELETIONAL",
            "confidence": 0.95
        }
        fragments = {
            "DELETIONAL": "aux 1° du II",
            "DEFINITIONAL": "autre texte"
        }
        
        result = self.locator._validate_reference_positioning(ref_data, fragments)
        assert result is True

    def test_validate_reference_positioning_missing_field(self):
        """Test reference positioning validation with missing field."""
        ref_data = {
            "reference_text": "aux 1°",
            "start_position": 0,
            "end_position": 6,
            # Missing source
            "confidence": 0.95
        }
        fragments = {
            "DELETIONAL": "aux 1° du II",
            "DEFINITIONAL": "autre texte"
        }
        
        result = self.locator._validate_reference_positioning(ref_data, fragments)
        assert result is False

    def test_validate_reference_positioning_invalid_positions(self):
        """Test reference positioning validation with invalid positions."""
        ref_data = {
            "reference_text": "aux 1°",
            "start_position": 5,
            "end_position": 3,  # End before start
            "source": "DELETIONAL",
            "confidence": 0.95
        }
        fragments = {
            "DELETIONAL": "aux 1° du II",
            "DEFINITIONAL": "autre texte"
        }
        
        result = self.locator._validate_reference_positioning(ref_data, fragments)
        assert result is False

    def test_validate_reference_positioning_text_mismatch(self):
        """Test reference positioning validation with text mismatch."""
        ref_data = {
            "reference_text": "wrong text",
            "start_position": 0,
            "end_position": 6,
            "source": "DELETIONAL",
            "confidence": 0.95
        }
        fragments = {
            "DELETIONAL": "aux 1° du II",
            "DEFINITIONAL": "autre texte"
        }
        
        result = self.locator._validate_reference_positioning(ref_data, fragments)
        assert result is False

    def test_validate_reference_positioning_position_out_of_bounds(self):
        """Test reference positioning validation with position out of bounds."""
        ref_data = {
            "reference_text": "aux 1°",
            "start_position": 0,
            "end_position": 50,  # Beyond text length
            "source": "DELETIONAL",
            "confidence": 0.95
        }
        fragments = {
            "DELETIONAL": "aux 1° du II",
            "DEFINITIONAL": "autre texte"
        }
        
        result = self.locator._validate_reference_positioning(ref_data, fragments)
        assert result is False

    def test_create_located_reference(self):
        """Test LocatedReference creation from valid data."""
        ref_data = {
            "reference_text": "aux 1° ou 2° du II",
            "start_position": 5,
            "end_position": 23,
            "source": "DELETIONAL",
            "confidence": 0.98
        }
        
        result = self.locator._create_located_reference(ref_data)
        
        assert isinstance(result, LocatedReference)
        assert result.reference_text == "aux 1° ou 2° du II"
        assert result.start_position == 5
        assert result.end_position == 23
        assert result.source == ReferenceSourceType.DELETIONAL
        assert result.confidence == 0.98

    def test_filter_by_confidence(self):
        """Test confidence filtering."""
        refs = [
            LocatedReference("ref1", 0, 5, ReferenceSourceType.DELETIONAL, 0.8),
            LocatedReference("ref2", 6, 10, ReferenceSourceType.DEFINITIONAL, 0.6),  # Below threshold
            LocatedReference("ref3", 11, 15, ReferenceSourceType.DELETIONAL, 0.9),
        ]
        
        filtered = self.locator._filter_by_confidence(refs, 0.7)
        
        assert len(filtered) == 2
        assert filtered[0].reference_text == "ref1"
        assert filtered[1].reference_text == "ref3"

    def test_locate_input_validation(self):
        """Test locate method input validation."""
        with pytest.raises(ValueError, match="Input must be a ReconstructorOutput object"):
            self.locator.locate("invalid input")

    @patch('bill_parser_engine.core.reference_resolver.reference_locator.Mistral')
    def test_locate_success_with_references(self, mock_mistral_class):
        """Test successful reference location with found references."""
        # Mock Mistral client and response
        mock_client = Mock()
        mock_mistral_class.return_value = mock_client
        
        mock_response = Mock()
        mock_response.choices = [Mock()]
        mock_response.choices[0].message.content = json.dumps({
            "located_references": [
                {
                    "reference_text": "aux 1° ou 2° du II",
                    "start_position": 50,
                    "end_position": 68,
                    "source": "DELETIONAL",
                    "confidence": 0.98
                },
                {
                    "reference_text": "à l'article L. 253-5",
                    "start_position": 9,
                    "end_position": 29,
                    "source": "DEFINITIONAL",
                    "confidence": 0.97
                }
            ]
        })
        mock_client.chat.complete.return_value = mock_response
        
        # Create test data
        reconstructor_output = ReconstructorOutput(
            deleted_or_replaced_text="incompatible avec celui des activités mentionnées aux 1° ou 2° du II",
            intermediate_after_state_text="interdit à l'article L. 253-5 du présent code"
        )
        
        # Reinitialize with mocked client
        locator = ReferenceLocator(api_key="test_key")
        result = locator.locate(reconstructor_output)
        
        assert len(result) == 2
        assert isinstance(result[0], LocatedReference)
        assert result[0].reference_text == "aux 1° ou 2° du II"
        assert result[0].source == ReferenceSourceType.DELETIONAL
        assert result[1].reference_text == "à l'article L. 253-5"
        assert result[1].source == ReferenceSourceType.DEFINITIONAL

    @patch('bill_parser_engine.core.reference_resolver.reference_locator.Mistral')
    def test_locate_success_no_references(self, mock_mistral_class):
        """Test successful reference location with no references found."""
        # Mock Mistral client and response
        mock_client = Mock()
        mock_mistral_class.return_value = mock_client
        
        mock_response = Mock()
        mock_response.choices = [Mock()]
        mock_response.choices[0].message.content = json.dumps({
            "located_references": []
        })
        mock_client.chat.complete.return_value = mock_response
        
        # Create test data
        reconstructor_output = ReconstructorOutput(
            deleted_or_replaced_text="Les modalités sont fixées par décret.",
            intermediate_after_state_text="Les modalités sont fixées par arrêté."
        )
        
        # Reinitialize with mocked client
        locator = ReferenceLocator(api_key="test_key")
        result = locator.locate(reconstructor_output)
        
        assert len(result) == 0

    @patch('bill_parser_engine.core.reference_resolver.reference_locator.Mistral')
    def test_locate_json_parsing_error(self, mock_mistral_class):
        """Test reference location with JSON parsing error."""
        # Mock Mistral client with invalid JSON response
        mock_client = Mock()
        mock_mistral_class.return_value = mock_client
        
        mock_response = Mock()
        mock_response.choices = [Mock()]
        mock_response.choices[0].message.content = "invalid json"
        mock_client.chat.complete.return_value = mock_response
        
        # Create test data
        reconstructor_output = ReconstructorOutput(
            deleted_or_replaced_text="test text",
            intermediate_after_state_text="test text"
        )
        
        # Reinitialize with mocked client
        locator = ReferenceLocator(api_key="test_key")
        result = locator.locate(reconstructor_output)
        
        assert result == []

    @patch('bill_parser_engine.core.reference_resolver.reference_locator.Mistral')
    def test_locate_with_invalid_positioning(self, mock_mistral_class):
        """Test reference location filtering out invalid positioning."""
        # Mock Mistral client and response with invalid positioning
        mock_client = Mock()
        mock_mistral_class.return_value = mock_client
        
        mock_response = Mock()
        mock_response.choices = [Mock()]
        mock_response.choices[0].message.content = json.dumps({
            "located_references": [
                {
                    "reference_text": "wrong text",  # This will fail validation
                    "start_position": 0,
                    "end_position": 10,
                    "source": "DELETIONAL",
                    "confidence": 0.98
                },
                {
                    "reference_text": "test text",  # This should pass validation
                    "start_position": 0,
                    "end_position": 9,
                    "source": "DELETIONAL",
                    "confidence": 0.97
                }
            ]
        })
        mock_client.chat.complete.return_value = mock_response
        
        # Create test data
        reconstructor_output = ReconstructorOutput(
            deleted_or_replaced_text="test text here",
            intermediate_after_state_text="other text"
        )
        
        # Reinitialize with mocked client
        locator = ReferenceLocator(api_key="test_key")
        result = locator.locate(reconstructor_output)
        
        # Only the valid reference should be returned
        assert len(result) == 1
        assert result[0].reference_text == "test text"

    def test_locate_real_example(self):
        """Test with real example from legislative bill (mock test)."""
        # This test uses real data structure but mocked LLM response
        # for predictable testing
        with patch('bill_parser_engine.core.reference_resolver.reference_locator.Mistral') as mock_mistral_class:
            mock_client = Mock()
            mock_mistral_class.return_value = mock_client
            
            mock_response = Mock()
            mock_response.choices = [Mock()]
            mock_response.choices[0].message.content = json.dumps({
                "located_references": [
                    {
                        "reference_text": "aux articles L. 254-6-2 et L. 254-6-3",
                        "start_position": 6,
                        "end_position": 43,
                        "source": "DELETIONAL",
                        "confidence": 0.99
                    }
                ]
            })
            mock_client.chat.complete.return_value = mock_response
            
            # Real example from the legislative bill
            reconstructor_output = ReconstructorOutput(
                deleted_or_replaced_text="prévu aux articles L. 254-6-2 et L. 254-6-3",
                intermediate_after_state_text="à l'utilisation des produits phytopharmaceutiques"
            )
            
            locator = ReferenceLocator(api_key="test_key")
            result = locator.locate(reconstructor_output)
            
            assert len(result) == 1
            assert result[0].reference_text == "aux articles L. 254-6-2 et L. 254-6-3"
            assert result[0].source == ReferenceSourceType.DELETIONAL
            assert result[0].confidence == 0.99 