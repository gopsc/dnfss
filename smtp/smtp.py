from flask import Flask,app
from urllib import request
from email.mime.text import MIMEText
from email.header import Header
import smtplib
import random
from waitress import serve

verification_code = 0

@app.route('/send_email', methods=["POST"])

def send_email():
    data = request.get_json()

    reciver_email = data.get('email')
    SMTP_SERVER = ''
    SMTP_PORT = 587
    SENDER_EMAIL = ''
    __PASS = ''

    verification_code = random.randint(1000,9999)

    subject = "DNFS 验证码"
    content = f"DNFS: 您的验证码为{str(verification_code)}"

    email_message = MIMEText(content, 'plain', 'utf-8')
    email_message['From'] = Header(SENDER_EMAIL, 'utf-8')
    email_message['To'] = Header(reciver_email, 'utf-8')
    email_message['Subject'] = Header(subject)

    try:
        server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT)
        server.starttls()
        server.login(SENDER_EMAIL, __PASS)
        server.sendmail(SENDER_EMAIL, [reciver_email], email_message.as_string())
        return 200

    except Exception as err:
        raise err
        return 400

    finally:
        server.quit()

@app.route('/get_verification_code', method=["POST"])

def get_verification_code():
    data=request.get_json()

    input_verification_code = data.get('verification_code')

    if verification_code == input_verification_code:
        return 200
    else:
        return 400

ADDR = '127.0.0.1'
PORT = 8007
if __name__ == "__main__":
    print(f'Listen at http://{ADDR}:{PORT}')
    serve(app, host=ADDR, port=PORT)