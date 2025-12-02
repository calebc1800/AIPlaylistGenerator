"""Generate playlist cover images using OpenAI's DALL-E API.

This script provides utilities to generate AI-powered cover images for playlists
based on either explicit user prompts or automatically derived attributes.
"""

import logging
import os
from typing import Dict, Optional

from openai import OpenAI, OpenAIError

logger = logging.getLogger(__name__)


def _get_openai_client() -> Optional[OpenAI]:
    """Initialize and return an OpenAI client instance."""
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        logger.error("OPENAI_API_KEY environment variable not set")
        return None

    try:
        return OpenAI(api_key=api_key)
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.exception("Failed to initialize OpenAI client: %s", exc)
        return None


def build_prompt_from_attributes(attributes: Dict[str, str]) -> str:
    """
    Build a DALL-E prompt from playlist attributes.

    Args:
        attributes: Dictionary containing playlist attributes like mood, genre, energy, artist

    Returns:
        A descriptive prompt suitable for image generation
    """
    if not attributes:
        return "Abstract colorful album cover art with musical notes and vibrant energy"

    parts = []

    # Extract key attributes
    mood = attributes.get("mood", "").strip()
    genre = attributes.get("genre", "").strip()
    energy = attributes.get("energy", "").strip()
    artist = attributes.get("artist", "").strip()

    # Build descriptive prompt
    if mood:
        parts.append(f"{mood} mood")

    if genre:
        parts.append(f"{genre} music")

    if energy:
        parts.append(f"{energy} energy")

    if artist:
        parts.append(f"inspired by {artist}")

    # Combine parts into a coherent prompt
    if parts:
        description = ", ".join(parts)
        prompt = (
            f"Create an abstract album cover artwork representing {description}. "
            f"Use bold colors, modern design, and musical elements. "
            f"Style: digital art, vibrant, professional album cover"
        )
    else:
        prompt = (
            "Abstract colorful album cover art with musical notes, "
            "vibrant energy, modern design, digital art style"
        )

    return prompt


def generate_cover_image(
    prompt: Optional[str] = None,
    attributes: Optional[Dict[str, str]] = None,
    size: str = "1024x1024",
    quality: str = "standard",
) -> Optional[str]:
    """
    Generate a playlist cover image using OpenAI's DALL-E API.

    Args:
        prompt: Custom user prompt for image generation. If None, attributes are used.
        attributes: Playlist attributes dict (mood, genre, energy, artist).
                   Only used if prompt is None.
        size: Image size. Options: "1024x1024", "1024x1792", "1792x1024"
        quality: Image quality. Options: "standard", "hd"

    Returns:
        URL of the generated image, or None if generation failed

    Raises:
        ValueError: If neither prompt nor attributes are provided
    """
    # Determine the prompt to use
    if prompt:
        image_prompt = prompt.strip()
        if not image_prompt:
            raise ValueError("Prompt cannot be empty")
    elif attributes:
        image_prompt = build_prompt_from_attributes(attributes)
    else:
        raise ValueError("Either prompt or attributes must be provided")

    # Validate size
    valid_sizes = {"1024x1024", "1024x1792", "1792x1024"}
    if size not in valid_sizes:
        logger.warning("Invalid size '%s', using default '1024x1024'", size)
        size = "1024x1024"

    # Validate quality
    valid_qualities = {"standard", "hd"}
    if quality not in valid_qualities:
        logger.warning("Invalid quality '%s', using default 'standard'", quality)
        quality = "standard"

    # Initialize OpenAI client
    client = _get_openai_client()
    if not client:
        logger.error("Cannot generate image: OpenAI client initialization failed")
        return None

    try:
        logger.info(
            "Generating cover image with prompt: '%s' (size=%s, quality=%s)",
            image_prompt[:100],
            size,
            quality,
        )

        # Call DALL-E API
        response = client.images.generate(
            model="dall-e-3",
            prompt=image_prompt,
            size=size,
            quality=quality,
            n=1,
        )

        # Extract image URL
        if response.data and len(response.data) > 0:
            image_url = response.data[0].url
            logger.info("Successfully generated image: %s", image_url)
            return image_url

        logger.error("No image data returned from DALL-E API")
        return None

    except OpenAIError as exc:
        logger.error("OpenAI API error during image generation: %s", exc)
        return None
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.exception("Unexpected error during image generation: %s", exc)
        return None


def generate_cover_image_with_fallback(
    prompt: Optional[str] = None,
    attributes: Optional[Dict[str, str]] = None,
) -> Dict[str, object]:
    """
    Generate a playlist cover with comprehensive error handling.

    Args:
        prompt: Custom user prompt for image generation
        attributes: Playlist attributes (mood, genre, energy, artist)

    Returns:
        Dictionary with keys:
        - success: bool indicating if generation succeeded
        - image_url: str URL of generated image (if successful)
        - error: str error message (if failed)
        - prompt_used: str the actual prompt used for generation
    """
    result: Dict[str, object] = {
        "success": False,
        "image_url": None,
        "error": None,
        "prompt_used": None,
    }

    try:
        # Determine prompt
        if prompt:
            final_prompt = prompt.strip()
        elif attributes:
            final_prompt = build_prompt_from_attributes(attributes)
        else:
            result["error"] = "No prompt or attributes provided"
            return result

        result["prompt_used"] = final_prompt

        # Generate image
        image_url = generate_cover_image(prompt=final_prompt)

        if image_url:
            result["success"] = True
            result["image_url"] = image_url
        else:
            result["error"] = "Image generation failed. Please try again."

    except ValueError as exc:
        result["error"] = str(exc)
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.exception("Unexpected error in generate_cover_image_with_fallback: %s", exc)
        result["error"] = f"Unexpected error: {exc}"

    return result
