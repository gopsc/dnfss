# SMTP API 手册
## `/send_email`
```json
{
    "email": "接收验证码的邮箱"
}
```

## `/verify_email`
```json
{
    "email": "刚才接收验证码的邮箱",
    "input_captcha": "输入的验证码"
}

```