#!/usr/bin/env python3
"""core 内嵌 confs 黑盒测试 — 与 Python confs 行为对比

前置条件：
  - Python confs 运行于 :8010（基线）
  - core (nidus.out) 以 --confs 8012 启动

用法: python3 test_core_confs.py [core_port] [py_port]
"""
import os
import sys

sys.path.insert(0, '/opt/micro_servers/pymsg')
from pymsg.rpc import RpcClient, RpcError

CORE_PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8012
PY_PORT = int(sys.argv[2]) if len(sys.argv) > 2 else 8010
CONFIG_DIR = '/opt/micro_servers/confs/configs'


def call(port, route, payload=None):
    return RpcClient('127.0.0.1', port, 'test').call(route, payload)


def call_err(port, route, payload=None):
    """返回错误信息字符串；若未报错则返回 None"""
    try:
        call(port, route, payload)
        return None
    except RpcError as e:
        return str(e)


passed = failed = 0


def check(label, cond):
    global passed, failed
    if cond:
        passed += 1
        print(f'  [PASS] {label}')
    else:
        failed += 1
        print(f'  [FAIL] {label}')


def main():
    print('========== 1. health ==========')
    h = call(CORE_PORT, 'health')
    print('  core:', h)
    check('status=ok', h['status'] == 'ok')
    check('service=confs', h['service'] == 'confs')
    check('config_dir 指向 confs/configs', h['config_dir'].endswith('confs/configs'))

    print('========== 2. list ==========')
    l2, l0 = call(CORE_PORT, 'list'), call(PY_PORT, 'list')
    n2 = sorted(c['name'] for c in l2['configs'])
    n0 = sorted(c['name'] for c in l0['configs'])
    print('  core:', n2)
    check('文件清单一致', n2 == n0)
    check('size 一致',
          {c['name']: c['size'] for c in l2['configs']}
          == {c['name']: c['size'] for c in l0['configs']})

    print('========== 3. get 全文 (db.ini) ==========')
    g2 = call(CORE_PORT, 'get', {'name': 'db.ini'})
    g0 = call(PY_PORT, 'get', {'name': 'db.ini'})
    check('content 一致', g2['content'] == g0['content'])
    check('size 一致', g2['size'] == g0['size'])

    print('========== 4. get INI 键 ==========')
    import configparser
    cp = configparser.ConfigParser(interpolation=None)
    cp.read(os.path.join(CONFIG_DIR, 'db.ini'))
    sec = cp.sections()[0]
    opt = list(cp[sec].keys())[0]
    key = f'{sec}.{opt}'
    k2 = call(CORE_PORT, 'get', {'name': 'db.ini', 'key': key})
    k0 = call(PY_PORT, 'get', {'name': 'db.ini', 'key': key})
    print(f'  key={key}  core={k2["value"]!r}  py={k0["value"]!r}')
    check('INI 键值一致', k2['value'] == k0['value'])

    print('========== 5. 错误用例（两边都应报错且信息一致） ==========')
    for label, payload in [('不存在的配置', {'name': 'nope.ini'}),
                           ('路径穿越', {'name': '../etc/x.ini'}),
                           ('非法类型', {'name': 'x.exe'}),
                           ('缺 name', None),
                           ('键不存在', {'name': 'db.ini', 'key': 'no.such'})]:
        e2 = call_err(CORE_PORT, 'get', payload)
        e0 = call_err(PY_PORT, 'get', payload)
        print(f'  {label}: core={e2!r}  py={e0!r}')
        check(f'{label} 错误一致', e2 is not None and e2 == e0)

    print('========== 6. reload ==========')
    r2 = call(CORE_PORT, 'reload', {'name': 'db.ini'})
    r0 = call(PY_PORT, 'reload', {'name': 'db.ini'})
    check('reload 单个', r2 == r0 == {'reloaded': ['db.ini']})
    r2, r0 = call(CORE_PORT, 'reload'), call(PY_PORT, 'reload')
    check('reload 全部返回集合一致', sorted(r2['reloaded']) == sorted(r0['reloaded']))

    print('========== 7. INI option 大小写不敏感 ==========')
    ku2 = call(CORE_PORT, 'get', {'name': 'db.ini', 'key': f'{sec}.{opt.upper()}'})
    check('大写 option 取值一致', ku2['value'] == k0['value'])

    print('========== 8. JSON 配置（若存在） ==========')
    jsons = [f for f in os.listdir(CONFIG_DIR) if f.endswith('.json')]
    if jsons:
        j2 = call(CORE_PORT, 'get', {'name': jsons[0]})
        j0 = call(PY_PORT, 'get', {'name': jsons[0]})
        check(f'{jsons[0]} content 一致', j2['content'] == j0['content'])
    else:
        print('  (无 .json 配置，跳过)')

    print()
    print(f'结果: {passed} 通过, {failed} 失败')
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
