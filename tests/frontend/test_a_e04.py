import pytest

from minidb.contracts.errors import SyntaxError
from minidb.contracts.extensions import ExtensionStatement
from minidb.frontend import Frontend


def parse_join(source):
    result = Frontend(enabled_extensions={"join"}).parse(source)
    assert len(result) == 1 and isinstance(result[0], ExtensionStatement)
    return result[0]


def test_a_e04_join_payload():
    result = parse_join("SELECT s.id,c.name FROM student AS s JOIN class c ON s.cid=c.id;")
    payload = result.payload
    assert result.feature == "join" and result.version == 1
    assert payload["join_type"] == "INNER"
    assert payload["left"]["table"]["fields"]["name"] == "student"
    assert payload["left"]["alias"]["fields"]["name"] == "s"
    assert payload["right"]["table"]["fields"]["name"] == "class"
    assert payload["right"]["alias"]["fields"]["name"] == "c"
    assert [(column["fields"]["qualifier"], column["fields"]["name"])
            for column in payload["columns"]] == [("s", "id"), ("c", "name")]
    assert payload["on"]["fields"]["left"]["fields"] == {"qualifier": "s", "name": "cid"}
    assert payload["on"]["fields"]["right"]["fields"] == {"qualifier": "c", "name": "id"}


def test_a_e04_join_where():
    payload = parse_join(
        "SELECT s.id FROM s INNER JOIN c ON s.id=c.id WHERE s.id>1;"
    ).payload
    assert payload["on"]["fields"]["op"] == "="
    assert payload["where"]["fields"]["op"] == ">"
    assert payload["where"]["fields"]["left"]["fields"]["qualifier"] == "s"


def test_a_e04_missing_on():
    with pytest.raises(SyntaxError) as caught:
        parse_join("SELECT s.id FROM s JOIN c;")
    assert caught.value.context["lexeme"] == ";"
    assert "ON" in caught.value.context["expected"]


def test_a_e04_reject_outer_join():
    with pytest.raises(SyntaxError) as caught:
        parse_join("SELECT * FROM s LEFT JOIN c ON s.id=c.id;")
    assert caught.value.code == "UNSUPPORTED_JOIN_TYPE"
    assert caught.value.context["lexeme"].casefold() == "left"


def test_a_e04_defaults_alias_and_preserves_duplicate_aliases():
    default = parse_join("SELECT id FROM student JOIN class ON student.id=class.id;").payload
    assert default["left"]["alias"]["fields"]["name"] == "student"
    assert default["right"]["alias"]["fields"]["name"] == "class"
    duplicate = parse_join("SELECT x.id FROM student x JOIN class x ON x.id=x.id;").payload
    assert duplicate["left"]["alias"]["fields"]["name"] == "x"
    assert duplicate["right"]["alias"]["fields"]["name"] == "x"


def test_a_e04_disabled_and_core_unchanged():
    with pytest.raises(SyntaxError) as caught:
        Frontend().parse("SELECT s.id FROM s JOIN c ON s.id=c.id;")
    assert caught.value.context["lexeme"].casefold() == "join"
    core = "SELECT id FROM student WHERE id=1;"
    assert Frontend().parse(core) == Frontend(enabled_extensions={"join"}).parse(core)
