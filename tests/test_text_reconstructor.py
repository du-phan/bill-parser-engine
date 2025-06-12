"""
Tests for the TextReconstructor component.
"""

import pytest
from unittest.mock import Mock, patch
import json

from bill_parser_engine.core.reference_resolver.text_reconstructor import TextReconstructor
from bill_parser_engine.core.reference_resolver.models import (
    BillChunk,
    ReconstructorOutput,
    TargetArticle,
    TargetOperationType,
)


class TestTextReconstructor:
    """Test cases for the TextReconstructor component."""

    def setup_method(self):
        """Set up test fixtures."""
        self.reconstructor = TextReconstructor(api_key="test_key")

    def test_init(self):
        """Test TextReconstructor initialization."""
        assert self.reconstructor.client is not None
        assert self.reconstructor.system_prompt is not None
        assert "mechanically apply the amendment" in self.reconstructor.system_prompt

    def test_create_user_prompt(self):
        """Test user prompt creation."""
        original = "Original article text"
        amendment = "Amendment instruction"
        
        prompt = self.reconstructor._create_user_prompt(original, amendment)
        parsed = json.loads(prompt)
        
        assert parsed["original_article"] == original
        assert parsed["amendment"] == amendment

    def test_validate_response_success(self):
        """Test response validation with valid content."""
        content = {
            "deleted_or_replaced_text": "some text",
            "intermediate_after_state_text": "new text"
        }
        
        # Should not raise any exception
        self.reconstructor._validate_response(content)

    def test_validate_response_missing_field(self):
        """Test response validation with missing required field."""
        content = {
            "deleted_or_replaced_text": "some text"
            # Missing intermediate_after_state_text
        }
        
        with pytest.raises(ValueError, match="Missing required field"):
            self.reconstructor._validate_response(content)

    def test_validate_deleted_text_exists(self):
        """Test deleted text validation."""
        original = "This is the original article with some text"
        deleted_text = "some text"
        
        # Should not raise exception (text exists)
        self.reconstructor._validate_deleted_text_exists(deleted_text, original)



    @patch('bill_parser_engine.core.reference_resolver.text_reconstructor.Mistral')
    def test_reconstruct_success(self, mock_mistral_class):
        """Test successful reconstruction."""
        # Mock Mistral client and response
        mock_client = Mock()
        mock_mistral_class.return_value = mock_client
        
        mock_response = Mock()
        mock_response.choices = [Mock()]
        mock_response.choices[0].message.content = json.dumps({
            "deleted_or_replaced_text": "old text",
            "intermediate_after_state_text": "new article text"
        })
        mock_client.chat.complete.return_value = mock_response
        
        # Create test data
        target_article = TargetArticle(
            operation_type=TargetOperationType.MODIFY,
            code="test code",
            article="L. 123",
            confidence=0.9,
            raw_text="test"
        )
        
        chunk = BillChunk(
            text="Replace 'old text' with 'new text'",
            titre_text="TITRE I",
            article_label="Article 1",
            article_introductory_phrase=None,
            major_subdivision_label=None,
            major_subdivision_introductory_phrase=None,
            numbered_point_label=None,
            hierarchy_path=["TITRE I", "Article 1"],
            chunk_id="test_chunk",
            start_pos=0,
            end_pos=10,
            target_article=target_article
        )
        
        original_article = "This is the original article with old text"
        
        # Reinitialize with mocked client
        reconstructor = TextReconstructor(api_key="test_key")
        result = reconstructor.reconstruct(original_article, chunk)
        
        assert isinstance(result, ReconstructorOutput)
        assert result.deleted_or_replaced_text == "old text"
        assert result.intermediate_after_state_text == "new article text"

    def test_reconstruct_missing_target_article(self):
        """Test reconstruction with missing target article."""
        chunk = BillChunk(
            text="Amendment",
            titre_text="TITRE I",
            article_label="Article 1",
            article_introductory_phrase=None,
            major_subdivision_label=None,
            major_subdivision_introductory_phrase=None,
            numbered_point_label=None,
            hierarchy_path=["TITRE I", "Article 1"],
            chunk_id="test_chunk",
            start_pos=0,
            end_pos=10,
            target_article=None  # Missing target article
        )
        
        with pytest.raises(ValueError, match="BillChunk must have a target_article"):
            self.reconstructor.reconstruct("original", chunk)

    def test_reconstruct_modify_empty_article(self):
        """Test reconstruction with MODIFY operation on empty article."""
        target_article = TargetArticle(
            operation_type=TargetOperationType.MODIFY,
            code="test code",
            article="L. 123",
            confidence=0.9,
            raw_text="test"
        )
        
        chunk = BillChunk(
            text="Amendment",
            titre_text="TITRE I",
            article_label="Article 1",
            article_introductory_phrase=None,
            major_subdivision_label=None,
            major_subdivision_introductory_phrase=None,
            numbered_point_label=None,
            hierarchy_path=["TITRE I", "Article 1"],
            chunk_id="test_chunk",
            start_pos=0,
            end_pos=10,
            target_article=target_article
        )
        
        with pytest.raises(ValueError, match="Cannot modify empty article"):
            self.reconstructor.reconstruct("", chunk)  # Empty original article

    @patch('bill_parser_engine.core.reference_resolver.text_reconstructor.Mistral')
    def test_reconstruct_json_error(self, mock_mistral_class):
        """Test reconstruction with JSON parsing error."""
        # Mock Mistral client with invalid JSON response
        mock_client = Mock()
        mock_mistral_class.return_value = mock_client
        
        mock_response = Mock()
        mock_response.choices = [Mock()]
        mock_response.choices[0].message.content = "invalid json"
        mock_client.chat.complete.return_value = mock_response
        
        target_article = TargetArticle(
            operation_type=TargetOperationType.MODIFY,
            code="test code",
            article="L. 123",
            confidence=0.9,
            raw_text="test"
        )
        
        chunk = BillChunk(
            text="Amendment",
            titre_text="TITRE I",
            article_label="Article 1",
            article_introductory_phrase=None,
            major_subdivision_label=None,
            major_subdivision_introductory_phrase=None,
            numbered_point_label=None,
            hierarchy_path=["TITRE I", "Article 1"],
            chunk_id="test_chunk",
            start_pos=0,
            end_pos=10,
            target_article=target_article
        )
        
        # Should raise RuntimeError instead of returning fallback output
        reconstructor = TextReconstructor(api_key="test_key")
        with pytest.raises(RuntimeError, match="TextReconstructor failed to parse API response"):
            reconstructor.reconstruct("original article", chunk) 