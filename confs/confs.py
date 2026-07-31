#!/usr/bin/env python3
'''
使用pymsg库的尝试
'''

from pymsg import MsgServer
from email.mime.text import MIMEText
from email.header import Header
from log_client import logs
from dotenv import load_dotenv
import smtplib
import random
import time
import sys, os
import json

# 从文件加载到环境变量？
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))
SMTP_SERVER: str = os.getenv('SMTP_SERVER')
SMTP_PORT: int = int(os.getenv("SMTP_PORT"))
SMTP_USER: str = os.getenv('SMTP_USER')
SMTP_PASSWORD: str = os.getenv('__PASSWORD')


print("【SMTP】")
print("server: ", SMTP_SERVER)
print("port: ", SMTP_PORT)
print("user: ", SMTP_USER)
print("password: ", SMTP_PASSWORD)

captchas: dict[str, list[int, int]] = {}


class SmtpServer:
    '''SMTP服务器类'''

    def __init__(self, addr="127.0.0.1", port=1234, workers=4):
        self.addr = addr
        self.port = port
        self._server = MsgServer(addr, port, workers=workers)

        self._route = {
            "send_email": self._send_email,
            "verify_email": self._verify_email
        }

    def start(self):
        self._server.start(self._analysis)

    def stop(self):
        self._server.stop()

    def _analysis(self, sock, addr, data):
        print("""【recv】\n"""+str(addr)+'\n'+data) # 打印实际收到的消息


        # 解包json
        try:
            request = json.loads(data)
        except json.JSONDecodeError as err:
            self._reply(sock, False, error=f"解析JSON失败: {err}")
            return

        # 路由
        route = request.pop("_route", "")
        func = self._route.get(route)
        if func is None:
            self._reply(sock, False, error=f"解析路由失败：没有找到路由{route}")
            return

        # 执行分支
        try:
            result = func(request)
            self._reply(sock, True, data=result)
        except Exception as err:
            self._reply(sock, False, error=f"执行操作失败: {err}")

    def _reply(self, sock, success: bool, data: str = None, error: str = None):
        print("""【send】\n"""+str(data)+'\n'+str(error))   # 打印实际输出
        response = {"success": success}
        if data is not None:
            response["data"] = data
        if error is not None:
            response["error"] = error
        self._server.reply(sock, json.dumps(response, ensure_ascii=False))


    def _send_email(self, data):
        def build_captcha() -> str:
            captcha: str = ""
            for i in range(0,6):
                digit: int = random.randint(0,9)
                captcha += str(digit)
            return captcha

        recipient: str = data.get("email")
        subject: str = "DNFS 邮箱验证"
        captcha: str = build_captcha()
        content: str = f"DNFS: 您的邮箱验证码为 {captcha}，一分钟内有效。"

        captchas[recipient] = ["", ""]
        captchas[recipient][0] = captcha
        captchas[recipient][1] = str(int(time.time())+60)

        message = MIMEText(content, "plain", "UTF-8")
        message["From"] = SMTP_USER
        message["To"] = recipient
        message["Subject"] = subject

        # 这一行不用写到try里，因为如果它失败就没有这个类不用quit()
        # 反而因为写进了try，和finally不在一个作用域
        server = smtplib.SMTP_SSL(SMTP_SERVER, SMTP_PORT, timeout=10)
        try:
            server.login(SMTP_USER, SMTP_PASSWORD)
            server.sendmail(SMTP_USER, recipient, message.as_string())
            logs.info("smtp", f"{recipient}: 验证码发送成功({captcha})")
            return {"success": True, "data": "发送成功"}

        except Exception as err:
            logs.error("smtp", f"{recipient}: 验证码发送失败({err})")
            return {"success": False, "error": f"发送失败: {err}"}

        finally:
            server.quit()


    def _verify_email(data):
        for i in list(captchas.keys()):
            if int(captchas[i][1]) < int(time.time()):
                del captchas[i]

        email: str = data.get("email")
        input_captcha: str = data.get("code")

        if captchas[email][0] == input_captcha:
            logs.info("smtp", f"{email}: 验证通过")
            return {"success": True, "data": "验证通过"}
        else:
            logs.info("smtp", f"{email}: 验证失败")
            return {"success": False, "data": "验证失败"}



if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="smtp--pymsg邮箱验证码服务")
    parser.add_argument("--addr", default="127.0.0.1", help="监听地址")
    parser.add_argument("--port", type=int, default=8008, help="监听端口")
    parser.add_argument("--workers", type=int, default=4,
                        help="回调并发线程数（0=同步串行模式）")
    args = parser.parse_args()

    server = SmtpServer(args.addr, args.port)
    try:
        server.start()
    except KeyboardInterrupt:
        server.stop()
