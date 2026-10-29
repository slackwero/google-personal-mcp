import pytest

from google_personal_mcp.gmail.labels import REJECTED_LABELS, LabelCatalog


def catalog(user: list[tuple[str, str]] = ()) -> LabelCatalog:
    labels = [{"id": "INBOX", "name": "INBOX", "type": "system"}, {"id": "UNREAD", "name": "UNREAD", "type": "system"}]
    labels += [{"id": label_id, "name": name, "type": "user"} for label_id, name in user]
    return LabelCatalog(labels)


def test_system_labels_case_insensitive():
    assert catalog().id_for("inbox") == "INBOX"


def test_user_labels_exact():
    c = catalog(user=[("Label_1", "Promociones"), ("Label_2", "Parent/Child")])
    assert c.id_for("Parent/Child") == "Label_2"
    with pytest.raises(ValueError, match="Unknown label: 'promociones'"):
        c.id_for("promociones")


def test_user_label_named_like_system_label_does_not_shadow_it():
    c = catalog(user=[("Label_1", "inbox")])
    assert c.id_for("inbox") == "Label_1"
    assert c.id_for("INBOX") == "INBOX"


def test_name_for_unknown_id_returns_id():
    assert catalog().name_for("Label_9") == "Label_9"


def test_names_for_maps_ids_to_names_in_order():
    c = catalog(user=[("Label_1", "Promociones")])
    assert c.names_for(["Label_1", "INBOX", "Label_9"]) == ["Promociones", "INBOX", "Label_9"]


def test_rejected_labels():
    assert REJECTED_LABELS == {"TRASH", "SPAM", "SENT", "DRAFT"}
