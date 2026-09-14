# 成员 A 扩展 A-E01 至 A-E10

本目录交付成员 A 的 A-E01 至 A-E10。A-E01 至 A-E07提供扩展语法和 JSON payload；A-E08提供独立错误恢复入口；A-E09提供文法集合与预测表分析；A-E10提供测试专用的 SQL 生成、变异、失败记录和缩减工具。默认仍只接受基础 SQL，不包含其他成员负责的执行和存储能力。

## 使用

```python
from minidb.frontend import Frontend

frontend = Frontend(enabled_extensions={"update", "order_limit", "distinct"})
statements = frontend.parse("""
UPDATE student SET age=21,name='李四' WHERE id=1;
SELECT id FROM student ORDER BY age DESC,id LIMIT 2;
SELECT DISTINCT name,age FROM student;
""")
for statement in statements:
    print(statement.feature, statement.version, statement.payload)
```

运行本机演示：

```powershell
cd D:\big\mini-db
$env:PYTHONUTF8 = '1'
.\.venv\Scripts\python.exe -m demo.frontend_demo --file demo/extensions.sql --enable update --enable order_limit --enable distinct
```

`--enable` 可以重复传入。不开启对应功能时，首次遇到其关键字便抛 `SYNTAX/EXTENSION_DISABLED`，不会静默启用；未知配置名称报 `UNSUPPORTED/UNKNOWN_EXTENSION`。字符串值必须放集合中，例如 `{"update"}`，不能传 `"update"`。

启用扩展后，`Frontend.parse`/`Parser.parse` 的结果可能包含 `ExtensionStatement`；未使用扩展的语句仍返回原来的核心 Statement，字段和 Span 不变。冻结 `FrontendPort` 和 `ast.Statement` 的核心定义没有改动；整合层启用扩展前必须能显式分派扩展容器，不能直接送入只支持核心 Statement 的 B 编译器。

## 文法和输入边界

```ebnf
update      = UPDATE name SET assignment {"," assignment} [WHERE expr]
assignment  = name "=" expr
order_limit = select [ORDER BY order_term {"," order_term}] [LIMIT INTEGER]
order_term  = name [ASC | DESC]
distinct    = SELECT DISTINCT ("*" | names) FROM name [WHERE expr]
```

order_limit 只有真正出现 ORDER BY 或 LIMIT 才包装为扩展；无后缀时仍是核心 SELECT。expr、name、names 沿用核心文法。UPDATE 的 SET 赋值符只接受 `=`；WHERE 比较继续兼容 `==` 和 `<>`。一般算术属于 A-E07，`SET age=age+1` 当前会在 `+` 报语法错误。

| 任务 | feature/version | payload 键 | 保留规则 |
|---|---|---|---|
| A-E01 | update / 1 | table、assignments、where | assignments 为列表，每项 column/expr 均为 NodeJSON；保留重复赋值和原顺序，无 WHERE 为 null |
| A-E02 | order_limit / 1 | query、order_by、limit | query 是核心 SelectStmtJSON；排序项 column/direction，默认 ASC；limit 缺省 null，0 合法 |
| A-E03 | distinct / 1 | query | query 是没有 DISTINCT 字段的核心 SelectStmtJSON；星号 columns=null；显式投影重复项不删除 |
| A-E04 | join / 1 | left、right、join_type、on、columns、where | 两表 INNER JOIN；限定列编码为 QualifiedIdentifier |
| A-E05 | aggregate / 1 | table、select_items、group_by、where | 保留投影与分组顺序；只有 COUNT 接受星号 |
| A-E06 | types / 1 | statement | FLOAT/BOOL 列与 TRUE/FALSE/NULL 字面量触发包装 |
| A-E07 | arithmetic / 1 | statement | 一元、乘除、加减按固定优先级构树，不提前求值 |
| A-E08 | error_recovery / 1 | statements、errors、truncated | 独立入口，保存原语句序号和诊断上限状态 |
| A-E09 | grammar_tooling / 1 | nullable、first、follow、table、conflicts | 使用不动点算法，稳定排序并保留冲突产生式 |
| A-E10 | fuzzing / 1 | seed、输入、摘要、失败分类 | 固定种子生成、保证非法的变异和确定性缩减 |

NodeJSON 固定 `{kind,fields,span}`。每个 Span 含 start/end，坐标仍对应原 SQL，而不是删除关键字或后缀后重新解析的字符串。排序的内层 query Span 只覆盖核心 SELECT 部分，外层 Span 覆盖整个后缀。DISTINCT 的内层 query 没有独立 DISTINCT 字段，其 Span 仍覆盖真实输入的 SELECT 语句。

LIMIT 只接受非负 INTEGER Token，不接受负号、正号、小数、字符串或标识符，不自行施加手册未规定的 INT32 上限。ORDER BY 在 LIMIT 之前，重复子句或逆序报 `INVALID_CLAUSE_ORDER`。列是否存在、是否可以排序、赋值类型是否匹配均交给 B。

DISTINCT 只能紧跟 SELECT 出现一次。`SELECT DISTINCT id FROM t ORDER BY id` 和带 LIMIT 的形式明确报 `UNSUPPORTED_COMBINATION`，因为本次三份 v1 任务没有定义组合 payload；不能丢失其中一项含义，也不私自嵌套另一种扩展协议。

## 快照和契约补齐

十个 `frontend_*_v1.json` 是手册字段的对照样例或工具配置。期望摘要由规范手工给出，不以被测 Parser 输出作为唯一真值。它们用于断言和整合对照，不替代公共手册规范。

前一阶段只初始化了 A 的最小基线，缺少手册要求的扩展容器。为完成本次请求，新增 `minidb/contracts/extensions.py` 中的 frozen dataclass `ExtensionStatement(feature,version,payload,span)`。构造时验证 JSON 基本值并深复制 payload，拒绝 AST 对象、tuple、非有限小数、非字符串键及循环引用。payload 的 dict/list 在复制后按只读约定使用；frozen 限制字段重新赋值，不声称它会禁止调用方主动修改所有嵌套容器。

原有 7 个核心契约文件逐字节保持不变，原哈希清单存于 `contracts.core-v1.sha256`，由回归测试核对。最新 `contracts.sha256` 新增一个文件条目；其整体哈希因新增文件发生变化。这是补齐缺失前置，不是把已有冻结 AST 或 Token 重新定义。四份项目整合前需统一采用新清单；不能忽略版本差异直接混合。

为使扩展能够被观察和验证，本次还更新了前端 formatter、演示工具及交付生成器。没有实现或修改其他成员模块；Git 提交与推送只在用户明确要求后执行。

## 验收和后续配合

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/frontend/test_a_e*.py
.\.venv\Scripts\python.exe tools/validate_frontend.py
```

完整校验会保留基础测试，验证前端和契约、运行核心及扩展演示，并测试不开扩展开关时的失败退出码。真实结果、每项测试名、源码哈希记录在 delivery 中，个人理解验收仍待本人完成。

UPDATE 执行需要 B-E04、C-E07、D-E02；排序/LIMIT 需要 B-E05、D-E03；JOIN 需要 B-E06、D-E05；聚合需要 B-E07、D-E04；类型与算术还需要 B/C/D 对应扩展。A-E08 至 A-E10可作为独立前端工具交付。所有语法功能仅标记“前端功能通过”。
