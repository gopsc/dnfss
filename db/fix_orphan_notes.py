#!/usr/bin/env python3
"""
fix_orphan_notes — 修复 Notes 表中"父节点不存在"的记录。

Notes 是父子结构（pid 指向父节点 id，0 表示无父/根）。当父节点被删除后，
子节点的 pid 会悬空（指向一个不存在的 id）。本脚本把这些 pid 悬空的记录的
pid 改为 0，使其成为根节点。

通过 db 服务的 pymsg execute 路由执行（不直接连库），与业务访问方式一致。

用法:
  python fix_orphan_notes.py            # 连接 db 服务（默认 127.0.0.1:8005）
  DB_HOST=127.0.0.1 DB_PORT=8005 python fix_orphan_notes.py

说明:
  - 默认只打印将修复的记录，不实际修改；加 --apply 才真正执行修改。
  - --id 显式指定需要修复的节点 id（逗号分隔），否则自动检测所有 pid 悬空节点。
"""

import os
import sys

import argparse

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from pymsg import RpcClient  # noqa: E402

DB_HOST = os.environ.get('DB_HOST', '127.0.0.1')
DB_PORT = int(os.environ.get('DB_PORT', '8005'))

# SQLite 默认 SQLITE_MAX_VARIABLE_NUMBER=999，in 子句分批大小取一个安全值
_IN_BATCH = 500


def _rpc():
    return RpcClient(DB_HOST, DB_PORT, 'fix_orphan_notes', timeout=10)


def _select(client, columns, where=None):
    payload = {'action': 'select', 'table': 'Notes', 'columns': columns}
    if where:
        payload['where'] = where
    return client.call('execute', payload)


def _find_orphan_ids(client):
    """返回所有 pid 指向不存在节点的记录 id 列表。"""
    all_ids = {row['id'] for row in _select(client, ['id'])}
    refs = _select(client, ['id', 'pid'], {'pid': {'ne': 0}})
    orphan = [row['id'] for row in refs if row['pid'] not in all_ids]
    return orphan


def _fix(client, ids, apply):
    """把指定 id 的记录的 pid 改为 0。返回实际修改行数。"""
    total = 0
    for i in range(0, len(ids), _IN_BATCH):
        batch = ids[i:i + _IN_BATCH]
        if not apply:
            print(f"  [dry-run] 节点 {batch} 的 pid 将改为 0")
            continue
        resp = client.call('execute', {
            'action': 'update',
            'table': 'Notes',
            'values': {'pid': 0},
            'where': {'id': {'in': batch}},
        })
        total += resp[0]['rowcount'] if resp else 0
    return total


def main():
    parser = argparse.ArgumentParser(description='修复 Notes 表中 pid 悬空的记录')
    parser.add_argument('--apply', action='store_true',
                        help='真正执行修改（默认 dry-run 仅打印）')
    parser.add_argument('--id', help='指定需要修复的节点 id（逗号分隔），否则自动检测')
    args = parser.parse_args()

    client = _rpc()

    if args.id:
        target = [int(x) for x in args.id.split(',') if x.strip()]
    else:
        target = _find_orphan_ids(client)

    if not target:
        print("没有发现需要修复的节点（所有 pid 均指向存在的父节点）。")
        return

    print(f"待修复 {len(target)} 个节点: {target}")
    if not args.apply:
        print("dry-run 模式：未做任何修改。加 --apply 真正执行。")
        _fix(client, target, apply=False)
        return

    fixed = _fix(client, target, apply=True)
    print(f"完成：已修复 {fixed}/{len(target)} 个节点 pid -> 0。")


if __name__ == "__main__":
    main()
