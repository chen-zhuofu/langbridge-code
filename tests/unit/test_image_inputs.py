import base64

import pytest

from langbridge_code.llm.images import (
    ImageAttachmentError,
    to_chat_content,
    to_responses_input,
    user_content_with_images,
)


def _png(tmp_path, name="sample.png"):
    path = tmp_path / name
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"test-image-bytes")
    return path


def test_internal_image_content_keeps_path_not_base64(tmp_path):
    path = _png(tmp_path)
    content = user_content_with_images("What is shown?", [path])
    assert content == [
        {"type": "input_text", "text": "What is shown?"},
        {"type": "input_image", "image_path": str(path.resolve()), "detail": "auto"},
    ]
    assert "base64" not in str(content)


def test_image_only_message_has_no_synthetic_text_prompt(tmp_path):
    content = user_content_with_images("", [_png(tmp_path)])
    assert content == [
        {
            "type": "input_image",
            "image_path": str((tmp_path / "sample.png").resolve()),
            "detail": "auto",
        }
    ]


def test_provider_converters_embed_data_url_only_at_request_time(tmp_path):
    path = _png(tmp_path)
    internal = [{"role": "user", "content": user_content_with_images("inspect", [path])}]

    responses = to_responses_input(internal)
    response_image = responses[0]["content"][1]
    assert response_image["type"] == "input_image"
    assert response_image["image_url"].startswith("data:image/png;base64,")

    chat = to_chat_content(internal[0]["content"])
    assert chat[0] == {"type": "text", "text": "inspect"}
    url = chat[1]["image_url"]["url"]
    assert base64.b64decode(url.split(",", 1)[1]).startswith(b"\x89PNG")
    assert "image_path" in internal[0]["content"][1]


def test_rejects_missing_or_unsupported_images(tmp_path):
    with pytest.raises(ImageAttachmentError, match="not found"):
        user_content_with_images("inspect", [tmp_path / "missing.png"])
    text = tmp_path / "not-image.png"
    text.write_text("hello", encoding="utf-8")
    with pytest.raises(ImageAttachmentError, match="Unsupported"):
        user_content_with_images("inspect", [text])
