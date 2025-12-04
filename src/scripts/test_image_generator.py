"""Tests for the image_generator module."""

import os
from unittest.mock import MagicMock, Mock, patch

import pytest

from scripts.image_generator import (
    build_prompt_from_attributes,
    generate_cover_image,
    generate_cover_image_with_fallback,
)


class TestBuildPromptFromAttributes:
    """Tests for build_prompt_from_attributes function."""

    def test_empty_attributes_returns_default_prompt(self):
        """Test that empty attributes return a default prompt."""
        result = build_prompt_from_attributes({})
        assert result == "Abstract colorful album cover art with musical notes and vibrant energy"

    def test_none_attributes_returns_default_prompt(self):
        """Test that None attributes return a default prompt."""
        result = build_prompt_from_attributes(None)
        assert result == "Abstract colorful album cover art with musical notes and vibrant energy"

    def test_single_mood_attribute(self):
        """Test prompt building with only mood attribute."""
        attributes = {"mood": "happy"}
        result = build_prompt_from_attributes(attributes)

        assert "happy mood" in result
        assert "abstract album cover" in result.lower()
        assert "bold colors" in result.lower()

    def test_single_genre_attribute(self):
        """Test prompt building with only genre attribute."""
        attributes = {"genre": "jazz"}
        result = build_prompt_from_attributes(attributes)

        assert "jazz music" in result
        assert "abstract album cover" in result.lower()

    def test_single_energy_attribute(self):
        """Test prompt building with only energy attribute."""
        attributes = {"energy": "high"}
        result = build_prompt_from_attributes(attributes)

        assert "high energy" in result
        assert "abstract album cover" in result.lower()

    def test_single_artist_attribute(self):
        """Test prompt building with only artist attribute."""
        attributes = {"artist": "The Beatles"}
        result = build_prompt_from_attributes(attributes)

        assert "inspired by The Beatles" in result
        assert "abstract album cover" in result.lower()

    def test_all_attributes_combined(self):
        """Test prompt building with all attributes."""
        attributes = {
            "mood": "melancholic",
            "genre": "indie rock",
            "energy": "medium",
            "artist": "Radiohead"
        }
        result = build_prompt_from_attributes(attributes)

        assert "melancholic mood" in result
        assert "indie rock music" in result
        assert "medium energy" in result
        assert "inspired by Radiohead" in result
        assert "abstract album cover" in result.lower()
        assert "bold colors" in result.lower()
        assert "digital art" in result.lower()

    def test_partial_attributes(self):
        """Test prompt building with subset of attributes."""
        attributes = {
            "mood": "energetic",
            "genre": "electronic"
        }
        result = build_prompt_from_attributes(attributes)

        assert "energetic mood" in result
        assert "electronic music" in result
        assert "abstract album cover" in result.lower()

    def test_empty_string_values_ignored(self):
        """Test that empty string values are ignored."""
        attributes = {
            "mood": "",
            "genre": "pop",
            "energy": "   ",
            "artist": "Taylor Swift"
        }
        result = build_prompt_from_attributes(attributes)

        assert "mood" not in result
        assert "pop music" in result
        assert "energy" not in result
        assert "inspired by Taylor Swift" in result

    def test_whitespace_trimmed(self):
        """Test that whitespace is properly trimmed from attributes."""
        attributes = {
            "mood": "  chill  ",
            "genre": " hip hop ",
        }
        result = build_prompt_from_attributes(attributes)

        assert "chill mood" in result
        assert "hip hop music" in result

    def test_unknown_attributes_ignored(self):
        """Test that unknown attributes are ignored."""
        attributes = {
            "mood": "happy",
            "unknown_field": "should be ignored",
            "another_field": "also ignored"
        }
        result = build_prompt_from_attributes(attributes)

        assert "happy mood" in result
        assert "unknown_field" not in result
        assert "should be ignored" not in result


class TestGenerateCoverImage:
    """Tests for generate_cover_image function."""

    @patch('scripts.image_generator._get_openai_client')
    def test_successful_generation_with_prompt(self, mock_get_client):
        """Test successful image generation with custom prompt."""
        # Mock OpenAI client and response
        mock_client = MagicMock()
        mock_response = Mock()
        mock_response.data = [Mock(url="https://example.com/image.png")]
        mock_client.images.generate.return_value = mock_response
        mock_get_client.return_value = mock_client

        result = generate_cover_image(prompt="A sunset over mountains")

        assert result == "https://example.com/image.png"
        mock_client.images.generate.assert_called_once_with(
            model="dall-e-3",
            prompt="A sunset over mountains",
            size="1024x1024",
            quality="standard",
            n=1,
        )

    @patch('scripts.image_generator._get_openai_client')
    def test_successful_generation_with_attributes(self, mock_get_client):
        """Test successful image generation with attributes."""
        mock_client = MagicMock()
        mock_response = Mock()
        mock_response.data = [Mock(url="https://example.com/image2.png")]
        mock_client.images.generate.return_value = mock_response
        mock_get_client.return_value = mock_client

        attributes = {"mood": "happy", "genre": "pop"}
        result = generate_cover_image(attributes=attributes)

        assert result == "https://example.com/image2.png"
        # Verify the prompt was built from attributes
        call_args = mock_client.images.generate.call_args
        assert "happy mood" in call_args[1]["prompt"]
        assert "pop music" in call_args[1]["prompt"]

    @patch('scripts.image_generator._get_openai_client')
    def test_custom_size_parameter(self, mock_get_client):
        """Test image generation with custom size."""
        mock_client = MagicMock()
        mock_response = Mock()
        mock_response.data = [Mock(url="https://example.com/image.png")]
        mock_client.images.generate.return_value = mock_response
        mock_get_client.return_value = mock_client

        generate_cover_image(
            prompt="Test prompt",
            size="1792x1024"
        )

        call_args = mock_client.images.generate.call_args
        assert call_args[1]["size"] == "1792x1024"

    @patch('scripts.image_generator._get_openai_client')
    def test_custom_quality_parameter(self, mock_get_client):
        """Test image generation with custom quality."""
        mock_client = MagicMock()
        mock_response = Mock()
        mock_response.data = [Mock(url="https://example.com/image.png")]
        mock_client.images.generate.return_value = mock_response
        mock_get_client.return_value = mock_client

        generate_cover_image(
            prompt="Test prompt",
            quality="hd"
        )

        call_args = mock_client.images.generate.call_args
        assert call_args[1]["quality"] == "hd"

    @patch('scripts.image_generator._get_openai_client')
    def test_invalid_size_defaults_to_1024x1024(self, mock_get_client):
        """Test that invalid size defaults to 1024x1024."""
        mock_client = MagicMock()
        mock_response = Mock()
        mock_response.data = [Mock(url="https://example.com/image.png")]
        mock_client.images.generate.return_value = mock_response
        mock_get_client.return_value = mock_client

        generate_cover_image(
            prompt="Test prompt",
            size="invalid_size"
        )

        call_args = mock_client.images.generate.call_args
        assert call_args[1]["size"] == "1024x1024"

    @patch('scripts.image_generator._get_openai_client')
    def test_invalid_quality_defaults_to_standard(self, mock_get_client):
        """Test that invalid quality defaults to standard."""
        mock_client = MagicMock()
        mock_response = Mock()
        mock_response.data = [Mock(url="https://example.com/image.png")]
        mock_client.images.generate.return_value = mock_response
        mock_get_client.return_value = mock_client

        generate_cover_image(
            prompt="Test prompt",
            quality="ultra_hd"
        )

        call_args = mock_client.images.generate.call_args
        assert call_args[1]["quality"] == "standard"

    def test_empty_prompt_raises_value_error(self):
        """Test that empty prompt raises ValueError."""
        with pytest.raises(ValueError, match="Prompt cannot be empty"):
            generate_cover_image(prompt="   ")

    def test_no_prompt_or_attributes_raises_value_error(self):
        """Test that missing both prompt and attributes raises ValueError."""
        with pytest.raises(ValueError, match="Either prompt or attributes must be provided"):
            generate_cover_image()

    @patch('scripts.image_generator._get_openai_client')
    def test_client_initialization_failure_returns_none(self, mock_get_client):
        """Test that failed client initialization returns None."""
        mock_get_client.return_value = None

        result = generate_cover_image(prompt="Test prompt")

        assert result is None

    @patch('scripts.image_generator._get_openai_client')
    def test_openai_error_returns_none(self, mock_get_client):
        """Test that OpenAI API errors return None."""
        from openai import OpenAIError

        mock_client = MagicMock()
        mock_client.images.generate.side_effect = OpenAIError("API error")
        mock_get_client.return_value = mock_client

        result = generate_cover_image(prompt="Test prompt")

        assert result is None

    @patch('scripts.image_generator._get_openai_client')
    def test_empty_response_data_returns_none(self, mock_get_client):
        """Test that empty response data returns None."""
        mock_client = MagicMock()
        mock_response = Mock()
        mock_response.data = []
        mock_client.images.generate.return_value = mock_response
        mock_get_client.return_value = mock_client

        result = generate_cover_image(prompt="Test prompt")

        assert result is None

    @patch('scripts.image_generator._get_openai_client')
    def test_unexpected_error_returns_none(self, mock_get_client):
        """Test that unexpected errors return None."""
        mock_client = MagicMock()
        mock_client.images.generate.side_effect = Exception("Unexpected error")
        mock_get_client.return_value = mock_client

        result = generate_cover_image(prompt="Test prompt")

        assert result is None

    @patch('scripts.image_generator._get_openai_client')
    def test_prompt_takes_precedence_over_attributes(self, mock_get_client):
        """Test that explicit prompt takes precedence over attributes."""
        mock_client = MagicMock()
        mock_response = Mock()
        mock_response.data = [Mock(url="https://example.com/image.png")]
        mock_client.images.generate.return_value = mock_response
        mock_get_client.return_value = mock_client

        attributes = {"mood": "happy", "genre": "pop"}
        result = generate_cover_image(
            prompt="Custom explicit prompt",
            attributes=attributes
        )

        assert result == "https://example.com/image.png"
        call_args = mock_client.images.generate.call_args
        assert call_args[1]["prompt"] == "Custom explicit prompt"
        assert "happy" not in call_args[1]["prompt"]


class TestGenerateCoverImageWithFallback:
    """Tests for generate_cover_image_with_fallback function."""

    @patch('scripts.image_generator.generate_cover_image')
    def test_successful_generation_with_prompt(self, mock_generate):
        """Test successful generation with custom prompt."""
        mock_generate.return_value = "https://example.com/image.png"

        result = generate_cover_image_with_fallback(prompt="Test prompt")

        assert result["success"] is True
        assert result["image_url"] == "https://example.com/image.png"
        assert result["error"] is None
        assert result["prompt_used"] == "Test prompt"

    @patch('scripts.image_generator.generate_cover_image')
    def test_successful_generation_with_attributes(self, mock_generate):
        """Test successful generation with attributes."""
        mock_generate.return_value = "https://example.com/image.png"

        attributes = {"mood": "happy", "genre": "jazz"}
        result = generate_cover_image_with_fallback(attributes=attributes)

        assert result["success"] is True
        assert result["image_url"] == "https://example.com/image.png"
        assert result["error"] is None
        assert "happy mood" in result["prompt_used"]
        assert "jazz music" in result["prompt_used"]

    @patch('scripts.image_generator.generate_cover_image')
    def test_failed_generation_returns_error(self, mock_generate):
        """Test that failed generation returns error."""
        mock_generate.return_value = None

        result = generate_cover_image_with_fallback(prompt="Test prompt")

        assert result["success"] is False
        assert result["image_url"] is None
        assert result["error"] == "Image generation failed. Please try again."
        assert result["prompt_used"] == "Test prompt"

    def test_no_prompt_or_attributes_returns_error(self):
        """Test that missing both prompt and attributes returns error."""
        result = generate_cover_image_with_fallback()

        assert result["success"] is False
        assert result["image_url"] is None
        assert result["error"] == "No prompt or attributes provided"
        assert result["prompt_used"] is None

    @patch('scripts.image_generator.generate_cover_image')
    def test_value_error_captured_in_result(self, mock_generate):
        """Test that ValueError is captured and returned in result."""
        mock_generate.side_effect = ValueError("Invalid input")

        result = generate_cover_image_with_fallback(prompt="Test")

        assert result["success"] is False
        assert result["image_url"] is None
        assert "Invalid input" in result["error"]

    @patch('scripts.image_generator.generate_cover_image')
    def test_unexpected_error_captured_in_result(self, mock_generate):
        """Test that unexpected errors are captured."""
        mock_generate.side_effect = RuntimeError("Unexpected problem")

        result = generate_cover_image_with_fallback(prompt="Test")

        assert result["success"] is False
        assert result["image_url"] is None
        assert "Unexpected" in result["error"]

    @patch('scripts.image_generator.generate_cover_image')
    def test_whitespace_prompt_trimmed(self, mock_generate):
        """Test that whitespace in prompt is trimmed."""
        mock_generate.return_value = "https://example.com/image.png"

        result = generate_cover_image_with_fallback(prompt="  Test prompt  ")

        assert result["prompt_used"] == "Test prompt"

    @patch('scripts.image_generator.generate_cover_image')
    def test_prompt_takes_precedence_over_attributes(self, mock_generate):
        """Test that prompt takes precedence over attributes."""
        mock_generate.return_value = "https://example.com/image.png"

        attributes = {"mood": "happy"}
        result = generate_cover_image_with_fallback(
            prompt="Explicit prompt",
            attributes=attributes
        )

        assert result["prompt_used"] == "Explicit prompt"
        assert "happy" not in result["prompt_used"]


class TestGetOpenAIClient:
    """Tests for _get_openai_client function."""

    @patch.dict(os.environ, {}, clear=True)
    @patch('scripts.image_generator.OpenAI')
    def test_missing_api_key_returns_none(self, mock_openai):
        """Test that missing API key returns None."""
        from scripts.image_generator import _get_openai_client

        result = _get_openai_client()

        assert result is None
        mock_openai.assert_not_called()

    @patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"})
    @patch('scripts.image_generator.OpenAI')
    def test_valid_api_key_returns_client(self, mock_openai):
        """Test that valid API key returns client instance."""
        from scripts.image_generator import _get_openai_client

        mock_client = MagicMock()
        mock_openai.return_value = mock_client

        result = _get_openai_client()

        assert result == mock_client
        mock_openai.assert_called_once_with(api_key="test-key")

    @patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"})
    @patch('scripts.image_generator.OpenAI')
    def test_client_initialization_error_returns_none(self, mock_openai):
        """Test that client initialization errors return None."""
        from scripts.image_generator import _get_openai_client

        mock_openai.side_effect = Exception("Initialization failed")

        result = _get_openai_client()

        assert result is None
