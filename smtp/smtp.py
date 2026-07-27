#!/usr/bin/env python3

from flask import Flask, request, jsonify
from email.mime.text import MIMEText
from email import header
from log_client import logs
from dotenv import load_dotenv
import waitress
import smtplib
import random
import time
import sys, os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))

app = Flask(__name__)
captchas: dict[str, list[str, str]] = {}

@app.route("/send_email", methods=["POST"])

def send_email():
    def build_captcha() -> str:
        captcha: str = ""
        for i in range(0,6):
            digit: int = random.randint(0,9)
            captcha += str(digit)
        return captcha

    data = request.get_json()

    recipient: str = data.get("email")
    subject: str = "DNFS 邮箱验证"
    captcha: str = build_captcha()
    content: str = f"DNFS: 您的邮箱验证码为 {captcha}，一分钟内有效。"

    captchas[recipient] = ["", ""]
    captchas[recipient][0] = captcha
    captchas[recipient][1] = str(int(time.time())+60)

    message = MIMEText(content, "plain", "UTF-8")
    message["From"] = "dnfs@qsont.me"
    message["To"] = recipient
    message["Subject"] = subject

    try:
        server = smtplib.SMTP_SSL(SMTP_SERVER, SMTP_PORT, timeout=10)
        server.login(SMTP_USER, __PASSWORD)
        server.sendmail(SMTP_USER, [recipient], message.as_string())
        logs.info("smtp", f"{recipient}: 验证码发送成功({captcha})")
        return jsonify({"success": "验证码发送成功"}), 200

    except Exception as err:
        logs.error("smtp", f"{recipient}: 验证码发送失败({err})")
        return jsonify({"error": f"验证码发送失败：{err}"}), 400

    finally:
        server.quit()


@app.route("/verify_email", methods=["POST"])

def verify_email():
    data = request.get_json()

    for i in list(captchas.keys()):
        if int(captchas[i][1]) < int(time.time()):
            del captchas[i]

    email: str = data.get("email")
    input_captcha: str = data.get("input_captcha")

    if captchas[email][0] == input_captcha:
        logs.info("smtp", f"{email}: 验证通过")
        return jsonify({"success": "验证通过"}), 200
    else:
        logs.info("smtp", f"{email}: 验证失败")
        return jsonify({"error": "验证失败"}), 400


if __name__ == "__main__":
    logs.info("smtp", "开始监听http://127.0.0.1:8008")
    print("Listen at http://127.0.0.1:8008")
    waitress.serve(app, host="127.0.0.1", port=8008)