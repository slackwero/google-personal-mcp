"""Label catalog: translation between label names (what the user says) and label ids (what the API takes)."""

from collections.abc import Iterable

# Labels that gmail_modify_labels refuses: trash and spam have their own tools or are managed by Gmail itself.
REJECTED_LABELS = frozenset({"TRASH", "SPAM", "SENT", "DRAFT"})


class LabelCatalog:
    """Maps names to ids and back for one snapshot of `labels.list`.

    User labels match their name exactly; system labels match their id case-insensitively.
    """

    def __init__(self, labels: list[dict]) -> None:
        self._names_by_id: dict[str, str] = {}
        self._user_ids: dict[str, str] = {}
        self._system_ids: dict[str, str] = {}
        for label in labels:
            label_id, name = label.get("id"), label.get("name")
            if not isinstance(label_id, str) or not isinstance(name, str):
                continue
            self._names_by_id[label_id] = name
            if label.get("type") == "system":
                self._system_ids[label_id.upper()] = label_id
            else:
                self._user_ids[name] = label_id

    def id_for(self, name: str) -> str:
        """Return the id of a label name, or raise ValueError when no label matches."""
        if name in self._user_ids:
            return self._user_ids[name]
        if name.upper() in self._system_ids:
            return self._system_ids[name.upper()]
        raise ValueError(f"Unknown label: '{name}'")

    def name_for(self, label_id: str) -> str:
        """Return the name of a label id; an unknown id is returned unchanged."""
        return self._names_by_id.get(label_id, label_id)

    def names_for(self, label_ids: Iterable[str]) -> list[str]:
        """Return the names of many label ids, in order."""
        return [self.name_for(label_id) for label_id in label_ids]
