const form = document.getElementById('form');
const usernameInput = document.getElementById('regusername');
const emailInput = document.getElementById('regemail');
const codeInput = document.getElementById('code');
const getCodeButton = document.getElementById('getCode');
const passwordInput = document.getElementById('regpsw');
const repeatPasswordInput = document.getElementById('repwd');
const msgTip = document.getElementById('msgTip');
const emlTip = document.getElementById('emlTip');
const pwdTip = document.getElementById('pwdTip');
const codeTip = document.getElementById('codeTip');
const submitButton = document.getElementById('formsub');

let sendCode = '';
let codeTarget = '';

form.addEventListener('reset', function () {
	clearTipStyle();
	sendCode = '';
	codeTarget = '';
});

function clearTipStyle() {
	msgTip.innerHTML = '';
	emlTip.innerHTML = '';
	pwdTip.innerHTML = '';
	codeTip.innerHTML = '';

	msgTip.classList.remove('tip-red', 'tip-yellow', 'tip-green');
	emlTip.classList.remove('tip-red', 'tip-yellow', 'tip-green');
	pwdTip.classList.remove('tip-red', 'tip-yellow', 'tip-green');
	codeTip.classList.remove('tip-red', 'tip-yellow', 'tip-green');
}
clearTipStyle();

function checkUsername() {
	const result = FormValidators.username(usernameInput.value);
	msgTip.innerHTML = '';
	msgTip.classList.remove('tip-red', 'tip-yellow', 'tip-green');
	msgTip.textContent = result.message;
	msgTip.classList.add(result.className);
	return result.valid ? result.value : false;
}

function checkEmail() {
	const result = FormValidators.email(emailInput.value);
	emlTip.innerHTML = '';
	emlTip.classList.remove('tip-red', 'tip-yellow', 'tip-green');
	emlTip.textContent = result.message;
	emlTip.classList.add(result.className);
	return result.valid ? result.value : false;
}

function checkPassword() {
	const result = FormValidators.password(passwordInput.value, 6);
	pwdTip.innerHTML = '';
	pwdTip.classList.remove('tip-red', 'tip-yellow', 'tip-green');

	if (!result.valid) {
		pwdTip.textContent = result.message;
		pwdTip.classList.add(result.className);
		return false;
	}
	if (result.value !== repeatPasswordInput.value) {
		pwdTip.textContent = '两次输入的密码不一致！';
		pwdTip.classList.add('tip-yellow');
		return false;
	}

	pwdTip.textContent = result.message;
	pwdTip.classList.add(result.className);
	return result.value;
}

function checkCode() {
	const code = codeInput.value.trim();
	codeTip.innerHTML = '';
	codeTip.classList.remove('tip-red', 'tip-yellow', 'tip-green');

	if (!sendCode) {
		codeTip.textContent = '请先获取验证码！';
		codeTip.classList.add('tip-red');
		return false;
	}
	if (emailInput.value.trim() !== codeTarget) {
		codeTip.textContent = '邮箱已修改，请重新获取验证码！';
		codeTip.classList.add('tip-red');
		return false;
	}
	if (!/^\d{6}$/.test(code)) {
		codeTip.textContent = '验证码必须是6位数字！';
		codeTip.classList.add('tip-yellow');
		return false;
	}
	if (code !== sendCode) {
		codeTip.textContent = '验证码错误，请核对！';
		codeTip.classList.add('tip-red');
		return false;
	}

	codeTip.textContent = '验证码正确';
	codeTip.classList.add('tip-green');
	return code;
}

usernameInput.addEventListener('focus', checkUsername);
passwordInput.addEventListener('focus', checkPassword);
repeatPasswordInput.addEventListener('focus', checkPassword);

usernameInput.addEventListener('input', checkUsername);
passwordInput.addEventListener('input', checkPassword);
repeatPasswordInput.addEventListener('input', checkPassword);

usernameInput.addEventListener('blur', checkUsername);
passwordInput.addEventListener('blur', checkPassword);
repeatPasswordInput.addEventListener('blur', checkPassword);

emailInput.addEventListener('focus', checkEmail);
emailInput.addEventListener('input', function () {
	checkEmail();
	if (sendCode) {
		checkCode();
	}
});
emailInput.addEventListener('blur', checkEmail);

codeInput.addEventListener('focus', checkCode);
codeInput.addEventListener('input', checkCode);
codeInput.addEventListener('blur', checkCode);

getCodeButton.addEventListener('click', function () {
	const email = checkEmail();
	if (!email) {
		return;
	}

	sendCode = Math.floor(100000 + Math.random() * 900000).toString();
	codeTarget = email;
	codeInput.value = '';
	codeTip.textContent = '验证码已发送，请填写验证码';
	codeTip.classList.remove('tip-red', 'tip-yellow', 'tip-green');
	codeTip.classList.add('tip-green');
	alert(`验证码已发送至${email}，您的验证码：${sendCode}`);
});

form.addEventListener('submit', async function (e) {
	e.preventDefault();

	const username = checkUsername();
	const email = checkEmail();
	const password = checkPassword();
	const code = checkCode();

	if (!username || !email || !password || !code) {
		return;
	}
	const jsonBody = JSON.stringify({
		username,
		password
	});

	try {
		submitButton.disabled = true;
		const res = await fetch('/register', {
			method: 'POST',
			headers: {
				'Content-Type': 'application/x-www-form-urlencoded'
			},
			body: jsonBody
		});

		const responseText = await res.text();
		let result;

		try {
			result = JSON.parse(responseText);
		} catch {
			console.error(`注册接口返回非 JSON 内容（HTTP ${res.status}）：`, responseText);
			throw new Error('服务器响应格式异常，请稍后重试');
		}

		if (!res.ok) {
			throw new Error(result.message || `注册失败（HTTP ${res.status}）`);
		}

		if (result.code === 0) {
			if (!result.data || !result.data.username) {
				throw new Error('注册成功，但服务器返回的数据不完整');
			}
			alert(`${result.message}，账号：${result.data.username}`);
			form.reset();
			sendCode = '';
			codeTarget = '';
			location.href = 'login.html';
			return;
		}

		alert(result.message || '注册失败');
	} catch (err) {
		console.error('注册请求异常：', err);
		alert(err.message || '接口请求失败，请检查后端服务是否启动');
	} finally {
		submitButton.disabled = false;
	}
});
