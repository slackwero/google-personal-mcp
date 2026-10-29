"""Pure parsing of Gmail API message dicts into models. Email content is untrusted: nothing here raises on bad input."""

import base64
import binascii
import codecs
from datetime import UTC, datetime
from email.message import Message
from email.utils import parseaddr
from html.parser import HTMLParser
from typing import Any

from pydantic import BaseModel

DEFAULT_CHARSET = "utf-8"
MAX_PART_DEPTH = 50
# Python codecs that are not real text charsets: they fail or decode to garbage without raising.
NON_TEXT_CODECS = frozenset({"idna", "punycode", "undefined"})
SKIPPED_TAGS = frozenset({"script", "style"})
BLOCK_TAGS = frozenset(
    {"p", "div", "br", "tr", "li", "ul", "ol", "table", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "pre", "hr"}
)


class MessageSummary(BaseModel):
    """Headers-level view of a message, used in search results."""

    id: str
    thread_id: str
    date: str
    sender: str
    subject: str
    snippet: str
    size_bytes: int
    label_ids: list[str]


class AttachmentInfo(BaseModel):
    """An attachment part of a message (its bytes are fetched separately)."""

    part_id: str
    filename: str
    mime_type: str
    size_bytes: int


class MessageDetail(BaseModel):
    """Full view of a message: headers, text body and attachment list."""

    id: str
    thread_id: str
    date: str
    sender: str
    to: str
    cc: str
    subject: str
    label_ids: list[str]
    size_bytes: int
    body_text: str
    body_truncated: bool
    attachments: list[AttachmentInfo]


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._chunks: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in SKIPPED_TAGS:
            self._skip_depth += 1
        elif tag in BLOCK_TAGS:
            self._chunks.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in SKIPPED_TAGS:
            self._skip_depth = max(0, self._skip_depth - 1)
        elif tag in BLOCK_TAGS:
            self._chunks.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip_depth:
            self._chunks.append(data)

    def text(self) -> str:
        lines = (" ".join(line.split()) for line in "".join(self._chunks).split("\n"))
        return "\n".join(line for line in lines if line)


def html_to_text(html: str) -> str:
    """Convert HTML to plain text: script/style dropped, block tags become newlines, whitespace collapsed."""
    extractor = _TextExtractor()
    extractor.feed(html)
    extractor.close()
    return extractor.text()


def sender_address(from_header: str) -> str:
    """Return the lowercased address of a From header, or the stripped header when none is found."""
    address = parseaddr(from_header)[1]
    return address.lower() if "@" in address else from_header.strip()


def _as_dict(value: Any) -> dict:  # noqa: ANN401 - untrusted JSON
    return value if isinstance(value, dict) else {}


def _as_str(value: Any) -> str:  # noqa: ANN401 - untrusted JSON
    return value if isinstance(value, str) else ""


def _as_int(value: Any) -> int:  # noqa: ANN401 - untrusted JSON
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _as_str_list(value: Any) -> list[str]:  # noqa: ANN401 - untrusted JSON
    return [item for item in value if isinstance(item, str)] if isinstance(value, list) else []


def _header(part: dict, name: str) -> str:
    wanted = name.lower()
    headers = _as_dict(part.get("payload", part)).get("headers")
    for header in headers if isinstance(headers, list) else []:
        header = _as_dict(header)  # noqa: PLW2901
        if _as_str(header.get("name")).lower() == wanted:
            return _as_str(header.get("value"))
    return ""


def _iso_date(internal_date: Any) -> str:  # noqa: ANN401 - untrusted JSON
    try:
        moment = datetime.fromtimestamp(int(internal_date) / 1000, UTC)
    except ValueError, TypeError, OverflowError, OSError:
        return ""
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def summarize(metadata: dict) -> MessageSummary:
    """Build a summary from a `format=metadata` message."""
    return MessageSummary(
        id=_as_str(metadata.get("id")),
        thread_id=_as_str(metadata.get("threadId")),
        date=_iso_date(metadata.get("internalDate")),
        sender=_header(metadata, "From"),
        subject=_header(metadata, "Subject"),
        snippet=_as_str(metadata.get("snippet")),
        size_bytes=_as_int(metadata.get("sizeEstimate")),
        label_ids=_as_str_list(metadata.get("labelIds")),
    )


def _charset(part: dict) -> str:
    content_type = _header(part, "Content-Type")
    if not content_type:
        return DEFAULT_CHARSET
    holder = Message()
    holder["Content-Type"] = content_type
    charset = holder.get_content_charset() or DEFAULT_CHARSET
    try:
        known = codecs.lookup(charset).name
    except LookupError, ValueError:
        return DEFAULT_CHARSET
    return DEFAULT_CHARSET if known in NON_TEXT_CODECS else charset


def decode_base64url(data: str) -> bytes:
    """Strictly decode Gmail's base64url data (padding optional); raises `binascii.Error` (a ValueError) if corrupt."""
    return base64.b64decode(data + "=" * (-len(data) % 4), altchars=b"-_", validate=True)


def _decode_part(part: dict) -> str:
    data = _as_str(_as_dict(part.get("body")).get("data"))
    try:
        raw = decode_base64url(data)
    except binascii.Error, ValueError:
        return ""
    try:
        return raw.decode(_charset(part), errors="replace")
    except LookupError, ValueError:  # unknown or hostile charset names (UnicodeError is a ValueError)
        return raw.decode(DEFAULT_CHARSET, errors="replace")


def _children(part: dict) -> list[dict]:
    parts = part.get("parts")
    return [_as_dict(child) for child in parts] if isinstance(parts, list) else []


def _walk(part: dict, depth: int = 0) -> list[dict]:
    found = [part]
    if depth < MAX_PART_DEPTH:
        for child in _children(part):
            found.extend(_walk(child, depth + 1))
    return found


def _is_attachment(part: dict) -> bool:
    return bool(_as_str(part.get("filename")))


def _body_text(parts: list[dict]) -> str:
    candidates = [p for p in parts if not _is_attachment(p)]
    for mime_type in ("text/plain", "text/html"):
        for part in candidates:
            if _as_str(part.get("mimeType")).lower() == mime_type:
                text = _decode_part(part)
                if text.strip():
                    return text if mime_type == "text/plain" else html_to_text(text)
    return ""


def parse_full_message(message: dict, *, max_body_chars: int) -> MessageDetail:
    """Build a detail from a `format=full` message; the body is cut to `max_body_chars`."""
    payload = _as_dict(message.get("payload"))
    parts = _walk(payload) if payload else []
    body = _body_text(parts)
    attachments = [
        AttachmentInfo(
            part_id=_as_str(part.get("partId")),
            filename=_as_str(part.get("filename")),
            mime_type=_as_str(part.get("mimeType")),
            size_bytes=_as_int(_as_dict(part.get("body")).get("size")),
        )
        for part in parts
        if _is_attachment(part)
    ]
    return MessageDetail(
        id=_as_str(message.get("id")),
        thread_id=_as_str(message.get("threadId")),
        date=_iso_date(message.get("internalDate")),
        sender=_header(message, "From"),
        to=_header(message, "To"),
        cc=_header(message, "Cc"),
        subject=_header(message, "Subject"),
        label_ids=_as_str_list(message.get("labelIds")),
        size_bytes=_as_int(message.get("sizeEstimate")),
        body_text=body[:max_body_chars],
        body_truncated=len(body) > max_body_chars,
        attachments=attachments,
    )


def find_part(message: dict, part_id: str) -> dict | None:
    """Return the payload part with the given `partId`, or None."""
    if not part_id:
        return None
    payload = _as_dict(message.get("payload"))
    for part in _walk(payload) if payload else []:
        if _as_str(part.get("partId")) == part_id:
            return part
    return None
