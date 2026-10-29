import pytest

from google_personal_mcp.gmail.targets import MAX_TARGET, validate_target


@pytest.mark.parametrize(
    ("ids", "query", "cap"),
    [
        (None, None, None),
        (["a"], "x", 5),
        (["a"], None, 5),
        (None, "x", None),
        (None, "x", 0),
        (None, "x", 1001),
        ([], None, None),
        ([""], None, None),
        (["  "], None, None),
        (None, "", 5),
        (None, "   ", 5),
    ],
)
def test_invalid_targets(ids, query, cap):
    with pytest.raises(ValueError):
        validate_target(ids, query, cap)


def test_ids_deduplicated_in_order():
    assert validate_target(["b", "a", "b"], None, None).message_ids == ("b", "a")


def test_query_target():
    target = validate_target(None, "from:x", 1000)
    assert (target.message_ids, target.query, target.max_messages) == (None, "from:x", 1000)


def test_more_than_1000_ids():
    assert len(validate_target([str(i) for i in range(MAX_TARGET)], None, None).message_ids) == MAX_TARGET
    with pytest.raises(ValueError, match="at most"):
        validate_target([str(i) for i in range(MAX_TARGET + 1)], None, None)
    assert len(validate_target(["a"] * 2000, None, None).message_ids) == 1
