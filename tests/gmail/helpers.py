"""Builders shared by the write-path tests."""

from google_personal_mcp.config import Settings, load_settings
from google_personal_mcp.gmail.labels import LabelCatalog
from tests.fakes import FakeGmailClient

SYSTEM_LABELS = ["INBOX", "UNREAD", "STARRED", "TRASH", "SPAM", "SENT", "DRAFT"]


def mailbox(**messages: list[str]) -> FakeGmailClient:
    """A fake mailbox where each keyword is a message id and its value the message's label ids."""
    fake = FakeGmailClient()
    fake.label_list = label_list()
    for message_id, labels in messages.items():
        fake.add_message(message_id, labels)
    return fake


def label_list(*user_labels: tuple[str, str]) -> list[dict]:
    system = [{"id": name, "name": name, "type": "system"} for name in SYSTEM_LABELS]
    return system + [{"id": label_id, "name": name, "type": "user"} for label_id, name in user_labels]


def catalog_with(*pairs: str) -> LabelCatalog:
    """A catalog with the system labels plus user labels given as alternating id, name arguments."""
    return LabelCatalog(label_list(*zip(pairs[::2], pairs[1::2], strict=True)))


def catalog() -> LabelCatalog:
    return catalog_with()


def settings() -> Settings:
    return load_settings({"HOME": "/nonexistent-home", "GOOGLE_PERSONAL_MCP_ACCOUNT": "user@example.com"})
