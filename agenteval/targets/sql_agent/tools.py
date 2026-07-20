"""sql_agent 的四个工具（PLAN 第 5/6 节）。

run_sql 一律走 sandbox 只读连接 + authorizer：
- 写/DDL 企图被拦截时，ToolCallRecord.blocked=True 记入 Trace（供 rules 判 E8）；
- 普通 SQL 错误（语法错误/不存在表）以 error 记录，供 sql_result 判 E1、rules 判 E6。
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

from agenteval.core.trace import ToolCallRecord
from agenteval.sandbox.db import connect_readonly
from agenteval.targets.mock import render_rows

# OpenAI tools 协议 schema（同时供真实 LLM 与 MockLLM 对齐名称）
TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "list_tables",
            "description": "列出数据库中的全部表名",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "describe_table",
            "description": "查看指定表的列名与类型",
            "parameters": {
                "type": "object",
                "properties": {"table": {"type": "string", "description": "表名"}},
                "required": ["table"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_sql",
            "description": "执行一条只读 SELECT 查询并返回结果（写操作会被沙箱拦截）",
            "parameters": {
                "type": "object",
                "properties": {"sql": {"type": "string", "description": "只读 SELECT SQL"}},
                "required": ["sql"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "make_chart",
            "description": "根据最近一次查询结果生成图表（演示环境只记录调用，不落盘）",
            "parameters": {
                "type": "object",
                "properties": {
                    "kind": {"type": "string", "description": "图表类型，如 bar/line"},
                    "title": {"type": "string", "description": "图表标题"},
                },
                "required": ["kind"],
            },
        },
    },
]

_ROW_LIMIT = 200


class ToolExecutor:
    """在指定沙箱库副本上执行工具调用。"""

    def __init__(self, db_path: str | Path, row_limit: int = _ROW_LIMIT):
        self.db_path = str(db_path)
        self.row_limit = row_limit

    def execute(self, name: str, arguments: dict) -> ToolCallRecord:
        start = time.perf_counter()
        record = ToolCallRecord(name=name, arguments=arguments or {})
        try:
            if name == "list_tables":
                record.result = self._list_tables()
            elif name == "describe_table":
                record.result = self._describe_table(str(arguments.get("table", "")))
            elif name == "run_sql":
                record.result = self._run_sql(str(arguments.get("sql", "")), record)
            elif name == "make_chart":
                record.result = "图表已生成（演示环境不落盘）"
            else:
                record.error = f"未知工具: {name}"
        except sqlite3.Error as e:
            record.error = str(e)
            record.blocked = "not authorized" in record.error.lower()
        except Exception as e:  # noqa: BLE001 - 工具层兜底，错误全部进 Trace
            record.error = f"{type(e).__name__}: {e}"
        record.duration_ms = (time.perf_counter() - start) * 1000
        return record

    # ------------------------------------------------------------------

    def _list_tables(self) -> str:
        conn = connect_readonly(self.db_path)
        try:
            rows = conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
            ).fetchall()
            return "\n".join(r[0] for r in rows)
        finally:
            conn.close()

    def _describe_table(self, table: str) -> str:
        if not table:
            raise ValueError("describe_table 缺少 table 参数")
        conn = connect_readonly(self.db_path)
        try:
            rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
            if not rows:
                raise sqlite3.Error(f"no such table: {table}")
            return "\n".join(f"{r[1]} {r[2]}" for r in rows)
        finally:
            conn.close()

    def _run_sql(self, sql: str, record: ToolCallRecord) -> str:
        conn = connect_readonly(self.db_path)
        try:
            cur = conn.execute(sql)
            rows = cur.fetchmany(self.row_limit + 1)
            cols = [d[0] for d in cur.description] if cur.description else []
            truncated = len(rows) > self.row_limit
            text = render_rows(cols, rows[: self.row_limit], limit=self.row_limit)
            if truncated:
                text += f"\n（结果超过 {self.row_limit} 行，已截断）"
            return text
        finally:
            conn.close()
