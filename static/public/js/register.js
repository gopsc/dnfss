let sendCode = "";
let codeTarget = "";

getCode.onclick = function () {
	const phoneMail = email.value.trim();

	if (phoneMail === "") {
		alert("请先填写邮箱号！");
		return;
	}

	sendCode = Math.floor(100000 + Math.random() * 900000).toString();
	codeTarget = phoneMail;

	alert(`验证码已发送至 ${phoneMail}，您的验证码：${sendCode}`);
};

regForm.onsubmit = function (e) {
	e.preventDefault();

	const uname = username.value.trim();
	const emailVal = email.value.trim();
	const codeVal = code.value.trim();
	const pwdVal = psw.value;
	const repwdVal = repwd.value;

	if (uname === "") {
		alert("请填写用户名");
		return;
	}

	if (emailVal === "") {
		alert("请填写邮箱号");
		return;
	}

	if (sendCode === "") {
		alert("请先点击获取验证码！");
		return;
	}

	if (emailVal !== codeTarget) {
		alert("邮箱已修改，请重新获取验证码！");
		sendCode = "";
		return;
	}

	if (!/^\d{6}$/.test(codeVal)) {
		alert("验证码必须是6位数字");
		return;
	}

	if (codeVal !== sendCode) {
		alert("验证码错误，请核对！");
		code.value = "";
		return;
	}

	if (pwdVal.length < 6) {
		alert("密码至少6位字符");
		return;
	}

	if (pwdVal !== repwdVal) {
		alert("两次输入的密码不一致");
		return;
	}

	alert("注册成功！");
	regForm.reset();
	sendCode = "";
	codeTarget = "";
};