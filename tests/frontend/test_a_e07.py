import pytest

from minidb.contracts.errors import SyntaxError
from minidb.contracts.extensions import ExtensionStatement
from minidb.frontend import Frontend


def arithmetic_statement(source):
    result = Frontend(enabled_extensions={"arithmetic"}).parse(source)
    assert len(result) == 1 and isinstance(result[0], ExtensionStatement)
    return result[0].payload["statement"]


def where_of(source):
    return arithmetic_statement(source)["fields"]["where"]


def test_a_e07_precedence():
    where = where_of("SELECT * FROM t WHERE age>10+2*4;")
    right = where["fields"]["right"]
    assert where["fields"]["op"] == ">"
    assert right["fields"]["op"] == "+"
    assert right["fields"]["right"]["fields"]["op"] == "*"


def test_a_e07_left_associative():
    right = where_of("SELECT * FROM t WHERE id=8-3-1;")["fields"]["right"]
    assert right["fields"]["op"] == "-"
    assert right["fields"]["left"]["fields"]["op"] == "-"


def test_a_e07_unary_and_not():
    where = where_of("SELECT * FROM t WHERE NOT id=-2*3;")
    comparison = where["fields"]["operand"]
    product = comparison["fields"]["right"]
    assert where["fields"]["op"] == "NOT"
    assert comparison["fields"]["op"] == "="
    assert product["fields"]["op"] == "*"
    assert product["fields"]["left"]["fields"]["op"] == "-"


def test_a_e07_missing_rhs():
    source = "SELECT * FROM t WHERE id=1+;"
    with pytest.raises(SyntaxError) as caught:
        arithmetic_statement(source)
    assert caught.value.context["lexeme"] == ";"
    assert caught.value.span.start.offset == source.index(";")


def test_a_e07_division_left_associative_and_parentheses():
    left = where_of("SELECT * FROM t WHERE id=8/2/2;")["fields"]["right"]
    grouped = where_of("SELECT * FROM t WHERE id=8/(2/2);")["fields"]["right"]
    assert left["fields"]["left"]["fields"]["op"] == "/"
    assert grouped["fields"]["right"]["fields"]["op"] == "/"


def test_a_e07_core_mode_still_rejects_arithmetic():
    with pytest.raises(SyntaxError) as caught:
        Frontend().parse("SELECT * FROM t WHERE id=1+2;")
    assert caught.value.context["lexeme"] == "+"
