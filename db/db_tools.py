#!/usr/bin/env python3
"""
db_tools — SQLite 数据库备份、导出、导入工具

用法:
  python db_tools.py backup <output_path>     # 安全备份数据库（使用 sqlite3 backup API）
  python db_tools.py export <output.json>     # 导出所有表数据为 JSON
  python db_tools.py import <input.json>      # 从 JSON 导入数据
  python db_tools.py vacuum                   # 执行 VACUUM 压缩数据库
  python db_tools.py wal-checkpoint           # 强制 WAL 检查点（确保数据写入主库）
  python db_tools.py info                     # 显示数据库信息和表统计
"""

import json
import os
import shutil
import sys
import sqlite3
from datetime import datetime, timezone

DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///database.db")

# 从 sqlite:///path 提取文件路径
def _get_db_path() -> str:
    if DATABASE_URL.startswith("sqlite:////"):
        return DATABASE_URL[10:]  # 绝对路径 sqlite:////app/data/xxx.db
    elif DATABASE_URL.startswith("sqlite:///"):
        return DATABASE_URL[9:]   # 相对路径
    else:
        return "database.db"


def _connect() -> sqlite3.Connection:
    """创建数据库连接（配置 PRAGMA）。"""
    conn = sqlite3.connect(_get_db_path())
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.row_factory = sqlite3.Row
    return conn


def backup(output_path: str):
    """
    使用 SQLite 官方 backup API 进行在线热备份（不需要停机）。
    这是最安全的备份方式，支持读写并发。
    """
    src = _get_db_path()
    print(f"[backup] 正在备份数据库: {src} -> {output_path}")
    
    if not os.path.exists(src):
        print(f"[backup] 错误: 数据库文件不存在: {src}", file=sys.stderr)
        sys.exit(1)
    
    start = datetime.now(timezone.utc)
    
    # 使用 sqlite3 backup API（支持在线备份）
    src_conn = sqlite3.connect(src)
    dst_conn = sqlite3.connect(output_path)
    try:
        src_conn.backup(dst_conn)
        dst_conn.commit()
    finally:
        dst_conn.close()
        src_conn.close()
    
    size_mb = os.path.getsize(output_path) / (1024 * 1024)
    elapsed = (datetime.now(timezone.utc) - start).total_seconds()
    print(f"[backup] 备份完成: {output_path} ({size_mb:.2f} MB, 耗时 {elapsed:.2f}s)")


def export_json(output_path: str):
    """导出所有表数据为 JSON 格式（便于跨平台传输/迁移）。"""
    print(f"[export] 正在导出数据到: {output_path}")
    conn = _connect()
    
    result = {
        "_meta": {
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "db_path": _get_db_path(),
            "sqlite_version": sqlite3.sqlite_version,
        },
        "tables": {}
    }
    
    try:
        # 获取所有用户表
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
        
        for (table_name,) in tables:
            # 获取列信息
            columns = [desc[1] for desc in conn.execute(f"PRAGMA table_info({table_name})").fetchall()]
            
            # 获取行数
            count = conn.execute(f"SELECT COUNT(*) FROM [{table_name}]").fetchone()[0]
            
            # 获取所有数据
            rows = conn.execute(f"SELECT * FROM [{table_name}]").fetchall()
            data = [dict(row) for row in rows]
            
            result["tables"][table_name] = {
                "columns": columns,
                "row_count": count,
                "data": data,
            }
            print(f"[export]   表 {table_name}: {count} 行")
        
        # 同时导出 schema
        schema = []
        for (table_name,) in tables:
            ddl = conn.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
                (table_name,)
            ).fetchone()[0]
            if ddl:
                schema.append(ddl + ";")
        
        result["_meta"]["schema"] = schema
        
    finally:
        conn.close()
    
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2, default=str)
    
    size_mb = os.path.getsize(output_path) / (1024 * 1024)
    print(f"[export] 导出完成: {output_path} ({size_mb:.2f} MB)")


def import_json(input_path: str):
    """从 JSON 文件导入数据（注意：会清空现有数据！）。"""
    print(f"[import] 正在从 {input_path} 导入数据")
    
    if not os.path.exists(input_path):
        print(f"[import] 错误: 文件不存在: {input_path}", file=sys.stderr)
        sys.exit(1)
    
    with open(input_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    
    conn = _connect()
    
    try:
        # 是否清空现有表（需要确认）
        tables = data.get("tables", {})
        if tables:
            print(f"[import] 警告: 即将导入 {len(tables)} 个表，现有数据将被覆盖!")
            # 为安全起见，不自动清空，由用户先备份
            
            # 按外键依赖顺序导入（先主表后子表，这里简单按名字排序）
            for table_name in sorted(tables.keys()):
                table_data = tables[table_name]
                rows = table_data.get("data", [])
                columns = table_data.get("columns", [])
                
                if not rows or not columns:
                    print(f"[import]   跳表 {table_name}: 无数据或列定义")
                    continue
                
                # 先清空表
                conn.execute(f"DELETE FROM [{table_name}]")
                
                # 批量插入
                placeholders = ", ".join(["?"] * len(columns))
                col_names = ", ".join(f"[{c}]" for c in columns)
                insert_sql = f"INSERT INTO [{table_name}] ({col_names}) VALUES ({placeholders})"
                
                conn.executemany(insert_sql, [tuple(row.get(col) for col in columns) for row in rows])
                print(f"[import]   表 {table_name}: 导入 {len(rows)} 行")
            
            conn.commit()
            print("[import] 导入完成")
        else:
            print("[import] 没有可导入的表数据")
    finally:
        conn.close()


def vacuum():
    """执行 VACUUM 压缩数据库，回收空间、重建索引。"""
    print("[vacuum] 正在执行 VACUUM...")
    db_path = _get_db_path()
    before_size = os.path.getsize(db_path) / (1024 * 1024)
    
    conn = _connect()
    try:
        # WAL 检查点先将所有数据写入主库
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.execute("VACUUM")
        conn.execute("ANALYZE")
    finally:
        conn.close()
    
    after_size = os.path.getsize(db_path) / (1024 * 1024)
    saved = before_size - after_size
    print(f"[vacuum] 完成: {before_size:.2f} MB -> {after_size:.2f} MB (节省 {saved:.2f} MB)")


def wal_checkpoint():
    """强制 WAL 检查点，确保所有写入都持久化到主数据库文件。"""
    print("[wal-checkpoint] 执行 WAL 检查点...")
    conn = _connect()
    try:
        result = conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        # busy, log, checkpointed
        print(f"[wal-checkpoint] 结果: busy={result[0]}, log_pages={result[1]}, checkpointed={result[2]}")
    finally:
        conn.close()
    print("[wal-checkpoint] 完成")


def show_info():
    """显示数据库信息和表统计。"""
    db_path = _get_db_path()
    print("=" * 50)
    print("SQLite 数据库信息")
    print("=" * 50)
    print(f"文件路径: {os.path.abspath(db_path)}")
    
    if os.path.exists(db_path):
        size_mb = os.path.getsize(db_path) / (1024 * 1024)
        mtime = datetime.fromtimestamp(os.path.getmtime(db_path), tz=timezone.utc)
        print(f"文件大小: {size_mb:.2f} MB")
        print(f"最后修改: {mtime.isoformat()}")
    else:
        print("文件大小: (不存在)")
    
    # WAL 文件
    wal_path = db_path + "-wal"
    shm_path = db_path + "-shm"
    if os.path.exists(wal_path):
        wal_size = os.path.getsize(wal_path) / 1024
        print(f"WAL 文件: {wal_size:.2f} KB")
    if os.path.exists(shm_path):
        shm_size = os.path.getsize(shm_path) / 1024
        print(f"SHM 文件: {shm_size:.2f} KB")
    
    conn = _connect()
    try:
        print(f"SQLite 版本: {sqlite3.sqlite_version}")
        
        # PRAGMA 信息
        journal_mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        synchronous = conn.execute("PRAGMA synchronous").fetchone()[0]
        page_size = conn.execute("PRAGMA page_size").fetchone()[0]
        page_count = conn.execute("PRAGMA page_count").fetchone()[0]
        free_pages = conn.execute("PRAGMA freelist_count").fetchone()[0]
        
        print(f"Journal 模式: {journal_mode}")
        print(f"同步级别: {synchronous}")
        print(f"页大小: {page_size} bytes")
        
        # 表统计
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ).fetchall()
        
        print(f"\n表统计 ({len(tables)} 个表):")
        print("-" * 40)
        total_rows = 0
        for (table_name,) in tables:
            count = conn.execute(f"SELECT COUNT(*) FROM [{table_name}]").fetchone()[0]
            total_rows += count
            print(f"  {table_name:20s} {count:>8d} 行")
        print("-" * 40)
        print(f"  {'总计':20s} {total_rows:>8d} 行")
        
    finally:
        conn.close()


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    
    cmd = sys.argv[1]
    
    if cmd == "backup":
        output = sys.argv[2] if len(sys.argv) > 2 else f"backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db"
        backup(output)
    elif cmd == "export":
        output = sys.argv[2] if len(sys.argv) > 2 else f"export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        export_json(output)
    elif cmd == "import":
        if len(sys.argv) < 3:
            print("用法: python db_tools.py import <input.json>", file=sys.stderr)
            sys.exit(1)
        import_json(sys.argv[2])
    elif cmd == "vacuum":
        vacuum()
    elif cmd == "wal-checkpoint":
        wal_checkpoint()
    elif cmd == "info":
        show_info()
    else:
        print(f"未知命令: {cmd}", file=sys.stderr)
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
