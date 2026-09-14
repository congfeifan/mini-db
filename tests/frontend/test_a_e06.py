import pytest

from minidb.contracts.errors import SyntaxError
from minidb.contracts.extensions import ExtensionStatement
from minidb.frontend import Frontend, Lexer


def parse_types(source):
    result = Frontend(enabled_extensions={"types"}).parse(source)
    assert len(result) == 1 and isinstance(result[0], ExtensionStatement)
    return result[0]


def test_a_e06_types_create():
    result = parse_types("CREATE TABLE t(x FLOAT,ok BOOL);")
    columns = result.payload["statement"]["fields"]["columns"]
    assert result.feature == "types"
    assert [column["fields"]["dtype_name"] for column in columns] == ["FLOAT", "BOOL"]
    with pytest.raises(SyntaxError):
        Frontend().parse("CREATE TABLE t(x FLOAT,ok BOOL);")


def test_a_e06_literal_values():
    values = parse_types(
        "INSERT INTO t VALUES(3.14,TRUE,NULL);"
    ).payload["statement"]["fields"]["values"]
    assert [value["fields"]["value"] for value in values] == [3.14, True, None]
    assert type(values[1]["fields"]["value"]) is bool


def test_a_e06_string_unchanged():
    statement = Frontend(enabled_extensions={"types"}).parse(
        "INSERT INTO t VALUES('NULL','true');"
    )[0]
    assert not isinstance(statement, ExtensionStatement)
    assert [value.value for value in statement.values] == ["NULL", "true"]


def test_a_e06_unknown_type():
    with pytest.raises(SyntaxError) as caught:
        Frontend(enabled_extensions={"types"}).parse("CREATE TABLE t(x MONEY);")
    assert caught.value.context["lexeme"] == "MONEY"
    assert caught.value.span.start.column == 18


def test_a_e06_keyword_case_and_quoted_false():
    upper = Lexer("FALSE").tokenize()[0]
    lower = Lexer("false").tokenize()[0]
    quoted = Lexer("'FALSE'").tokenize()[0]
    assert upper.kind == lower.kind and upper.value == lower.value == "false"
    assert upper.lexeme == "FALSE" and lower.lexeme == "false"
    assert quoted.value == "FALSE"


def test_a_e06_boolean_where_is_wrapped():
    result = parse_types("SELECT * FROM t WHERE TRUE;")
    where = result.payload["statement"]["fields"]["where"]
    assert where["fields"]["value"] is True
