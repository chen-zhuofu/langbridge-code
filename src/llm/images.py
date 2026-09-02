"""Local image attachments and provider request conversion.

Session traces keep only validated local paths. Image bytes are converted to
data URLs on a copied request immediately before the provider call, so logs and
the in-memory context never retain base64 payloads.
"""
from __future__ import annotations

import base64
import copy
from pathlib import Path

MAX_IMAGE_BYTES = 20 * 1024 * 1024
MAX_IMAGES_PER_MESSAGE = 4


class ImageAttachmentError(ValueError):
    """Raised when an attachment is missing, too large, or not a supported image."""


def _mime_type(data: bytes) -> str | None:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def validate_image_path(path: str | Path) -> tuple[Path, str]:
    """Return a canonical path and detected MIME type for a safe local image."""
    raw = str(path or "").strip()
    if not raw:
        raise ImageAttachmentError("Image path is empty.")
    resolved = Path(raw).expanduser().resolve()
    if not resolved.is_file():
        raise ImageAttachmentError(f"Image not found: {resolved}")
    size = resolved.stat().st_size
    if size <= 0:
        raise ImageAttachmentError(f"Image is empty: {resolved.name}")
    if size > MAX_IMAGE_BYTES:
        limit = MAX_IMAGE_BYTES // (1024 * 1024)
        raise ImageAttachmentError(
            f"Image is too large: {resolved.name} ({size} bytes; limit {limit} MB)."
        )
    with resolved.open("rb") as handle:
        mime = _mime_type(handle.read(16))
    if mime is None:
        raise ImageAttachmentError(
            f"Unsupported image format: {resolved.name}. Use PNG, JPEG, GIF, or WebP."
        )
    return resolved, mime


def normalize_image_paths(paths) -> list[str]:
    """Validate and deduplicate attachment paths while preserving their order."""
    raw_paths = list(paths or [])
    if len(raw_paths) > MAX_IMAGES_PER_MESSAGE:
        raise ImageAttachmentError(
            f"Attach at most {MAX_IMAGES_PER_MESSAGE} images per message."
        )
    normalized: list[str] = []
    seen: set[str] = set()
    for raw in raw_paths:
        path, _ = validate_image_path(raw)
        value = str(path)
        if value not in seen:
            normalized.append(value)
            seen.add(value)
    return normalized


def user_content_with_images(text: str, image_paths) -> str | list[dict]:
    """Build internal multimodal user content without embedding image bytes."""
    paths = normalize_image_paths(image_paths)
    body = (text or "").strip()
    if not paths:
        return body
    content: list[dict] = []
    if body:
        content.append({"type": "input_text", "text": body})
    content.extend(
        {"type": "input_image", "image_path": path, "detail": "auto"}
        for path in paths
    )
    return content


def image_paths_from_content(content) -> list[str]:
    """Extract internal local image paths from a content-part list."""
    if not isinstance(content, list):
        return []
    return [
        str(part.get("image_path"))
        for part in content
        if isinstance(part, dict)
        and part.get("type") == "input_image"
        and part.get("image_path")
    ]


def _data_url(path: str) -> str:
    resolved, mime = validate_image_path(path)
    encoded = base64.b64encode(resolved.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def _responses_content(content):
    if not isinstance(content, list):
        return content
    converted = []
    for part in content:
        if not isinstance(part, dict):
            converted.append(part)
            continue
        if part.get("type") != "input_image" or not part.get("image_path"):
            converted.append(copy.deepcopy(part))
            continue
        converted.append(
            {
                "type": "input_image",
                "image_url": _data_url(str(part["image_path"])),
                "detail": part.get("detail", "auto"),
            }
        )
    return converted


def to_responses_input(agent_input: list[dict]) -> list[dict]:
    """Copy internal messages into OpenAI Responses multimodal wire format."""
    converted = copy.deepcopy(agent_input)
    for item in converted:
        if isinstance(item, dict) and "content" in item:
            item["content"] = _responses_content(item.get("content"))
    return converted


def to_chat_content(content):
    """Convert internal content parts to OpenAI-compatible chat content parts."""
    if not isinstance(content, list):
        return content
    converted = []
    for part in content:
        if not isinstance(part, dict):
            converted.append(part)
            continue
        part_type = part.get("type")
        if part_type == "input_text":
            converted.append({"type": "text", "text": str(part.get("text", ""))})
        elif part_type == "input_image" and part.get("image_path"):
            converted.append(
                {
                    "type": "image_url",
                    "image_url": {"url": _data_url(str(part["image_path"]))},
                }
            )
        else:
            converted.append(copy.deepcopy(part))
    return converted
