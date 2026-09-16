import pytest

from minidb.contracts.ast import SelectStmt
from minidb.contracts.errors import SyntaxError
from minidb.frontend import Frontend


def test_a_e08_recover_next():
    result = Frontend().parse_recovering("SELECT FROM t; SELECT id FROM t;")

    assert len(result.errors) == 1
    assert result.errors[0].statement_index == 1
    assert result.errors[0].error.stage == "SYNTAX"
    assert len(result.statements) == 1
    assert result.statements[0].statement_index == 2
    assert isinstance(result.statements[0].statement, SelectStmt)
    assert result.statements[0].statement.span.start.offset == 15
    assert not result.truncated


def test_a_e08_consecutive_errors():
    result = Frontend().parse_recovering("SELECT; DELETE; SELECT * FROM t;")

    assert [item.statement_index for item in result.errors] == [1, 2]
    assert [item.statement_index for item in result.statements] == [3]
    assert result.errors[0].error.span.start.offset == 6
    assert result.errors[1].error.span.start.offset == 14
    assert not result.truncated


def test_a_e08_eof_error():
    result = Frontend().parse_recovering("SELECT id FROM")

    assert not result.statements
    assert len(result.errors) == 1
    assert result.errors[0].statement_index == 1
    assert result.errors[0].error.context["actual"] == "EOF"
    assert result.errors[0].error.span.start.offset == len("SELECT id FROM")
    assert not result.truncated


def test_a_e08_core_unchanged():
    with pytest.raises(SyntaxError) as caught:
        Frontend().parse("SELECT FROM t; SELECT id FROM t;")

    assert caught.value.span.start.offset == 7


def test_a_e08_error_limit_and_lexical_stop():
    limited = Frontend().parse_recovering(
        "SELECT; DELETE; SELECT * FROM t;", max_errors=1
    )
    assert len(limited.errors) == 1
    assert not limited.statements
    assert limited.truncated

    lexical = Frontend().parse_recovering(
        "SELECT * FROM t; INSERT INTO t VALUES('oops);"
    )
    assert not lexical.statements
    assert len(lexical.errors) == 1
    assert lexical.errors[0].statement_index == 2
    assert lexical.errors[0].error.stage == "LEXICAL"
    assert lexical.errors[0].error.code == "UNTERMINATED_STRING"
    assert lexical.truncated


@pytest.mark.parametrize("maximum", [0, -1, True, 1.5, "1"])
def test_a_e08_reject_invalid_error_limit(maximum):
    with pytest.raises(ValueError, match="max_errors"):
        Frontend().parse_recovering("SELECT * FROM t;", max_errors=maximum)


def test_a_e08_does_not_swallow_internal_errors(monkeypatch):
    def crash(_self):
        raise RuntimeError("injected")

    monkeypatch.setattr("minidb.frontend.parser.Parser.parse_statement", crash)
    with pytest.raises(RuntimeError, match="injected"):
        Frontend().parse_recovering("SELECT * FROM t;")


def test_a_e08_keeps_enabled_extensions():
    result = Frontend(enabled_extensions={"distinct"}).parse_recovering(
        "SELECT; SELECT DISTINCT id FROM t;"
    )

    assert [item.statement_index for item in result.errors] == [1]
    assert [item.statement_index for item in result.statements] == [2]
    assert result.statements[0].statement.feature == "distinct"
