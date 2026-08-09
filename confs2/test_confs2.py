#!/usr/bin/env python3
"""confs2 vs confs 对比测试 — 21 项路由逐一对比 Python 原版与 C++ 重写版。

智能比较策略：
- mtime 字段：截断到秒精度（Python float 与 C++ 纳秒转微秒有 ±1µs 精度差异）
- health 路由：比较结构（字段集 + status + service + config_dir），忽略 uptime/cached
- reload all：集合比较（dict 保序 vs unordered_map 哈希序，条目集一致即可）
"""
import sys, json, os, re
sys.path.insert(0, "/opt/micro_servers/confs/venv/lib/python3.12/site-packages")
sys.path.insert(0, "/opt/micro_servers/pymsg")
from pymsg import RpcClient

PORT_OLD = 8010  # Python confs
PORT_NEW = 8011  # C++ confs2

def call_both(route, params):
    c_old = RpcClient("127.0.0.1", PORT_OLD, "test", timeout=5.0)
    c_new = RpcClient("127.0.0.1", PORT_NEW, "test", timeout=5.0)
    try:
        r_old = c_old.call(route, params); old_ok = True
    except Exception as e:
        r_old = str(e); old_ok = False
    try:
        r_new = c_new.call(route, params); new_ok = True
    except Exception as e:
        r_new = str(e); new_ok = False
    return r_old, r_new, old_ok, new_ok

def call_both_raw(route, params):
    c_old = RpcClient("127.0.0.1", PORT_OLD, "test", timeout=5.0)
    c_new = RpcClient("127.0.0.1", PORT_NEW, "test", timeout=5.0)
    try:
        r_old = c_old.call_raw(route, params); old_ok = r_old.get("success", False)
    except Exception as e:
        r_old = {"success": False, "error": str(e)}; old_ok = False
    try:
        r_new = c_new.call_raw(route, params); new_ok = r_new.get("success", False)
    except Exception as e:
        r_new = {"success": False, "error": str(e)}; new_ok = False
    return r_old, r_new, old_ok, new_ok

def normalize_mtime(s):
    if isinstance(s, str):
        return re.sub(r'\.\d+(?=\+00:00|Z|$)', '', s)
    return s

def normalize_obj(obj):
    if isinstance(obj, dict):
        return {k: (normalize_mtime(v) if k == 'mtime' else normalize_obj(v)) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [normalize_obj(item) for item in obj]
    return obj

results = []

def report(label, match, old, new, ok_old, ok_new):
    status = "PASS" if match else "FAIL"
    print(f"[{status}] {label}")
    if not match:
        print(f"  OLD: ok={ok_old} -> {json.dumps(old, default=str)[:300]}")
        print(f"  NEW: ok={ok_new} -> {json.dumps(new, default=str)[:300]}")
    results.append(match)

# 1. health (ignore uptime/cached)
r_old, r_new, ok_old, ok_new = call_both("health", {})
if ok_old and ok_new:
    keys_match = set(r_old.keys()) == set(r_new.keys())
    vals_match = all(r_old.get(k) == r_new.get(k) for k in ["status", "service", "config_dir"])
    report("health", keys_match and vals_match, r_old, r_new, ok_old, ok_new)
else:
    report("health", ok_old == ok_new, r_old, r_new, ok_old, ok_new)

# 2. list (normalize mtime)
r_old, r_new, ok_old, ok_new = call_both("list", {})
if ok_old and ok_new:
    report("list", normalize_obj(r_old) == normalize_obj(r_new), r_old, r_new, ok_old, ok_new)
else:
    report("list", ok_old == ok_new, r_old, r_new, ok_old, ok_new)

# 3. get full content - db.ini (normalize mtime)
r_old, r_new, ok_old, ok_new = call_both("get", {"name": "db.ini"})
if ok_old and ok_new:
    report("get db.ini full", normalize_obj(r_old) == normalize_obj(r_new), r_old, r_new, ok_old, ok_new)
else:
    report("get db.ini full", ok_old == ok_new, r_old, r_new, ok_old, ok_new)

# 4-6. get INI keys
for key, label in [("server.port", "server.port"), ("database.url", "database.url"), ("logs.retention_days", "logs.retention_days")]:
    r_old, r_new, ok_old, ok_new = call_both("get", {"name": "db.ini", "key": key})
    report(f"get db.ini {label}", ok_old and ok_new and r_old == r_new, r_old, r_new, ok_old, ok_new)

# 7. case-insensitive option
r_old, r_new, ok_old, ok_new = call_both("get", {"name": "db.ini", "key": "server.PORT"})
report("get db.ini server.PORT (case-insensitive)", ok_old and ok_new and r_old == r_new, r_old, r_new, ok_old, ok_new)

# 8-9. static.ini keys
for key, label in [("server.port", "server.port"), ("cors.allowed_origins", "cors.allowed_origins")]:
    r_old, r_new, ok_old, ok_new = call_both("get", {"name": "static.ini", "key": key})
    report(f"get static.ini {label}", ok_old and ok_new and r_old == r_new, r_old, r_new, ok_old, ok_new)

# 10. get full content - captcha.ini (normalize mtime)
r_old, r_new, ok_old, ok_new = call_both("get", {"name": "captcha.ini"})
if ok_old and ok_new:
    report("get captcha.ini full", normalize_obj(r_old) == normalize_obj(r_new), r_old, r_new, ok_old, ok_new)
else:
    report("get captcha.ini full", ok_old == ok_new, r_old, r_new, ok_old, ok_new)

# 11-13. error cases
for params, label in [
    ({"name": "nonexistent.ini"}, "get nonexistent (expect error)"),
    ({"name": "../../etc/passwd"}, "get path traversal (expect error)"),
    ({"name": "test.sh"}, "get invalid extension (expect error)"),
]:
    r_old, r_new, ok_old, ok_new = call_both_raw("get", params)
    report(label, ok_old == ok_new, r_old, r_new, ok_old, ok_new)

# 14. reload specific
r_old, r_new, ok_old, ok_new = call_both("reload", {"name": "db.ini"})
report("reload db.ini", ok_old and ok_new and r_old == r_new, r_old, r_new, ok_old, ok_new)

# 15. reload all (set comparison — dict vs unordered_map)
r_old, r_new, ok_old, ok_new = call_both("reload", {})
if ok_old and ok_new:
    old_set = set(r_old.get("reloaded", []))
    new_set = set(r_new.get("reloaded", []))
    report("reload all", old_set == new_set, r_old, r_new, ok_old, ok_new)
else:
    report("reload all", ok_old == ok_new, r_old, r_new, ok_old, ok_new)

# 16-21. error cases
for route, params, label in [
    ("reload", {"name": ""}, "reload empty string (expect error)"),
    ("reload", {"name": 123}, "reload non-string (expect error)"),
    ("get", {"name": ""}, "get empty name (expect error)"),
    ("get", {}, "get without name (expect error)"),
    ("get", {"name": "db.ini", "key": "nonexistent.key"}, "get db.ini nonexistent.key (expect error)"),
    ("get", {"name": "db.ini", "key": "noformat"}, "get db.ini noformat (expect error)"),
]:
    r_old, r_new, ok_old, ok_new = call_both_raw(route, params)
    report(label, ok_old == ok_new, r_old, r_new, ok_old, ok_new)

# Summary
passed = sum(1 for r in results if r)
total = len(results)
print(f"\n{'='*60}")
print(f"Results: {passed}/{total} passed")
if passed < total:
    print(f"FAILED: {total - passed} tests")
    sys.exit(1)
else:
    print("ALL TESTS PASSED!")
    sys.exit(0)
