#!/usr/bin/env python3
"""查看 database.db 各表数据 (非交互式)"""

import argparse
import sqlite3
import os
import sys

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "database.db")


def get_tables(cur):
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")
    return [r[0] for r in cur.fetchall()]


def print_rows(cols, rows):
    if not rows:
        print("(空)")
        return
    widths = [len(c) for c in cols]
    str_rows = []
    for row in rows:
        str_row = [str(v) if v is not None else "NULL" for v in row]
        str_rows.append(str_row)
        for i, v in enumerate(str_row):
            widths[i] = max(widths[i], min(len(v), 50))
    print(" | ".join(c.ljust(widths[i]) for i, c in enumerate(cols)))
    print("-+-".join("-" * w for w in widths))
    for str_row in str_rows:
        print(" | ".join(v[:50].ljust(widths[i]) for i, v in enumerate(str_row)))
    print(f"\n({len(rows)} 行)")


def main():
    parser = argparse.ArgumentParser(description="查看 database.db")
    parser.add_argument("table", nargs="?", help="要查看的表名 (不指定则列出所有表)")
    parser.add_argument("-n", "--limit", type=int, default=50, help="显示最后 N 行 (默认 50)")
    parser.add_argument("-w", "--where", help="WHERE 条件, 如: \"level='ERROR'\"")
    parser.add_argument("-s", "--sql", help="自由 SQL 查询 (忽略 table/where/limit)")
    args = parser.parse_args()

    if not os.path.exists(DB_PATH):
        print(f"数据库不存在: {DB_PATH}", file=sys.stderr)
        sys.exit(1)

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    tables = get_tables(cur)

    # 自由 SQL
    if args.sql:
        cur.execute(args.sql)
        cols = [d[0] for d in cur.description]
        print_rows(cols, cur.fetchall())
        conn.close()
        return

    # 列出所有表
    if not args.table:
        print(f"数据库: {DB_PATH}\n")
        for t in tables:
            cur.execute(f"SELECT COUNT(*) FROM [{t}]")
            print(f"  {t} ({cur.fetchone()[0]} 行)")
        conn.close()
        return

    # 查看指定表：取最后 N 行（按 rowid 倒序取，再正序展示）
    # 用子查询包装：内层倒序取末尾 N 行，外层按 rowid 正序还原展示顺序
    if args.table not in tables:
        print(f"表 '{args.table}' 不存在, 可用: {', '.join(tables)}", file=sys.stderr)
        sys.exit(1)

    inner = f"SELECT * FROM [{args.table}]"
    if args.where:
        inner += f" WHERE {args.where}"
    inner += f" ORDER BY rowid DESC LIMIT {args.limit}"
    sql = f"SELECT * FROM ({inner}) ORDER BY rowid ASC"

    cur.execute(sql)
    cols = [d[0] for d in cur.description]
    print_rows(cols, cur.fetchall())
    conn.close()


if __name__ == "__main__":
    main()
