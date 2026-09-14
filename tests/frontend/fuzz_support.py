"""A-E10 的确定性 SQL 生成、非法变异、失败记录和用例缩减工具。"""

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
import random

from minidb.contracts.errors import MiniDBError
from minidb.frontend import Frontend


@dataclass(frozen=True)
class GeneratedCase:
    sql: str
    expected_kind: str
    summary: tuple[tuple[str, object], ...]


class FailureCategory(StrEnum):
    CRASH = "Crash"
    WRONG_ACCEPT = "Wrong Accept"
    WRONG_REJECT = "Wrong Reject"
    POSITION_ERROR = "Position Error"


@dataclass(frozen=True)
class FailureRecord:
    seed: int
    sql: str
    category: FailureCategory
    exception_type: str | None
    message: str
    replay_command: str


def _identifier(rng: random.Random) -> str:
    return rng.choice(("student", "course", "score", "t", "records"))


def _literal(rng: random.Random) -> str:
    if rng.randrange(3) == 0:
        return "'" + rng.choice(("Alice", "张三", "Tom''s", "x")) + "'"
    return str(rng.randrange(-20, 101))


def _expression(rng: random.Random, depth: int, maximum: int) -> str:
    column = rng.choice(("id", "age", "score"))
    comparison = f"{column}{rng.choice(('=', '!=', '<', '<=', '>', '>='))}{_literal(rng)}"
    if depth >= maximum or rng.randrange(3) == 0:
        return comparison
    if rng.randrange(4) == 0:
        return "NOT " + _expression(rng, depth + 1, maximum)
    left = _expression(rng, depth + 1, maximum)
    right = _expression(rng, depth + 1, maximum)
    return f"({left} {rng.choice(('AND', 'OR'))} {right})"


def generate_valid(seed: int, count: int, max_expression_depth: int = 4) -> tuple[GeneratedCase, ...]:
    if type(seed) is not int or type(count) is not int or count < 0:
        raise ValueError("seed 必须为整数且 count 必须为非负整数")
    if type(max_expression_depth) is not int or not 0 <= max_expression_depth <= 8:
        raise ValueError("max_expression_depth 必须在 0 到 8 之间")
    rng = random.Random(seed)
    result = []
    for _ in range(count):
        kind = rng.choice(("CreateTableStmt", "InsertStmt", "SelectStmt", "DeleteStmt"))
        table = _identifier(rng)
        if kind == "CreateTableStmt":
            sql = f"CREATE TABLE {table}(id INT,name VARCHAR);"
            summary = (("columns", 2),)
        elif kind == "InsertStmt":
            sql = f"INSERT INTO {table}(id,name) VALUES({_literal(rng)},{_literal(rng)});"
            summary = (("values", 2),)
        elif kind == "SelectStmt":
            columns = rng.choice(("*", "id", "name,id", "id,id"))
            has_where = bool(rng.randrange(2))
            suffix = f" WHERE {_expression(rng, 0, max_expression_depth)}" if has_where else ""
            sql = f"SELECT {columns} FROM {table}{suffix};"
            summary = (("has_where", has_where), ("star", columns == "*"))
        else:
            has_where = bool(rng.randrange(2))
            suffix = f" WHERE {_expression(rng, 0, max_expression_depth)}" if has_where else ""
            sql = f"DELETE FROM {table}{suffix};"
            summary = (("has_where", has_where),)
        result.append(GeneratedCase(sql, kind, summary))
    return tuple(result)


def mutate_invalid(sql: str, mutation: str) -> str:
    if mutation == "remove_from":
        marker = " FROM "
        if marker not in sql.upper():
            raise ValueError("remove_from 需要含 FROM 的 SQL")
        index = sql.upper().index(marker)
        return sql[:index] + " " + sql[index + len(marker):]
    if mutation == "unterminated_string":
        index = sql.rfind("'")
        if index < 0:
            raise ValueError("unterminated_string 需要含字符串字面量的 SQL")
        return sql[:index] + sql[index + 1:]
    raise ValueError(f"未知变异：{mutation}")


def minimize(sql: str, predicate: Callable[[str], bool]) -> str:
    """逐字符确定性缩减；仅接受仍满足同一失败谓词的候选。"""
    if not predicate(sql):
        raise ValueError("原始输入不满足失败谓词")
    current = sql
    changed = True
    while changed:
        changed = False
        for index in range(len(current)):
            candidate = current[:index] + current[index + 1:]
            if predicate(candidate):
                current = candidate
                changed = True
                break
    return current


def record_failure(seed: int, sql: str, runner: Callable[[str], object], *,
                   expected_valid: bool = True) -> FailureRecord | None:
    try:
        runner(sql)
    except MiniDBError as error:
        if not expected_valid:
            return None
        return FailureRecord(seed, sql, FailureCategory.WRONG_REJECT,
                             type(error).__name__, str(error), _replay(seed, sql))
    except Exception as error:
        return FailureRecord(seed, sql, FailureCategory.CRASH,
                             type(error).__name__, str(error), _replay(seed, sql))
    if expected_valid:
        return None
    return FailureRecord(seed, sql, FailureCategory.WRONG_ACCEPT,
                         None, "非法 SQL 被接受", _replay(seed, sql))


def _replay(seed: int, sql: str) -> str:
    return f"seed={seed}; Frontend().parse({sql!r})"


def default_runner(sql: str):
    return Frontend().parse(sql)
