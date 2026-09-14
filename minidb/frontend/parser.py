"""核心 SQL 的手写递归下降 Parser，以及无状态 FrontendPort 实现。"""

from collections.abc import Callable, Collection, Sequence
from dataclasses import dataclass, fields, replace

from minidb.contracts.ast import (
    BinaryExpr, ColumnDef, CreateTableStmt, DeleteStmt, Expr, Identifier,
    InsertStmt, Literal, SelectStmt, Statement, UnaryExpr,
)
from minidb.contracts.errors import LexicalError, MiniDBError, SyntaxError
from minidb.contracts.extensions import ExtensionStatement
from minidb.contracts.results import TraceEvent
from minidb.contracts.source import Span
from minidb.contracts.tokens import Token, TokenKind as K

from .lexer import Lexer


# AST 使用统一比较符，输入中的 ==、<> 分别转换为 =、!=。
COMPARISONS = {
    K.EQ: "=", K.NE: "!=", K.LT: "<", K.LE: "<=", K.GT: ">", K.GE: ">=",
    K.EQ_ALIAS: "=", K.NE_ALIAS: "!=",
}
# 各类语法成分允许的起始 Token，同时用于生成“期望符号”错误提示。
LITERAL_START = (K.INTEGER, K.MINUS, K.STRING, K.FLOAT)
PRIMARY_START = (K.IDENTIFIER, *LITERAL_START, K.LPAREN)
EXPRESSION_START = (*PRIMARY_START, K.NOT)
STATEMENT_START = (K.CREATE, K.INSERT, K.SELECT, K.DELETE)
SUPPORTED_EXTENSIONS = frozenset({
    "update", "order_limit", "distinct", "join", "aggregate", "types", "arithmetic",
})
ParsedStatement = Statement | ExtensionStatement


@dataclass(frozen=True)
class QualifiedIdentifier:
    """仅在 JOIN 扩展内部使用；公共输出会立即转换为 NodeJSON。"""

    qualifier: str
    name: str
    span: Span


@dataclass(frozen=True)
class RecoveredStatement:
    statement_index: int
    statement: ParsedStatement


@dataclass(frozen=True)
class RecoveryDiagnostic:
    statement_index: int
    error: MiniDBError


@dataclass(frozen=True)
class RecoveryResult:
    statements: tuple[RecoveredStatement, ...]
    errors: tuple[RecoveryDiagnostic, ...]
    truncated: bool = False


def _extension_node_data(node: object) -> object:
    """编码核心 AST 和前端私有限定名，不把 Python 对象放入 payload。"""
    node_types = (
        Literal, Identifier, UnaryExpr, BinaryExpr, ColumnDef,
        CreateTableStmt, InsertStmt, SelectStmt, DeleteStmt, ExtensionStatement,
        QualifiedIdentifier,
    )
    result = [None]
    pending = [(node, result, 0)]
    while pending:
        value, parent, key = pending.pop()
        if isinstance(value, node_types):
            record = {
                "kind": type(value).__name__,
                "fields": {},
                "span": {
                    "start": {"offset": value.span.start.offset, "line": value.span.start.line,
                              "column": value.span.start.column},
                    "end": {"offset": value.span.end.offset, "line": value.span.end.line,
                            "column": value.span.end.column},
                },
            }
            parent[key] = record
            names = [field.name for field in fields(value) if field.name != "span"]
            for name in names:
                record["fields"][name] = None
            for name in reversed(names):
                pending.append((getattr(value, name), record["fields"], name))
        elif isinstance(value, dict):
            target = {name: None for name in value}
            parent[key] = target
            for name, child in reversed(list(value.items())):
                pending.append((child, target, name))
        elif isinstance(value, (list, tuple)):
            target = [None] * len(value)
            parent[key] = target
            for index in reversed(range(len(value))):
                pending.append((value[index], target, index))
        elif value is None or type(value) in (str, int, float, bool):
            parent[key] = value
        else:
            raise TypeError(f"扩展节点含不支持的字段类型：{type(value).__name__}")
    return result[0]


def _extension_set(features: Collection[str]) -> frozenset[str]:
    if isinstance(features, str):
        raise TypeError("enabled_extensions 需要名称集合，例如 {'update'}，不能传单个字符串")
    enabled = frozenset(features)
    unknown = enabled - SUPPORTED_EXTENSIONS
    if unknown:
        raise MiniDBError("UNSUPPORTED", "UNKNOWN_EXTENSION",
                          f"未知前端扩展：{', '.join(sorted(map(str, unknown)))}")
    return enabled


class Parser:
    """Token 下标只前进；不查询元数据、不求值、不写磁盘。"""

    def __init__(self, tokens: Sequence[Token], *, max_nesting: int = 64,
                 enabled_extensions: Collection[str] = ()):
        self.tokens = tuple(tokens)
        if not self.tokens or self.tokens[-1].kind != K.EOF:
            raise ValueError("Parser 的 Token 序列必须以 EOF 结束")
        if any(t.kind == K.EOF for t in self.tokens[:-1]):
            raise ValueError("EOF 只能在 Token 序列末尾出现一次")
        if not 1 <= max_nesting <= 64:
            raise ValueError("max_nesting 必须在 1 到 64 之间")
        # index 指向当前 Token；nesting 记录当前进入了多少层表达式括号。
        self.index = 0
        self.nesting = 0
        self.max_nesting = max_nesting
        self.enabled_extensions = _extension_set(enabled_extensions)
        self.allow_qualified = False
        self._uses_types = False
        self._uses_arithmetic = False

    def peek(self) -> Token:
        return self.tokens[self.index]

    def consume(self) -> Token:
        token = self.peek()
        # EOF 保持在末尾，防止缺少符号时继续前进而产生下标越界。
        if token.kind != K.EOF:
            self.index += 1
        return token

    def _unexpected(self, expected: Sequence[K | str], *, code: str = "UNEXPECTED_TOKEN",
                    reason: str = "") -> SyntaxError:
        # 集中组织实际符号、期望集合和源码位置，让各解析函数的报错一致。
        token = self.peek()
        expected_names = tuple(dict.fromkeys(kind.value if isinstance(kind, K) else kind
                                            for kind in expected))
        actual = "EOF" if token.kind == K.EOF else repr(token.lexeme)
        message = f"遇到 {actual} ({token.kind.value})"
        if expected_names:
            message += f"；期望 {' | '.join(expected_names)}"
        if reason:
            message = f"{reason}；{message}"
        return SyntaxError(code, message, token.span, {
            "actual": token.kind.value, "lexeme": token.lexeme,
            "expected": expected_names,
        })

    def expect(self, *kinds: K) -> Token:
        # 文法要求某个符号必须出现时使用此方法，匹配成功才消费 Token。
        if self.peek().kind not in kinds:
            raise self._unexpected(kinds)
        return self.consume()

    def _span_since(self, first: Token) -> Span:
        # 语句范围到最后已消费的 Token 为止，不包含尚未消费的分号。
        return Span(first.span.start, self.tokens[self.index - 1].span.end)

    def parse(self) -> list[ParsedStatement]:
        statements = []
        while self.peek().kind != K.EOF:
            if self.peek().kind == K.SEMICOLON:
                # 连续分号代表空语句，跳过即可，不生成空 AST。
                self.consume()
                continue
            statement = self.parse_statement()
            # 最后一条可以直接接 EOF，其余语句之间必须用分号分隔。
            if self.peek().kind not in (K.SEMICOLON, K.EOF):
                raise self._unexpected((K.SEMICOLON, K.EOF), reason="语句之间需要分号，不支持额外后缀")
            statements.append(statement)
        return statements

    def synchronize(self) -> None:
        """丢弃当前错误语句并停在下一条语句开头。"""
        if self.peek().kind == K.EOF:
            return
        if self.peek().kind == K.SEMICOLON:
            self.consume()
            return
        # 至少消费一个 Token，防止恢复循环在同一错误位置反复报告。
        self.consume()
        while self.peek().kind not in (K.SEMICOLON, K.EOF):
            self.consume()
        if self.peek().kind == K.SEMICOLON:
            self.consume()

    def parse_recovering(self, *, max_errors: int = 20) -> RecoveryResult:
        if type(max_errors) is not int or max_errors < 1:
            raise ValueError("max_errors 必须为正整数")
        statements = []
        errors = []
        statement_index = 0
        truncated = False
        while self.peek().kind != K.EOF:
            if self.peek().kind == K.SEMICOLON:
                self.consume()
                continue
            statement_index += 1
            try:
                statement = self.parse_statement()
                if self.peek().kind not in (K.SEMICOLON, K.EOF):
                    raise self._unexpected((K.SEMICOLON, K.EOF),
                                           reason="语句之间需要分号，不支持额外后缀")
            except SyntaxError as error:
                errors.append(RecoveryDiagnostic(statement_index, error))
                if len(errors) >= max_errors:
                    truncated = True
                    break
                self.synchronize()
                continue
            statements.append(RecoveredStatement(statement_index, statement))
            if self.peek().kind == K.SEMICOLON:
                self.consume()
        return RecoveryResult(tuple(statements), tuple(errors), truncated)

    def parse_statement(self) -> ParsedStatement:
        self._uses_types = False
        self._uses_arithmetic = False
        if self._at_word("update"):
            self._require_extension("update")
            statement = self.parse_update()
        else:
            # 根据语句的第一个关键字分派到对应的递归下降入口。
            methods = {
                K.CREATE: self.parse_create_table,
                K.INSERT: self.parse_insert,
                K.SELECT: self.parse_select,
                K.DELETE: self.parse_delete,
            }
            method = methods.get(self.peek().kind)
            if method is None:
                raise self._unexpected(STATEMENT_START, reason="核心仅支持 CREATE、INSERT、SELECT、DELETE")
            statement = method()
        return self._wrap_detected_extension(statement)

    def _at_word(self, word: str) -> bool:
        # 复用既有保留字种别，不新增/改动冻结 TokenKind；字符串值不会误匹配。
        token = self.peek()
        return token.kind == K.UNSUPPORTED_KEYWORD and token.value == word

    def _at_dot(self) -> bool:
        return self.peek().kind == K.UNSUPPORTED_KEYWORD and self.peek().value == "."

    def _statement_contains_word(self, words: set[str]) -> bool:
        for token in self.tokens[self.index:]:
            if token.kind in (K.SEMICOLON, K.EOF):
                return False
            if token.kind == K.UNSUPPORTED_KEYWORD and token.value in words:
                return True
        return False

    def _wrap_detected_extension(self, statement: ParsedStatement) -> ParsedStatement:
        detected = [name for name, used in (
            ("types", self._uses_types), ("arithmetic", self._uses_arithmetic)
        ) if used]
        if not detected:
            return statement
        if len(detected) > 1 or isinstance(statement, ExtensionStatement):
            raise self._unexpected((K.SEMICOLON, K.EOF), code="UNSUPPORTED_COMBINATION",
                                   reason="当前 v1 扩展不能在同一语句中嵌套组合")
        feature = detected[0]
        return ExtensionStatement(feature, 1, {"statement": _extension_node_data(statement)},
                                  statement.span)

    def _expect_word(self, word: str) -> Token:
        if not self._at_word(word):
            raise self._unexpected((word.upper(),))
        return self.consume()

    def _require_extension(self, feature: str) -> None:
        if feature not in self.enabled_extensions:
            raise self._unexpected((feature.upper(),), code="EXTENSION_DISABLED",
                                   reason=f"扩展 {feature} 未启用，请显式配置 enabled_extensions")

    def parse_assignment(self) -> dict:
        from .formatter import ast_to_data

        column = self.parse_identifier()
        self.expect(K.EQ)
        expression = self.parse_expression()
        return {"column": ast_to_data(column), "expr": ast_to_data(expression)}

    def parse_update(self) -> ExtensionStatement:
        from .formatter import ast_to_data

        first = self._expect_word("update")
        table = self.parse_identifier()
        self._expect_word("set")
        assignments = list(self._comma_list(self.parse_assignment,
                                            (K.WHERE, K.SEMICOLON, K.EOF)))
        where = self.parse_where_optional()
        return ExtensionStatement("update", 1, {
            "table": ast_to_data(table), "assignments": assignments,
            "where": ast_to_data(where),
        }, self._span_since(first))

    def parse_identifier(self) -> Identifier:
        # 名称和位置一起进入 AST，后续成员 B 才能准确定位未知表列。
        token = self.expect(K.IDENTIFIER)
        return Identifier(token.value, token.span)

    def parse_column_ref(self) -> Identifier | QualifiedIdentifier:
        qualifier = self.parse_identifier()
        if not self._at_dot():
            return qualifier
        self.consume()
        name = self.parse_identifier()
        return QualifiedIdentifier(qualifier.name, name.name,
                                   Span(qualifier.span.start, name.span.end))

    def _comma_list(self, item: Callable, terminators: tuple[K, ...]) -> tuple:
        """至少一项；缺分隔符时同时报告逗号和可能的结束符。"""
        items = [item()]
        while True:
            if self.peek().kind == K.COMMA:
                self.consume()
                # 逗号之后必须还有一项，因此尾随逗号不能被静默接受。
                items.append(item())
            elif self.peek().kind in terminators:
                return tuple(items)
            else:
                raise self._unexpected((K.COMMA, *terminators))

    def parse_column_def(self) -> ColumnDef:
        # 一项列定义由“列名 + 类型”组成，基础存储类型只有 INT、VARCHAR。
        name = self.parse_identifier()
        if self._at_word("float") or self._at_word("bool"):
            self._require_extension("types")
            dtype = self.consume()
            self._uses_types = True
            dtype_name = dtype.value.upper()
        else:
            dtype = self.expect(K.INT, K.VARCHAR)
            dtype_name = dtype.kind.value
        return ColumnDef(name, dtype_name, Span(name.span.start, dtype.span.end))

    def parse_create_table(self) -> CreateTableStmt:
        # 按 CREATE TABLE 表名(列定义,...) 的顺序消费输入并构造建表节点。
        first = self.expect(K.CREATE)
        self.expect(K.TABLE)
        name = self.parse_identifier()
        self.expect(K.LPAREN)
        columns = self._comma_list(self.parse_column_def, (K.RPAREN,))
        self.expect(K.RPAREN)
        return CreateTableStmt(name, columns, self._span_since(first))

    def parse_literal(self) -> Literal:
        if any(self._at_word(word) for word in ("true", "false", "null")):
            self._require_extension("types")
            token = self.consume()
            self._uses_types = True
            value = {"true": True, "false": False, "null": None}[token.value]
            return Literal(value, token.span)
        token = self.expect(*LITERAL_START)
        if token.kind == K.MINUS:
            # 负整数由负号和整数 Token 合并，节点位置同时覆盖负号和数字。
            number = self.expect(K.INTEGER)
            return Literal(-number.value, Span(token.span.start, number.span.end))
        return Literal(token.value, token.span)

    def parse_insert(self) -> InsertStmt:
        first = self.expect(K.INSERT)
        self.expect(K.INTO)
        table = self.parse_identifier()
        columns = None
        # None 表示省略列列表；显式列表保留原顺序，不能在这里重排值。
        if self.peek().kind == K.LPAREN:
            self.consume()
            columns = self._comma_list(self.parse_identifier, (K.RPAREN,))
            self.expect(K.RPAREN)
        self.expect(K.VALUES)
        self.expect(K.LPAREN)
        values = self._comma_list(self.parse_literal, (K.RPAREN,))
        self.expect(K.RPAREN)
        return InsertStmt(table, columns, values, self._span_since(first))

    def parse_projection(self) -> tuple[Identifier, ...] | None:
        # 契约用 None 表示 SELECT *，普通列列表保留顺序和重复列。
        if self.peek().kind == K.STAR:
            self.consume()
            return None
        return self._comma_list(self.parse_identifier, (K.FROM,))

    def parse_where_optional(self) -> Expr | None:
        # SELECT 和 DELETE 共用此入口，无 WHERE 时返回 None。
        if self.peek().kind == K.WHERE:
            self.consume()
            return self.parse_expression()
        return None

    def parse_select(self) -> SelectStmt | ExtensionStatement:
        first = self.expect(K.SELECT)
        join_words = {"join", "inner", "left", "right", "outer"}
        if self._statement_contains_word(join_words) and "join" not in self.enabled_extensions:
            # 报错位置指向扩展触发词，而不是触发词之前的限定名点号。
            for index in range(self.index, len(self.tokens)):
                token = self.tokens[index]
                if token.kind == K.UNSUPPORTED_KEYWORD and token.value in join_words:
                    self.index = index
                    break
            self._require_extension("join")
        if self._statement_contains_word(join_words):
            return self.parse_join(first)
        aggregate_words = {"count", "sum", "avg", "min", "max", "group", "as"}
        if self._statement_contains_word(aggregate_words) and "aggregate" not in self.enabled_extensions:
            for index in range(self.index, len(self.tokens)):
                token = self.tokens[index]
                if token.kind == K.UNSUPPORTED_KEYWORD and token.value in aggregate_words:
                    self.index = index
                    break
            self._require_extension("aggregate")
        if self._statement_contains_word(aggregate_words):
            return self.parse_aggregate(first)
        distinct = self._at_word("distinct")
        if distinct:
            self._require_extension("distinct")
            self.consume()
            if self.peek().kind not in (K.IDENTIFIER, K.STAR):
                raise self._unexpected((K.IDENTIFIER, K.STAR))
        # 查询的基础结构是投影、数据表和可选条件；此处只构树，不读取数据。
        columns = self.parse_projection()
        self.expect(K.FROM)
        table = self.parse_identifier()
        where = self.parse_where_optional()
        query = SelectStmt(table, columns, where, self._span_since(first))
        if self._at_word("order") or self._at_word("limit"):
            if distinct:
                raise self._unexpected((K.SEMICOLON, K.EOF),
                                       code="UNSUPPORTED_COMBINATION",
                                       reason="distinct v1 不支持与 ORDER BY/LIMIT 组合")
            self._require_extension("order_limit")
            return self.parse_query_suffix(query, first)
        if distinct:
            from .formatter import ast_to_data

            return ExtensionStatement("distinct", 1, {"query": ast_to_data(query)},
                                      self._span_since(first))
        return query

    def parse_table_ref(self) -> dict:
        table = self.parse_identifier()
        alias = None
        if self._at_word("as"):
            self.consume()
            alias = self.parse_identifier()
        elif self.peek().kind == K.IDENTIFIER:
            alias = self.parse_identifier()
        if alias is None:
            alias = Identifier(table.name, table.span)
        return {"table": _extension_node_data(table), "alias": _extension_node_data(alias)}

    def parse_join_projection(self) -> list[dict] | None:
        if self.peek().kind == K.STAR:
            self.consume()
            return None
        return list(self._comma_list(self.parse_column_ref, (K.FROM,)))

    def parse_join(self, first: Token) -> ExtensionStatement:
        if self._at_word("distinct"):
            raise self._unexpected((K.IDENTIFIER, K.STAR), code="UNSUPPORTED_COMBINATION",
                                   reason="join v1 不支持 DISTINCT 组合")
        columns = self.parse_join_projection()
        self.expect(K.FROM)
        left = self.parse_table_ref()
        if any(self._at_word(word) for word in ("left", "right", "outer")):
            raise self._unexpected(("INNER", "JOIN"), code="UNSUPPORTED_JOIN_TYPE",
                                   reason="join v1 只支持两表 INNER JOIN")
        if self._at_word("inner"):
            self.consume()
        self._expect_word("join")
        right = self.parse_table_ref()
        self._expect_word("on")
        self.allow_qualified = True
        try:
            on = self.parse_expression()
            where = self.parse_where_optional()
        finally:
            self.allow_qualified = False
        if self._at_word("join") or self._at_word("inner"):
            raise self._unexpected((K.SEMICOLON, K.EOF), code="UNSUPPORTED_JOIN_CHAIN",
                                   reason="join v1 只允许连接两张表")
        return ExtensionStatement("join", 1, {
            "left": left,
            "right": right,
            "join_type": "INNER",
            "on": _extension_node_data(on),
            "columns": _extension_node_data(columns),
            "where": _extension_node_data(where),
        }, self._span_since(first))

    def parse_select_item(self) -> dict:
        functions = {"count", "sum", "avg", "min", "max"}
        function = None
        if any(self._at_word(name) for name in functions):
            function = self.consume().value.upper()
            self.expect(K.LPAREN)
            if self.peek().kind == K.STAR:
                if function != "COUNT":
                    raise self._unexpected((K.IDENTIFIER,), code="INVALID_AGGREGATE_ARGUMENT",
                                           reason="只有 COUNT 支持星号参数")
                self.consume()
                column = "*"
            else:
                column = _extension_node_data(self.parse_identifier())
            self.expect(K.RPAREN)
        else:
            column = _extension_node_data(self.parse_identifier())
        alias = None
        if self._at_word("as"):
            self.consume()
            alias = _extension_node_data(self.parse_identifier())
        return {"function": function, "column": column, "alias": alias}

    def parse_aggregate(self, first: Token) -> ExtensionStatement:
        if self._at_word("distinct"):
            raise self._unexpected((K.IDENTIFIER, "COUNT", "SUM", "AVG", "MIN", "MAX"),
                                   code="UNSUPPORTED_COMBINATION",
                                   reason="aggregate v1 不支持 DISTINCT 组合")
        items = list(self._comma_list(self.parse_select_item, (K.FROM,)))
        self.expect(K.FROM)
        table = self.parse_identifier()
        where = self.parse_where_optional()
        group_by = []
        if self._at_word("group"):
            self.consume()
            self._expect_word("by")
            group_by = list(self._comma_list(self.parse_identifier,
                                             (K.SEMICOLON, K.EOF)))
        return ExtensionStatement("aggregate", 1, {
            "table": _extension_node_data(table),
            "select_items": items,
            "group_by": _extension_node_data(group_by),
            "where": _extension_node_data(where),
        }, self._span_since(first))

    def parse_order_terms(self) -> list[dict]:
        from .formatter import ast_to_data

        terms = []
        while True:
            column = self.parse_identifier()
            direction = "ASC"
            if self._at_word("asc") or self._at_word("desc"):
                direction = self.consume().value.upper()
            terms.append({"column": ast_to_data(column), "direction": direction})
            if self.peek().kind != K.COMMA:
                return terms
            self.consume()

    def parse_limit(self) -> int:
        self._expect_word("limit")
        if self.peek().kind != K.INTEGER:
            raise self._unexpected((K.INTEGER,), code="INVALID_LIMIT",
                                   reason="LIMIT 需要非负整数")
        return self.consume().value

    def parse_query_suffix(self, query: SelectStmt, first: Token) -> ExtensionStatement:
        from .formatter import ast_to_data

        order_by = []
        limit = None
        if self._at_word("order"):
            self.consume()
            self._expect_word("by")
            order_by = self.parse_order_terms()
        if self._at_word("limit"):
            limit = self.parse_limit()
        if self._at_word("order") or self._at_word("limit"):
            raise self._unexpected((K.SEMICOLON, K.EOF), code="INVALID_CLAUSE_ORDER",
                                   reason="ORDER BY 必须在 LIMIT 之前，子句不可重复")
        return ExtensionStatement("order_limit", 1, {
            "query": ast_to_data(query), "order_by": order_by, "limit": limit,
        }, self._span_since(first))

    def parse_delete(self) -> DeleteStmt:
        # DELETE 无 WHERE 是合法的全表删除语法，具体删除由执行器完成。
        first = self.expect(K.DELETE)
        self.expect(K.FROM)
        table = self.parse_identifier()
        where = self.parse_where_optional()
        return DeleteStmt(table, where, self._span_since(first))

    def parse_expression(self) -> Expr:
        # 从结合最松的 OR 层进入，依次下探到 AND、NOT、比较和基本项。
        return self.parse_or()

    def parse_or(self) -> Expr:
        # 先完成 AND 子树，保证 a OR b AND c 解析为 a OR (b AND c)。
        left = self.parse_and()
        while self.peek().kind == K.OR:
            self.consume()
            right = self.parse_and()
            left = BinaryExpr("OR", left, right, Span(left.span.start, right.span.end))
        return left

    def parse_and(self) -> Expr:
        left = self.parse_not()
        while self.peek().kind == K.AND:
            self.consume()
            right = self.parse_not()
            # 新节点复用已有左树，使同级的多个 AND 按从左到右的顺序结合。
            left = BinaryExpr("AND", left, right, Span(left.span.start, right.span.end))
        return left

    def parse_not(self) -> Expr:
        # 显式保存 NOT 栈，等价于 not_expr 的右递归，避免长 NOT 链耗尽调用栈。
        prefixes = []
        while self.peek().kind == K.NOT:
            prefixes.append(self.consume())
        arithmetic_prefix = self.peek().kind in (K.PLUS, K.MINUS) and "arithmetic" in self.enabled_extensions
        extended_literal = any(self._at_word(word) for word in ("true", "false", "null"))
        if self.peek().kind not in PRIMARY_START and not arithmetic_prefix and not extended_literal:
            raise self._unexpected(EXPRESSION_START)
        # 先得到完整比较，再包上 NOT：NOT age=18 应是 NOT(age=18)。
        operand = self.parse_comparison()
        for token in reversed(prefixes):
            operand = UnaryExpr("NOT", operand, Span(token.span.start, operand.span.end))
        return operand

    def parse_comparison(self) -> Expr:
        parse_operand = self.parse_additive if "arithmetic" in self.enabled_extensions else self.parse_primary
        left = parse_operand()
        if self.peek().kind in COMPARISONS:
            operator = self.consume()
            right = parse_operand()
            left = BinaryExpr(COMPARISONS[operator.kind], left, right,
                              Span(left.span.start, right.span.end))
            if self.peek().kind in COMPARISONS:
                # 基础文法不支持 a<b<c，第二个比较符处立即报错。
                raise self._unexpected((K.AND, K.OR, K.RPAREN, K.SEMICOLON, K.EOF),
                                       code="CHAINED_COMPARISON", reason="不支持链式比较")
        return left

    def parse_additive(self) -> Expr:
        left = self.parse_multiplicative()
        while self.peek().kind in (K.PLUS, K.MINUS):
            operator = self.consume()
            right = self.parse_multiplicative()
            self._uses_arithmetic = True
            left = BinaryExpr(operator.value, left, right, Span(left.span.start, right.span.end))
        return left

    def parse_multiplicative(self) -> Expr:
        left = self.parse_unary()
        while self.peek().kind in (K.STAR, K.SLASH):
            operator = self.consume()
            right = self.parse_unary()
            self._uses_arithmetic = True
            left = BinaryExpr(operator.value, left, right, Span(left.span.start, right.span.end))
        return left

    def parse_unary(self) -> Expr:
        if self.peek().kind in (K.PLUS, K.MINUS):
            operator = self.consume()
            operand = self.parse_unary()
            self._uses_arithmetic = True
            return UnaryExpr(operator.value, operand, Span(operator.span.start, operand.span.end))
        return self.parse_primary()

    def parse_primary(self) -> Expr:
        kind = self.peek().kind
        if kind == K.IDENTIFIER:
            return self.parse_column_ref() if self.allow_qualified else self.parse_identifier()
        if kind in LITERAL_START or any(self._at_word(word) for word in ("true", "false", "null")):
            return self.parse_literal()
        if kind == K.LPAREN:
            if self.nesting >= self.max_nesting:
                raise self._unexpected(PRIMARY_START, code="NESTING_LIMIT",
                                       reason=f"括号嵌套超过前端资源限制 {self.max_nesting} 层")
            first = self.consume()
            self.nesting += 1
            try:
                expr = self.parse_expression()
                last = self.expect(K.RPAREN)
            finally:
                # 即使括号内解析失败，也要恢复嵌套计数。
                self.nesting -= 1
            # AST 不额外保存括号节点，但根表达式的范围需要包含外层括号。
            return replace(expr, span=Span(first.span.start, last.span.end))
        raise self._unexpected(PRIMARY_START)


class Frontend:
    def __init__(self, on_trace: Callable[[TraceEvent], None] | None = None, *,
                 enabled_extensions: Collection[str] = ()):
        self.on_trace = on_trace
        self.enabled_extensions = _extension_set(enabled_extensions)

    def parse(self, source: str) -> list[ParsedStatement]:
        # 每次建立新 Lexer/Parser；失败和上次调用均不会污染后续状态。
        from .formatter import format_ast, format_tokens

        tokens = Lexer(source).tokenize()
        if self.on_trace is not None:
            # 词法成功即可展示 Token；语法失败时不会发出成功的 AST 事件。
            self.on_trace(TraceEvent("TOKEN", format_tokens(tokens)))
        statements = Parser(tokens, enabled_extensions=self.enabled_extensions).parse()
        if self.on_trace is not None:
            # 只有整个脚本解析成功后才输出 AST，避免把部分成功误当全部成功。
            self.on_trace(TraceEvent("AST", format_ast(statements)))
        return statements

    def parse_recovering(self, source: str, *, max_errors: int = 20) -> RecoveryResult:
        """面向编辑器诊断的恢复入口；结果不会自动进入执行器。"""
        try:
            tokens = Lexer(source).tokenize()
        except LexicalError as error:
            # 未闭合字符串或注释无法可靠找到后续语句边界，因此立即停止。
            statement_index = source[:error.span.start.offset].count(";") + 1
            return RecoveryResult((), (RecoveryDiagnostic(statement_index, error),), True)
        return Parser(tokens, enabled_extensions=self.enabled_extensions).parse_recovering(
            max_errors=max_errors
        )


def parse_recovering(source: str, *, max_errors: int = 20,
                     enabled_extensions: Collection[str] = ()) -> RecoveryResult:
    return Frontend(enabled_extensions=enabled_extensions).parse_recovering(
        source, max_errors=max_errors
    )
