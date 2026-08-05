const form = document.getElementById('form');
const emailInput = document.getElementById('regemail');
const codeInput = document.getElementById('code');
const getCodeButton = document.getElementById('getCode');
const passwordInput = document.getElementById('regpsw');
const repeatPasswordInput = document.getElementById('repwd');
const emlTip = document.getElementById('emlTip');
const pwdTip = document.getElementById('pwdTip');
const codeTip = document.getElementById('codeTip');
const captchaImg = document.getElementById('captchaImg');
const captchaInput = document.getElementById('captchaAnswer');
const captchaTip = document.getElementById('captchaTip');
const submitButton = document.getElementById('formsub');

let codeTarget = '';	// 已发送验证码的目标邮箱（验证码本体由服务端校验，前端不经手）
let cooldownTimer = null;	// 重发倒计时（与服务端同邮箱 60s 发送冷却对齐）

/* ── 图形验证码（gateway 代理 captcha 服务：GET /captcha/image） ── */
let captchaToken = '';
let captchaLoading = false;	// 防重复点击：加载中忽略再次触发

async function refreshCaptcha(preserveInput) {
	if (captchaLoading) return;
	captchaLoading = true;
	captchaToken = '';
	if (!preserveInput) captchaInput.value = '';  // 默认清空输入；preserveInput=true 时保留（获取验证码后）
	captchaImg.style.opacity = '0.5'; // 视觉提示：正在刷新
	try {
		const res = await fetch('/captcha/image', { cache: 'no-store' });
		if (res.status === 403) {	// WAF 拦截：body 非图片，提示后待用户点击重试
			captchaImg.removeAttribute('src');
			captchaImg.alt = '验证码加载被安全规则拦截，点击重试';
			return;
		}
		if (!res.ok) throw new Error('captcha load failed');
		captchaToken = res.headers.get('X-Captcha-Token') || '';
		const blob = await res.blob();
		if (captchaImg.src && captchaImg.src.startsWith('blob:')) URL.revokeObjectURL(captchaImg.src);
		captchaImg.src = URL.createObjectURL(blob);
		captchaImg.alt = '点击刷新';
		captchaImg.style.opacity = '';  // 恢复透明度
	} catch (e) {
		captchaImg.removeAttribute('src');
		captchaImg.alt = '加载失败，点击重试';
	} finally {
		captchaLoading = false;
	}
}
captchaImg.addEventListener('click', refreshCaptcha);
refreshCaptcha();

// 发送成功后进入倒计时，避免用户触发服务端同邮箱 60s 发送冷却
function startResendCooldown(seconds) {
	clearInterval(cooldownTimer);
	let remain = seconds;
	getCodeButton.disabled = true;
	getCodeButton.value = `${remain}s 后重发`;
	cooldownTimer = setInterval(function () {
		remain -= 1;
		if (remain <= 0) {
			clearInterval(cooldownTimer);
			cooldownTimer = null;
			getCodeButton.disabled = false;
			getCodeButton.value = '获取验证码';
		} else {
			getCodeButton.value = `${remain}s 后重发`;
		}
	}, 1000);
}

form.addEventListener('reset', function () {
	clearTipStyle();
	codeTarget = '';
	captchaInput.value = '';
	refreshCaptcha();
});

function clearTipStyle() {
	emlTip.innerHTML = '';
	pwdTip.innerHTML = '';
	codeTip.innerHTML = '';
	captchaTip.innerHTML = '';

	emlTip.classList.remove('tip-red', 'tip-yellow', 'tip-green');
	pwdTip.classList.remove('tip-red', 'tip-yellow', 'tip-green');
	codeTip.classList.remove('tip-red', 'tip-yellow', 'tip-green');
	captchaTip.classList.remove('tip-red', 'tip-yellow', 'tip-green');
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

function checkCaptcha() {
	captchaTip.innerHTML = '';
	captchaTip.classList.remove('tip-red', 'tip-yellow', 'tip-green');
	const v = captchaInput.value.trim();
	if (!v) {
		captchaTip.textContent = '请输入图形验证码！';
		captchaTip.classList.add('tip-red');
		return false;
	}
	if (!captchaToken) {
		captchaTip.textContent = '图形验证码未加载，请点击图片刷新';
		captchaTip.classList.add('tip-red');
		return false;
	}
	return v;
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
	if (!email) return;
	const captchaAnswer = checkCaptcha();
	if (!captchaAnswer) return;

	getCodeButton.disabled = true;
	codeTip.textContent = '验证码发送中…';
	codeTip.classList.remove('tip-red', 'tip-yellow', 'tip-green');
	codeTip.classList.add('tip-yellow');
	let httpStatus = 0;
	try {
		const res = await fetch('/register/send_code', {
			method: 'POST',
			headers: { 'Content-Type': 'application/json' },
			body: JSON.stringify({
				email,
				captcha_token: captchaToken,
				captcha_answer: captchaAnswer
			})
		});
		httpStatus = res.status;
		const responseText = await res.text();
		if (window.WafTip && WafTip.isBlocked(res, responseText)) throw new Error(WafTip.MESSAGE);
		let result;
		try { result = JSON.parse(responseText); } catch { throw new Error('服务器响应格式异常'); }
		if (!res.ok || !result.success) throw new Error(result.message || '验证码发送失败');

		codeTarget = email;
		codeInput.value = '';
		codeTip.textContent = '验证码已发送至邮箱，请查收（10 分钟内有效）';
		codeTip.classList.remove('tip-red', 'tip-yellow', 'tip-green');
		codeTip.classList.add('tip-green');
		startResendCooldown(60);
	} catch (err) {
		codeTip.textContent = err.message || '验证码发送失败，请稍后重试';
		codeTip.classList.remove('tip-red', 'tip-yellow', 'tip-green');
		codeTip.classList.add('tip-red');
		if (httpStatus === 429) startResendCooldown(60);	// 命中冷却/限流，倒计时对齐
	} finally {
		// CAPTCHA Token 一次性消费，无论成败都刷新图形验证码；
		// 刷新时保留已输入的图形验证码答案，避免误清空用户输入
		refreshCaptcha(true);
		if (!cooldownTimer) getCodeButton.disabled = false;
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
		if (window.WafTip && WafTip.isBlocked(res, responseText)) throw new Error(WafTip.MESSAGE);
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
