"""
micro_servers 黑盒测试 — 统一运行入口。

用法:
  python3 test_runner.py                     # 运行全部测试
  python3 test_runner.py --url http://127.0.0.1:8443  # 测试指定服务
  python3 test_runner.py --list              # 列出可用测试
  python3 test_runner.py --service gateway   # 按服务名运行
"""

import argparse
import importlib
import os
import sys

# 确保当前目录在 path 中
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import SERVICES, PORT_MAP, detect_service, get_url
from helpers import check_service

TEST_MODULES = {
    "gateway": "test_gateway",
    "captcha": "test_captcha",
    "smtp": "test_smtp",
    "dnfs": "test_dnfs",
    "db": "test_db",
    "ai_review": "test_ai_review",
    "static": "test_static",
    "waf": "test_waf",
}


def run_service_test(name: str, url: str) -> tuple:
    """运行单个服务的测试，返回 (passed, failed, skipped)。"""
    module_name = TEST_MODULES.get(name)
    if not module_name:
        print(f"未知服务: {name}")
        return 0, 0, 0

    try:
        mod = importlib.import_module(module_name)
    except ImportError as e:
        print(f"无法加载测试模块 {module_name}: {e}")
        return 0, 0, 0

    if not hasattr(mod, "run"):
        print(f"模块 {module_name} 缺少 run(url) 函数")
        return 0, 0, 0

    svc = SERVICES[name]
    print(f"\n{'='*60}")
    print(f"  {svc['description']}  ({url})")
    print(f"{'='*60}")

    reachable, detail = check_service(url)
    if not reachable:
        print(f"  [WARN] 服务不可达: {url}")
        if detail:
            print(f"         原因: {detail}")
        print(f"  所有测试将被跳过。请先启动服务。\n")
        return 0, 0, 0

    passed, failed, skipped = mod.run(url)
    return passed, failed, skipped


def run_all():
    """运行全部服务的测试。"""
    total_passed = 0
    total_failed = 0
    total_skipped = 0

    for name in SERVICES:
        url = get_url(name)
        p, f, s = run_service_test(name, url)
        total_passed += p
        total_failed += f
        total_skipped += s

    total = total_passed + total_failed + total_skipped
    print(f"\n{'='*60}")
    print(f"  全部测试完成: {total} total, "
          f"{total_passed} passed, {total_failed} failed, {total_skipped} skipped")
    print(f"{'='*60}")

    return total_failed


def run_by_url(url: str):
    """以 URL 为入口，测试全部可代理服务，内部服务自动回退 localhost。

    可代理服务 (通过 WAF→gateway 代理链): waf, gateway, static, dnfs
    内部服务 (仅 localhost 直连):           db, ai_review, captcha, smtp
    """
    from urllib.parse import urlparse

    hostname = urlparse(url).hostname or ""
    is_remote = hostname not in ("127.0.0.1", "localhost", "::1")

    if is_remote:
        print(f"  入口: {url}")
        print(f"  可代理服务 (waf/gateway/static/dnfs) 通过入口测试")
        print(f"  内部服务 (db/ai_review/captcha/smtp) 回退到 localhost")
        print()

    # 可通过入口 URL 访问的服务
    proxyable = {"waf", "gateway", "static", "dnfs"}
    total_passed = 0
    total_failed = 0
    total_skipped = 0

    for name in SERVICES:
        if name in proxyable:
            service_url = url
        else:
            service_url = get_url(name)

        p, f, s = run_service_test(name, service_url)
        total_passed += p
        total_failed += f
        total_skipped += s

    total = total_passed + total_failed + total_skipped
    print(f"\n{'='*60}")
    print(f"  全部测试完成: {total} total, "
          f"{total_passed} passed, {total_failed} failed, {total_skipped} skipped")
    print(f"{'='*60}")

    return total_failed


def run_by_name(name: str):
    """按服务名运行测试。"""
    svc = SERVICES.get(name)
    if not svc:
        print(f"未知服务: {name}")
        print(f"可用服务: {', '.join(SERVICES.keys())}")
        return 1

    url = get_url(name)
    p, f, s = run_service_test(name, url)
    return f


def list_tests():
    """列出所有可用测试。"""
    print("可用的黑盒测试:\n")
    for name, svc in SERVICES.items():
        print(f"  {name:12s}  端口 {svc['port']:<5d}  {svc['description']}")
    print()


def main():
    parser = argparse.ArgumentParser(
        description="micro_servers 黑盒用户模拟测试",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  %(prog)s                                          # 全部服务 localhost 测试
  %(prog)s --url https://qsont.me                  # 公网入口测试全部可代理服务
  %(prog)s --url http://127.0.0.1:8443             # 本地入口测试全部
  %(prog)s --service gateway                        # 仅测试指定服务
  %(prog)s --list                                   # 列出可用测试
        """,
    )
    parser.add_argument("--url", help="入口 URL，通过它测试全部可代理服务（内部服务回退 localhost）")
    parser.add_argument("--service", help="按服务名称运行测试")
    parser.add_argument("--list", action="store_true", help="列出所有可用测试")

    args = parser.parse_args()

    if args.list:
        list_tests()
        return 0

    if args.url:
        return run_by_url(args.url)

    if args.service:
        return run_by_name(args.service)

    # 默认：运行全部
    return run_all()


if __name__ == "__main__":
    sys.exit(main())
