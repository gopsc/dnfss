const form = document.getElementById('form');
const emailInput = document.getElementById('regemail');
const codeInput = document.getElementById('code');
const getCodeButton = document.getElementById('getCode');
const passwordInput = document.getElementById('regpsw');
const repeatPasswordInput = document.getElementById('repwd');
const emlTip = document.getElementById('emlTip');
const pwdTip = document.getElementById('pwdTip');
const codeTip = document.getElementById('codeTip');
const submitButton = document.getElementById('formsub');

let codeTarget = '';	// 已发送验证码的目标邮箱（验证码本体由服务端校验，前端不经手）

form.addEventListener('reset', function () {
	clearTipStyle();
	codeTarget = '';
});

function clearTipStyle() {
	emlTip.innerHTML = '';
	pwdTip.innerHTML = '';
	codeTip.innerHTML = '';

	emlTip.classList.remove('tip-red', 'tip-yellow', 'tip-green');
	pwdTip.classList.remove('tip-red', 'tip-yellow', 'tip-green');
	codeTip.classList.remove('tip-red', 'tip-yellow', 'tip-green');
}
clearTipStyle();

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

	if (!codeTarget) {
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
	// 验证码正确性由服务端在注册提交时校验（一次性消费）
	return code;
}

passwordInput.addEventListener('focus', checkPassword);
repeatPasswordInput.addEventListener('focus', checkPassword);

passwordInput.addEventListener('input', checkPassword);
repeatPasswordInput.addEventListener('input', checkPassword);

passwordInput.addEventListener('blur', checkPassword);
repeatPasswordInput.addEventListener('blur', checkPassword);

emailInput.addEventListener('focus', checkEmail);
emailInput.addEventListener('input', function () {
	checkEmail();
	if (codeTarget) {
		checkCode();
	}
});
emailInput.addEventListener('blur', checkEmail);

codeInput.addEventListener('focus', checkCode);
codeInput.addEventListener('input', checkCode);
codeInput.addEventListener('blur', checkCode);

getCodeButton.addEventListener('click', async function () {
	if (getCodeButton.disabled) return;	// 防重复点击
	const email = checkEmail();
	if (!email) {
		return;
	}

	getCodeButton.disabled = true;
	codeTip.textContent = '验证码发送中…';
	codeTip.classList.remove('tip-red', 'tip-yellow', 'tip-green');
	codeTip.classList.add('tip-yellow');
	try {
		const res = await fetch('/register/send_code', {
			method: 'POST',
			headers: { 'Content-Type': 'application/json' },
			body: JSON.stringify({ email })
		});
		const responseText = await res.text();
		let result;
		try { result = JSON.parse(responseText); } catch { throw new Error('服务器响应格式异常'); }
		if (!res.ok || !result.success) throw new Error(result.message || '验证码发送失败');

		codeTarget = email;
		codeInput.value = '';
		codeTip.textContent = '验证码已发送至邮箱，请查收（60 秒内有效）';
		codeTip.classList.remove('tip-red', 'tip-yellow', 'tip-green');
		codeTip.classList.add('tip-green');
	} catch (err) {
		codeTip.textContent = err.message || '验证码发送失败，请稍后重试';
		codeTip.classList.remove('tip-red', 'tip-yellow', 'tip-green');
		codeTip.classList.add('tip-red');
	} finally {
		getCodeButton.disabled = false;
	}
});

form.addEventListener('submit', async function (e) {
	e.preventDefault();

	const email = checkEmail();
	const password = checkPassword();
	const code = checkCode();

	if (!email || !password || !code) {
		return;
	}
	const jsonBody = JSON.stringify({
		username: email,
		password,
		email_code: code
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

		if (result.success) {
			alert('注册成功');
			form.reset();
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
