import pytest

from minidb.contracts.errors import SyntaxError
from minidb.contracts.extensions import ExtensionStatement
from minidb.frontend import Frontend


def parse_aggregate(source):
    result = Frontend(enabled_extensions={"aggregate"}).parse(source)
    assert len(result) == 1 and isinstance(result[0], ExtensionStatement)
    return result[0]


def test_a_e05_group_payload():
    payload = parse_aggregate(
        "SELECT age,COUNT(*) AS n FROM t GROUP BY age;"
    ).payload
    assert [item["function"] for item in payload["select_items"]] == [None, "COUNT"]
    assert payload["select_items"][0]["column"]["fields"]["name"] == "age"
    assert payload["select_items"][1]["column"] == "*"
    assert payload["select_items"][1]["alias"]["fields"]["name"] == "n"
    assert [item["fields"]["name"] for item in payload["group_by"]] == ["age"]
    assert payload["where"] is None


def test_a_e05_global_aggregate():
    result = parse_aggregate("SELECT SUM(age) FROM t;")
    assert result.feature == "aggregate"
    assert result.payload["group_by"] == []
    assert result.payload["select_items"][0]["function"] == "SUM"
    assert result.payload["select_items"][0]["column"]["fields"]["name"] == "age"


def test_a_e05_bad_star():
    with pytest.raises(SyntaxError) as caught:
        parse_aggregate("SELECT SUM(*) FROM t;")
    assert caught.value.code == "INVALID_AGGREGATE_ARGUMENT"
    assert caught.value.context["lexeme"] == "*"


def test_a_e05_semantic_deferred():
    payload = parse_aggregate(
        "SELECT name,COUNT(*) FROM t WHERE age>0 GROUP BY age;"
    ).payload
    assert payload["select_items"][0]["column"]["fields"]["name"] == "name"
    assert payload["group_by"][0]["fields"]["name"] == "age"
    assert payload["where"]["fields"]["left"]["fields"]["name"] == "age"


def test_a_e05_multiple_items_aliases_preserve_order():
    items = parse_aggregate(
        "SELECT COUNT(*) AS n,SUM(age) AS total FROM t;"
    ).payload["select_items"]
    assert [(item["function"], item["alias"]["fields"]["name"])
            for item in items] == [("COUNT", "n"), ("SUM", "total")]


def test_a_e05_disabled_and_plain_select_unchanged():
    with pytest.raises(SyntaxError):
        Frontend().parse("SELECT COUNT(*) FROM t;")
    source = "SELECT age FROM t;"
    assert Frontend().parse(source) == Frontend(enabled_extensions={"aggregate"}).parse(source)
