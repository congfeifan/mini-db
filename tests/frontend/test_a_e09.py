from minidb.frontend.grammar_analysis import (
    EOF, EPSILON, analyze_grammar, build_table, compute_first,
    compute_follow, compute_nullable,
)


GRAMMAR = {
    "S": [("A", "B")],
    "A": [("a",), (EPSILON,)],
    "B": [("b",)],
}


def test_a_e09_nullable_first():
    assert compute_nullable(GRAMMAR) == frozenset({"A"})
    first = compute_first(GRAMMAR)
    assert first["A"] == frozenset({"a", EPSILON})
    assert first["S"] == frozenset({"a", "b"})


def test_a_e09_follow():
    follow = compute_follow(GRAMMAR, "S")
    assert follow["S"] == frozenset({EOF})
    assert follow["A"] == frozenset({"b"})
    assert follow["B"] == frozenset({EOF})


def test_a_e09_table():
    table, conflicts = build_table(GRAMMAR, "S")
    assert table[("S", "a")][0].right == ("A", "B")
    assert table[("S", "b")][0].right == ("A", "B")
    assert table[("A", "b")][0].right == ()
    assert conflicts == ()


def test_a_e09_conflict():
    grammar = {"S": [("a", "A"), ("a", "B")], "A": [("x",)], "B": [("y",)]}
    analysis = analyze_grammar(grammar, "S")
    assert len(analysis.conflicts) == 1
    conflict = analysis.conflicts[0]
    assert (conflict.nonterminal, conflict.lookahead) == ("S", "a")
    assert [item.right for item in conflict.productions] == [("a", "A"), ("a", "B")]


def test_a_e09_nullable_suffix_and_stable_output():
    grammar = {"S": [("A", "B")], "A": [("a",), (EPSILON,)], "B": [(EPSILON,)]}
    first = analyze_grammar(grammar, "S")
    second = analyze_grammar(grammar, "S")
    assert first.first["S"] == frozenset({"a", EPSILON})
    assert first.follow["A"] == frozenset({EOF})
    assert first.to_data() == second.to_data()
    assert first.nullable_rounds and first.first_rounds and first.follow_rounds
