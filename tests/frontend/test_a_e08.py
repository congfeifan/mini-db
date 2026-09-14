import pytest

from minidb.contracts.ast import SelectStmt
from minidb.contracts.errors import SyntaxError
from minidb.frontend import Frontend, parse_recovering


def test_a_e08_recover_next():
    result = parse_recovering("SELECT FROM t; SELECT id FROM t;")
    assert len(result.errors) == 1
    assert len(result.statements) == 1
    assert result.statements[0].statement_index == 2
    assert isinstance(result.statements[0].statement, SelectStmt)
    assert result.statements[0].statement.span.start.offset == 15


def test_a_e08_consecutive_errors():
    result = parse_recovering("SELECT; DELETE; SELECT * FROM t;")
    assert [error.statement_index for error in result.errors] == [1, 2]
    assert [item.statement_index for item in result.statements] == [3]
    assert not result.truncated


def test_a_e08_eof_error():
    result = parse_recovering("SELECT id FROM")
    assert len(result.errors) == 1 and not result.statements
    assert result.errors[0].error.context["actual"] == "EOF"


def test_a_e08_core_unchanged():
    with pytest.raises(SyntaxError):
        Frontend().parse("SELECT FROM t; SELECT id FROM t;")


def test_a_e08_error_limit_and_lexical_stop():
    limited = parse_recovering("SELECT; DELETE; SELECT * FROM t;", max_errors=1)
    assert len(limited.errors) == 1 and limited.truncated
    lexical = parse_recovering("SELECT * FROM t; INSERT INTO t VALUES('oops);")
    assert lexical.truncated
    assert lexical.errors[0].error.code == "UNTERMINATED_STRING"


def test_a_e08_does_not_swallow_internal_errors(monkeypatch):
    def crash(_self):
        raise RuntimeError("injected")

    monkeypatch.setattr("minidb.frontend.parser.Parser.parse_statement", crash)
    with pytest.raises(RuntimeError, match="injected"):
        parse_recovering("SELECT * FROM t;")
