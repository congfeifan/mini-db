import pytest

from minidb.contracts.errors import LexicalError, SyntaxError
from minidb.frontend import Frontend, Lexer
from minidb.frontend.formatter import ast_to_data
from tests.frontend.fuzz_support import (
    FailureCategory, generate_valid, minimize, mutate_invalid, record_failure,
)


def test_a_e10_reproducible():
    first = generate_valid(2026, 200, 4)
    second = generate_valid(2026, 200, 4)
    assert first == second
    for case in first:
        statements = Frontend().parse(case.sql)
        assert len(statements) == 1
        assert type(statements[0]).__name__ == case.expected_kind
        summary = dict(case.summary)
        if "has_where" in summary:
            assert (statements[0].where is not None) == summary["has_where"]
        if "star" in summary:
            assert (statements[0].columns is None) == summary["star"]


def test_a_e10_invalid_mutation():
    missing_from = mutate_invalid("SELECT id FROM t;", "remove_from")
    unterminated = mutate_invalid("INSERT INTO t VALUES('abc');", "unterminated_string")
    with pytest.raises(SyntaxError):
        Frontend().parse(missing_from)
    with pytest.raises(LexicalError):
        Frontend().parse(unterminated)


def test_a_e10_minimizer():
    predicate = lambda text: "BAD" in text
    assert minimize("xxBADyy", predicate) == "BAD"
    assert minimize("xxBADyy", predicate) == "BAD"
    with pytest.raises(ValueError):
        minimize("good", predicate)


def test_a_e10_failure_record():
    def crash(_sql):
        raise RuntimeError("injected parser crash")

    record = record_failure(2026, "SELECT * FROM t;", crash)
    assert record is not None
    assert record.seed == 2026 and record.sql == "SELECT * FROM t;"
    assert record.category == FailureCategory.CRASH
    assert record.exception_type == "RuntimeError"
    assert "seed=2026" in record.replay_command
    replay = record_failure(record.seed, record.sql, crash)
    assert replay == record


def test_a_e10_wrong_accept_and_reject_categories():
    accepted = record_failure(1, "SELECT id t;", lambda _sql: object(), expected_valid=False)
    rejected = record_failure(2, "SELECT id FROM t;", Frontend().parse)
    assert accepted.category == FailureCategory.WRONG_ACCEPT
    assert rejected is None


def test_a_e10_keyword_case_equivalence_preserves_lexeme():
    upper_sql = "SELECT id FROM t;"
    lower_sql = "select id from t;"
    assert ast_to_data(Frontend().parse(upper_sql)) == ast_to_data(Frontend().parse(lower_sql))
    upper = Lexer(upper_sql).tokenize()[0]
    lower = Lexer(lower_sql).tokenize()[0]
    assert upper.kind == lower.kind
    assert upper.lexeme == "SELECT" and lower.lexeme == "select"
