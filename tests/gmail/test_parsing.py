import base64

import pytest

from google_personal_mcp.gmail.parsing import (
    find_part,
    html_to_text,
    parse_full_message,
    sender_address,
    summarize,
)

KNOWN_CODECS = {"iso-8859-1", "utf-8"}


def b64(data: bytes, *, pad: bool = True) -> str:
    encoded = base64.urlsafe_b64encode(data).decode()
    return encoded if pad else encoded.rstrip("=")


def hdrs(**headers: str) -> list[dict]:
    return [{"name": name.replace("_", "-"), "value": value} for name, value in headers.items()]


def meta(*, internal_date: str = "1700000000000", headers: list[dict] | None = None, **named: str) -> dict:
    return {
        "id": "m1",
        "threadId": "t1",
        "internalDate": internal_date,
        "snippet": "hello",
        "sizeEstimate": 1234,
        "labelIds": ["INBOX", "UNREAD"],
        "payload": {"headers": hdrs(**named) if headers is None else headers},
    }


def text_part(
    content: str | bytes,
    mime: str = "text/plain",
    *,
    charset: str | None = "utf-8",
    pad: bool = True,
    part_id: str = "0",
) -> dict:
    # An unknown charset only appears in the header; the bytes themselves are utf-8.
    encoding = charset if charset in KNOWN_CODECS else "utf-8"
    raw = content.encode(encoding) if isinstance(content, str) else content
    content_type = f'{mime}; charset="{charset}"' if charset else mime
    return {
        "partId": part_id,
        "mimeType": mime,
        "filename": "",
        "headers": [{"name": "Content-Type", "value": content_type}],
        "body": {"size": len(raw), "data": b64(raw, pad=pad)},
    }


def message(payload: dict) -> dict:
    return {
        "id": "m1",
        "threadId": "t1",
        "internalDate": "1700000000000",
        "sizeEstimate": 999,
        "labelIds": ["INBOX"],
        "payload": {
            **payload,
            "headers": payload.get("headers", [])
            + hdrs(From="A <a@example.com>", To="user@example.com", Cc="c@example.com", Subject="Hi"),
        },
    }


def plain(content: str | bytes, *, charset: str | None = "utf-8", pad: bool = True) -> dict:
    return message(text_part(content, charset=charset, pad=pad))


def html_only(html: str) -> dict:
    return message(text_part(html, "text/html"))


def attachment(part_id: str = "1", filename: str = "report.pdf") -> dict:
    return {
        "partId": part_id,
        "mimeType": "application/pdf",
        "filename": filename,
        "headers": [],
        "body": {"size": 5000, "attachmentId": "att-1"},
    }


def test_summarize_uses_internal_date_and_headers():
    s = summarize(meta(internal_date="1700000000000", From="Shop <deals@shop.example>", Subject="Sale"))
    assert s.date == "2023-11-14T22:13:20Z" and s.sender == "Shop <deals@shop.example>" and s.subject == "Sale"
    assert (s.id, s.thread_id, s.snippet, s.size_bytes, s.label_ids) == ("m1", "t1", "hello", 1234, ["INBOX", "UNREAD"])


def test_summarize_matches_headers_case_insensitively():
    s = summarize(meta(headers=[{"name": "fROM", "value": "x@example.com"}, {"name": "SUBJECT", "value": "S"}]))
    assert s.sender == "x@example.com" and s.subject == "S"


def test_missing_headers_are_empty_strings():
    s = summarize(meta(headers=[]))
    assert s.sender == "" and s.subject == ""


def test_summarize_never_raises_on_malformed_input():
    s = summarize({"internalDate": "not-a-number"})
    assert s.id == "" and s.date == "" and s.size_bytes == 0 and s.label_ids == []


def test_prefers_plain_text_over_html():
    alt = {
        "partId": "",
        "mimeType": "multipart/alternative",
        "headers": [],
        "parts": [text_part("<p>html</p>", "text/html", part_id="1"), text_part("plain wins", part_id="0")],
    }
    assert parse_full_message(message(alt), max_body_chars=100).body_text == "plain wins"


def test_html_only_is_converted():
    assert parse_full_message(html_only("<p>Hi</p><script>x()</script><p>there</p>"), max_body_chars=100).body_text == (
        "Hi\nthere"
    )


def test_nested_multipart_mixed_with_attachment():
    inner = {
        "partId": "0",
        "mimeType": "multipart/alternative",
        "headers": [],
        "parts": [text_part("<b>h</b>", "text/html", part_id="0.1"), text_part("inner plain", part_id="0.0")],
    }
    mixed = {"partId": "", "mimeType": "multipart/mixed", "headers": [], "parts": [inner, attachment("1")]}
    d = parse_full_message(message(mixed), max_body_chars=100)
    assert d.body_text == "inner plain"
    assert len(d.attachments) == 1
    a = d.attachments[0]
    assert (a.part_id, a.filename, a.mime_type, a.size_bytes) == ("1", "report.pdf", "application/pdf", 5000)


def test_detail_headers_and_metadata():
    d = parse_full_message(plain("body"), max_body_chars=100)
    assert (d.sender, d.to, d.cc, d.subject) == ("A <a@example.com>", "user@example.com", "c@example.com", "Hi")
    assert (d.id, d.thread_id, d.size_bytes, d.label_ids, d.date) == (
        "m1",
        "t1",
        999,
        ["INBOX"],
        "2023-11-14T22:13:20Z",
    )
    assert d.attachments == [] and d.body_truncated is False


def test_iso_8859_1_body():
    assert "café" in parse_full_message(plain("café", charset="iso-8859-1"), max_body_chars=100).body_text


def test_unknown_charset_falls_back_to_utf8():
    d = parse_full_message(plain("café", charset="no-such-charset"), max_body_chars=100)
    assert d.body_text == "café"


def test_missing_charset_defaults_to_utf8():
    assert parse_full_message(plain("café", charset=None), max_body_chars=100).body_text == "café"


def test_unpadded_base64url_is_decoded():
    d = parse_full_message(plain("abcd", pad=False), max_body_chars=100)
    assert d.body_text == "abcd"


def test_invalid_base64_falls_back_to_empty_body():
    for bad in ("a", "caf\u00e9", "\u00e9" * 3):
        part = text_part("x")
        part["body"]["data"] = bad
        assert parse_full_message(message(part), max_body_chars=100).body_text == ""


@pytest.mark.parametrize("charset", ["undefined", "idna", "punycode", "utf\x00-8"])
def test_hostile_charset_falls_back_to_utf8(charset):
    part = text_part("caf\u00e9")
    part["headers"] = [{"name": "Content-Type", "value": f'text/plain; charset="{charset}"'}]
    assert parse_full_message(message(part), max_body_chars=100).body_text == "caf\u00e9"


def test_deeply_nested_parts_do_not_raise():
    part = text_part("deep")
    for _ in range(1000):
        part = {"partId": "n", "mimeType": "multipart/mixed", "parts": [part]}
    assert parse_full_message(message(part), max_body_chars=100).body_text == ""
    assert find_part(message(part), "0") is None


def test_invalid_bytes_use_replacement_char():
    d = parse_full_message(plain(b"ok \xff\xfe end"), max_body_chars=100)
    assert "�" in d.body_text and d.body_text.startswith("ok ")


def test_message_without_payload_does_not_raise():
    d = parse_full_message({"id": "m9"}, max_body_chars=10)
    assert d.id == "m9" and d.body_text == "" and d.attachments == []


def test_truncation_sets_flag():
    d = parse_full_message(plain("x" * 50), max_body_chars=10)
    assert d.body_text == "x" * 10 and d.body_truncated is True


def test_exact_length_is_not_truncated():
    d = parse_full_message(plain("x" * 10), max_body_chars=10)
    assert d.body_text == "x" * 10 and d.body_truncated is False


def test_sender_address_normalizes():
    assert sender_address('"Shop" <Deals@Shop.Example>') == "deals@shop.example"
    assert sender_address("not an address") == "not an address"
    assert sender_address("  ") == ""


def test_html_to_text_collapses_whitespace_and_skips_style():
    html = "<style>p{}</style><div>a   b</div><br>c<ul><li>d</li></ul>"
    assert html_to_text(html) == "a b\nc\nd"


def test_find_part_by_id():
    inner = {"partId": "0", "mimeType": "multipart/alternative", "parts": [text_part("t", part_id="0.0")]}
    msg = message({"partId": "", "mimeType": "multipart/mixed", "parts": [inner, attachment("1")]})
    found = find_part(msg, "1")
    assert found is not None and found["filename"] == "report.pdf"
    nested = find_part(msg, "0.0")
    assert nested is not None and nested["mimeType"] == "text/plain"
    assert find_part(msg, "9") is None
    assert find_part({}, "1") is None
    assert find_part(msg, "") is None
