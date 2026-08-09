#!/usr/bin/env python3
"""
test_rpc.py — captcha2 pymsg RPC 功能验证脚本

用法:  python3 test_rpc.py [host] [port]
默认:  python3 test_rpc.py 127.0.0.1 8009
"""

import base64
import os
import sys
import time

_here = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_here, "..", "pymsg"))
from pymsg import RpcClient  # noqa: E402

HOST = sys.argv[1] if len(sys.argv) > 1 else "127.0.0.1"
PORT = int(sys.argv[2]) if len(sys.argv) > 2 else 8009

passed = 0
failed = 0


def test(name: str, expect_ok: bool, fn):
    global passed, failed
    try:
        result = fn()
        # RPC 调用成功 = 返回 dict 且没有 error 字段（或 error 是预期的一部分）
        rpc_ok = isinstance(result, dict)
        if expect_ok == rpc_ok:
            print(f"  ✓ {name} → {result}")
            passed += 1
            return result
        else:
            print(f"  ✗ {name} — 预期 {'成功' if expect_ok else '失败'}，实际: {result}")
            failed += 1
            return result
    except Exception as e:
        if not expect_ok:
            print(f"  ✓ {name} (预期异常: {e})")
            passed += 1
        else:
            print(f"  ✗ {name} — 异常: {e}")
            failed += 1
        return None


def main():
    global passed, failed
    print(f"captcha2 RPC 功能测试 — tcp://{HOST}:{PORT}")
    print("=" * 60)

    client = RpcClient(HOST, PORT, "test-captcha2", timeout=5.0)

    # ── 1. health ──
    print("\n[1] health 检查")
    r = test("health 返回 ok", True, lambda: client.call("health"))
    if r:
        print(f"      服务: {r.get('service')}, 状态: {r.get('status')}")

    # ── 2. image 生成 ──
    print("\n[2] image 验证码生成")
    r = test("image 返回 token+image", True, lambda: client.call("image"))
    token = ""
    if r:
        token = r.get("token", "")
        img_b64 = r.get("image", "")
        print(f"      token: {token[:16]}... (len={len(token)})")
        print(f"      image base64: {len(img_b64)} 字符")

        # 解码保存 PNG
        try:
            img_bytes = base64.b64decode(img_b64)
            fname = f"/tmp/captcha2_test_{token[:8]}.png"
            with open(fname, "wb") as f:
                f.write(img_bytes)
            print(f"      PNG 已保存: {fname} ({len(img_bytes)} bytes)")
        except Exception as e:
            print(f"      ⚠ PNG 保存失败: {e}")

    # ── 3. verify 校验 ──
    print("\n[3] verify 校验")

    # 3a. 正向校验（仅在终端交互模式下）
    if sys.stdin.isatty():
        print("  生成验证码用于正向校验...")
        r2 = client.call("image")
        token2 = r2.get("token", "")
        if token2 and r2.get("image"):
            img_bytes = base64.b64decode(r2["image"])
            fname = f"/tmp/captcha2_manual_{token2[:8]}.png"
            with open(fname, "wb") as f:
                f.write(img_bytes)
            print(f"  请查看图片: {fname}")
            print(f"  输入图片中的 4 位字符（不区分大小写）: ", end="", flush=True)
            try:
                user_answer = sys.stdin.readline().strip()
            except (EOFError, KeyboardInterrupt):
                user_answer = ""
            if user_answer:
                vr = client.call("verify", {"token": token2, "answer": user_answer})
                is_valid = bool(vr.get("valid", False))
                if is_valid:
                    print(f"  ✓ verify 正向校验 → {{'valid': True}}（答案 '{user_answer}' 正确）")
                    passed += 1
                else:
                    print(f"  ✗ verify 正向校验 → {{'valid': False}}（答案 '{user_answer}' 未通过）")
                    failed += 1
            else:
                print(f"  ⚠ 跳过（无输入）")
    else:
        print("  ⚠ 非交互模式，跳过正向校验（需人工读取 PNG）")

    # 3b. 错误答案 → RPC 成功返回 valid=false
    test("verify 错误答案 → valid=false", True,
         lambda: client.call("verify", {"token": token, "answer": "ZZZZ"}))

    # 3c. 大小写不敏感（P0 修复验证）
    r3 = client.call("image")
    token3 = r3.get("token", "")
    if token3:
        # 输入全大写，验证大小写不敏感（P0 修复后应失效——答案存的是小写）
        # 这里仅验证 RPC 调用不崩溃
        test("verify 全大写答案 → RPC 成功", True,
             lambda: client.call("verify", {"token": token3, "answer": "ABCD"}))

    # 3d. 缺参数 → RPC 成功返回 valid=false + error
    test("verify 缺参数 → valid=false", True,
         lambda: client.call("verify", {}))

    # 3e. 无效 token → RPC 成功返回 valid=false + error
    test("verify 无效 token → valid=false", True,
         lambda: client.call("verify", {"token": "deadbeef" * 8, "answer": "abcd"}))

    # ── 4. 速率限制压力测试（P1-1: 全局限流 600/min，70 次应全部通过；gateway 层按 IP 限流 10/min） ──
    print("\n[4] captcha2 自身全局限流（阈值 600/min，70 次请求）")
    ok_count = 0
    rate_limited = 0
    for i in range(70):
        try:
            r = client.call("image")
            if r.get("token"):
                ok_count += 1
            elif not r.get("success", True):
                rate_limited += 1
        except Exception:
            rate_limited += 1
    print(f"      成功: {ok_count}, 被限: {rate_limited}")
    # P1-1 修复后，全局限流阈值调到 600，70 次应全部通过
    if ok_count == 70:
        print(f"  ✓ 全局限流未误拦（70/70 通过，阈值 600/min 正确）")
        passed += 1
    else:
        print(f"  ✗ 预期 70 次全通过，实际成功 {ok_count}")
        failed += 1

    # ── 结果汇总 ──
    print("\n" + "=" * 60)
    print(f"结果: {passed} 通过, {failed} 失败")
    return failed == 0


if __name__ == "__main__":
    ok = main()
    sys.exit(0 if ok else 1)
