"""用于教学的 nullable、FIRST、FOLLOW 和 LL(1) 预测表分析。"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass


EPSILON = "ε"
EOF = "EOF"


@dataclass(frozen=True, order=True)
class Production:
    left: str
    right: tuple[str, ...]

    def text(self) -> str:
        return f"{self.left} -> {' '.join(self.right) if self.right else EPSILON}"


@dataclass(frozen=True)
class LL1Conflict:
    nonterminal: str
    lookahead: str
    productions: tuple[Production, ...]


@dataclass(frozen=True)
class GrammarAnalysis:
    start: str
    nullable: frozenset[str]
    first: Mapping[str, frozenset[str]]
    follow: Mapping[str, frozenset[str]]
    table: Mapping[tuple[str, str], tuple[Production, ...]]
    conflicts: tuple[LL1Conflict, ...]
    nullable_rounds: tuple[tuple[str, ...], ...]
    first_rounds: tuple[Mapping[str, tuple[str, ...]], ...]
    follow_rounds: tuple[Mapping[str, tuple[str, ...]], ...]

    def to_data(self) -> dict:
        """返回键和值均稳定排序的 JSON 数据。"""
        return {
            "start": self.start,
            "nullable": sorted(self.nullable),
            "first": {name: sorted(self.first[name]) for name in sorted(self.first)},
            "follow": {name: sorted(self.follow[name]) for name in sorted(self.follow)},
            "table": [
                {
                    "nonterminal": key[0],
                    "lookahead": key[1],
                    "productions": [production.text() for production in self.table[key]],
                }
                for key in sorted(self.table)
            ],
            "conflicts": [
                {
                    "nonterminal": conflict.nonterminal,
                    "lookahead": conflict.lookahead,
                    "productions": [production.text() for production in conflict.productions],
                }
                for conflict in self.conflicts
            ],
        }


def normalize_grammar(grammar: Mapping[str, Sequence[Sequence[str]]]) -> dict[str, tuple[Production, ...]]:
    if not isinstance(grammar, Mapping) or not grammar:
        raise ValueError("grammar 必须是非空产生式映射")
    normalized = {}
    for left, alternatives in grammar.items():
        if type(left) is not str or not left:
            raise ValueError("非终结符必须为非空字符串")
        productions = []
        for raw_right in alternatives:
            if isinstance(raw_right, str):
                raise TypeError("产生式右部必须是符号序列，不能直接传字符串")
            right = tuple(raw_right)
            if any(type(symbol) is not str or not symbol for symbol in right):
                raise ValueError("文法符号必须为非空字符串")
            if EPSILON in right:
                if right != (EPSILON,):
                    raise ValueError("epsilon 必须单独构成产生式右部")
                right = ()
            productions.append(Production(left, right))
        if not productions:
            raise ValueError(f"非终结符 {left} 没有产生式")
        normalized[left] = tuple(productions)
    return normalized


def _nullable_with_rounds(productions: Mapping[str, tuple[Production, ...]]):
    nullable = set()
    rounds = []
    while True:
        updated = set(nullable)
        for left, alternatives in productions.items():
            if any(not production.right or all(symbol in nullable for symbol in production.right)
                   for production in alternatives):
                updated.add(left)
        if updated == nullable:
            return frozenset(nullable), tuple(rounds)
        nullable = updated
        rounds.append(tuple(sorted(nullable)))


def compute_nullable(grammar: Mapping[str, Sequence[Sequence[str]]]) -> frozenset[str]:
    return _nullable_with_rounds(normalize_grammar(grammar))[0]


def _first_of_sequence(sequence: Sequence[str], first: Mapping[str, set[str]],
                       nullable: frozenset[str], nonterminals: set[str]) -> set[str]:
    if not sequence:
        return {EPSILON}
    result = set()
    for symbol in sequence:
        if symbol in nonterminals:
            result.update(first[symbol] - {EPSILON})
            if symbol not in nullable:
                break
        else:
            result.add(symbol)
            break
    else:
        result.add(EPSILON)
    return result


def _first_with_rounds(productions: Mapping[str, tuple[Production, ...]],
                       nullable: frozenset[str]):
    nonterminals = set(productions)
    first = {name: set() for name in productions}
    rounds = []
    while True:
        updated = {name: set(values) for name, values in first.items()}
        for left, alternatives in productions.items():
            for production in alternatives:
                updated[left].update(_first_of_sequence(
                    production.right, first, nullable, nonterminals
                ))
        if updated == first:
            stable = {name: frozenset(first[name]) for name in sorted(first)}
            return stable, tuple(rounds)
        first = updated
        rounds.append({name: tuple(sorted(first[name])) for name in sorted(first)})


def compute_first(grammar: Mapping[str, Sequence[Sequence[str]]]) -> dict[str, frozenset[str]]:
    productions = normalize_grammar(grammar)
    nullable, _ = _nullable_with_rounds(productions)
    return _first_with_rounds(productions, nullable)[0]


def _follow_with_rounds(productions: Mapping[str, tuple[Production, ...]], start: str,
                        nullable: frozenset[str], first: Mapping[str, frozenset[str]]):
    nonterminals = set(productions)
    mutable_first = {name: set(values) for name, values in first.items()}
    follow = {name: set() for name in productions}
    follow[start].add(EOF)
    rounds = [{name: tuple(sorted(follow[name])) for name in sorted(follow)}]
    while True:
        updated = {name: set(values) for name, values in follow.items()}
        for left, alternatives in productions.items():
            for production in alternatives:
                for index, symbol in enumerate(production.right):
                    if symbol not in nonterminals:
                        continue
                    suffix = production.right[index + 1:]
                    suffix_first = _first_of_sequence(
                        suffix, mutable_first, nullable, nonterminals
                    )
                    updated[symbol].update(suffix_first - {EPSILON})
                    if EPSILON in suffix_first:
                        updated[symbol].update(follow[left])
        if updated == follow:
            stable = {name: frozenset(follow[name]) for name in sorted(follow)}
            return stable, tuple(rounds)
        follow = updated
        rounds.append({name: tuple(sorted(follow[name])) for name in sorted(follow)})


def compute_follow(grammar: Mapping[str, Sequence[Sequence[str]]], start: str) -> dict[str, frozenset[str]]:
    productions = normalize_grammar(grammar)
    if start not in productions:
        raise ValueError("开始符必须是文法中的非终结符")
    nullable, _ = _nullable_with_rounds(productions)
    first, _ = _first_with_rounds(productions, nullable)
    return _follow_with_rounds(productions, start, nullable, first)[0]


def _build_table(productions: Mapping[str, tuple[Production, ...]],
                 nullable: frozenset[str], first: Mapping[str, frozenset[str]],
                 follow: Mapping[str, frozenset[str]]):
    nonterminals = set(productions)
    mutable_first = {name: set(values) for name, values in first.items()}
    cells: dict[tuple[str, str], list[Production]] = {}
    for left, alternatives in productions.items():
        for production in alternatives:
            lookaheads = _first_of_sequence(
                production.right, mutable_first, nullable, nonterminals
            )
            targets = set(lookaheads - {EPSILON})
            if EPSILON in lookaheads:
                targets.update(follow[left])
            for lookahead in sorted(targets):
                cell = cells.setdefault((left, lookahead), [])
                if production not in cell:
                    cell.append(production)
    table = {key: tuple(cells[key]) for key in sorted(cells)}
    conflicts = tuple(
        LL1Conflict(key[0], key[1], table[key])
        for key in sorted(table) if len(table[key]) > 1
    )
    return table, conflicts


def build_table(grammar: Mapping[str, Sequence[Sequence[str]]], start: str):
    analysis = analyze_grammar(grammar, start)
    return analysis.table, analysis.conflicts


def analyze_grammar(grammar: Mapping[str, Sequence[Sequence[str]]], start: str) -> GrammarAnalysis:
    productions = normalize_grammar(grammar)
    if start not in productions:
        raise ValueError("开始符必须是文法中的非终结符")
    nullable, nullable_rounds = _nullable_with_rounds(productions)
    first, first_rounds = _first_with_rounds(productions, nullable)
    follow, follow_rounds = _follow_with_rounds(productions, start, nullable, first)
    table, conflicts = _build_table(productions, nullable, first, follow)
    return GrammarAnalysis(
        start, nullable, first, follow, table, conflicts,
        nullable_rounds, first_rounds, follow_rounds,
    )
