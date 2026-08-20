#!/usr/bin/env python3
"""ssh-exec - 通过SSH远程执行单条命令的工具（支持密码认证）"""

import argparse
import os
import sys
import paramiko


def main():
    parser = argparse.ArgumentParser(
        description="ssh-exec - 通过SSH远程执行单条命令（支持密码认证）",
        epilog="""
参数说明:
  -H, --host       SSH主机地址（默认: 127.0.0.1）
  -P, --port       SSH端口号（默认: 22）
  -u, --user        SSH用户名（必填）
  -c, --command    要执行的远程命令（必填）

密码认证:
  密码经环境变量 AIBOX_SSH_PASS 提供（必填），不走命令行参数，
  避免凭据进入进程列表与会话日志（J12 条例）。

使用示例:
  AIBOX_SSH_PASS=12345678 %(prog)s -u qing -c "ls -la"
  AIBOX_SSH_PASS=mypass %(prog)s -u root -H 192.168.1.100 -P 22 -c "df -h"
  AIBOX_SSH_PASS=12345678 %(prog)s -u qing -H 127.0.0.1 -c "uname -a"
""",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("-H", "--host", default="127.0.0.1", help="SSH主机地址（默认: 127.0.0.1）")
    parser.add_argument("-P", "--port", type=int, default=22, help="SSH端口号（默认: 22）")
    parser.add_argument("-u", "--user", required=True, help="SSH用户名（必填）")
    parser.add_argument("-c", "--command", required=True, help="要执行的远程命令（必填）")

    args = parser.parse_args()

    # 密码仅从环境变量读取（S4：凭据不进命令行）
    password = os.environ.get("AIBOX_SSH_PASS")
    if not password:
        print("错误: 请通过环境变量 AIBOX_SSH_PASS 提供SSH密码", file=sys.stderr)
        sys.exit(2)

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    try:
        client.connect(
            hostname=args.host,
            port=args.port,
            username=args.user,
            password=password,
            timeout=10,
        )

        stdin, stdout, stderr = client.exec_command(args.command, timeout=30)

        exit_code = stdout.channel.recv_exit_status()
        out_text = stdout.read().decode("utf-8", errors="replace")
        err_text = stderr.read().decode("utf-8", errors="replace")

        if out_text:
            print(out_text, end="")
        if err_text:
            print(err_text, end="", file=sys.stderr)

        sys.exit(exit_code)

    except paramiko.AuthenticationException:
        print(f"错误: SSH认证失败（{args.user}@{args.host}:{args.port}）", file=sys.stderr)
        sys.exit(1)
    except paramiko.SSHException as e:
        print(f"错误: SSH连接失败 - {e}", file=sys.stderr)
        sys.exit(1)
    except TimeoutError:
        print(f"错误: 连接超时（{args.host}:{args.port}）", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"错误: {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        client.close()


if __name__ == "__main__":
    main()
