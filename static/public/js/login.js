const msgTip = document.getElementById('msgTip');
const pwdTip = document.getElementById('pwdTip');
const agreeTip = document.getElementById('agreeTip');
const captchaTip = document.getElementById('captchaTip');
const form = document.getElementById('form');
const usernameInput = document.getElementById('loginUsername');
const passwordInput = document.getElementById('loginPassword');
const captchaInput = document.getElementById('captchaAnswer');
const captchaImg = document.getElementById('captchaImg');
const agreeBox = document.getElementById('agreeBox');
const resetUser = document.getElementById('resetUser');
const registerBtn = document.getElementById('registerBtn');
const loginButton = document.getElementById('loginBtn');

/* ── 图形验证码（gateway 代理 captcha 服务：GET /captcha/image） ── */
let captchaToken = '';
let captchaLoading = false;	// 防重复点击：加载中忽略再次触发

async function refreshCaptcha() {
	if (captchaLoading) return;
	captchaLoading = true;
	captchaToken = '';
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
	} catch (e) {
		captchaImg.removeAttribute('src');
		captchaImg.alt = '加载失败，点击重试';
	} finally {
		captchaLoading = false;
	}
}
captchaImg.addEventListener('click', refreshCaptcha);
refreshCaptcha();

resetUser.addEventListener('click', function() { clearTipStyle(); });

function clearTipStyle() {
	msgTip.innerHTML = '';
	pwdTip.innerHTML = '';
	agreeTip.innerHTML = '';
	captchaTip.innerHTML = '';
	msgTip.classList.remove('tip-red', 'tip-yellow', 'tip-green');
	pwdTip.classList.remove('tip-red', 'tip-yellow', 'tip-green');
	agreeTip.classList.remove('tip-red');
	captchaTip.classList.remove('tip-red');
}
clearTipStyle();

function checkUsername() {
	const result = FormValidators.username(usernameInput.value);
	msgTip.textContent = result.message;
	msgTip.className = result.className;
	return result.valid ? result.value : false;
}

function checkPwd() {
	const result = FormValidators.password(passwordInput.value);
	pwdTip.textContent = result.message;
	pwdTip.className = result.className;
	return result.valid ? result.value : false;
}

function checkCaptcha() {
	captchaTip.innerHTML = '';
	captchaTip.classList.remove('tip-red');
	const v = captchaInput.value.trim();
	if (!v) {
		captchaTip.innerHTML = '请输入验证码！';
		captchaTip.classList.add('tip-red');
		return false;
	}
	if (!captchaToken) {
		captchaTip.innerHTML = '验证码未加载，请点击图片刷新';
		captchaTip.classList.add('tip-red');
		return false;
	}
	return v;
}

function checkAgree() {
	agreeTip.innerHTML = '';
	agreeTip.classList.remove('tip-red');
	if (!agreeBox.checked) {
		agreeTip.innerHTML = '请同意用户协议！';
		agreeTip.classList.add('tip-red');
		return false;
	}
	return true;
}

usernameInput.addEventListener('focus', checkUsername);
passwordInput.addEventListener('focus', checkPwd);
usernameInput.addEventListener('input', checkUsername);
passwordInput.addEventListener('input', checkPwd);
usernameInput.addEventListener('blur', checkUsername);
passwordInput.addEventListener('blur', checkPwd);
agreeBox.addEventListener('change', checkAgree);
registerBtn.addEventListener('click', function() { location.href = 'register.html'; });

form.addEventListener('submit', async function(e) {
	e.preventDefault();
	clearTipStyle();
	const userOk = checkUsername();
	const pwdOk = checkPwd();
	const captchaOk = checkCaptcha();
	const agreeOk = checkAgree();
	if (!userOk || !pwdOk || !captchaOk || !agreeOk) return;

	const jsonBody = JSON.stringify({
		username: userOk,
		password: pwdOk,
		captcha_token: captchaToken,
		captcha_answer: captchaOk
	});
	try {
		loginButton.disabled = true;
		const res = await fetch('/login', {
			method: 'POST',
			headers: {'Content-Type': 'application/json'},
			body: jsonBody
		});
		const responseText = await res.text();
		if (window.WafTip && WafTip.isBlocked(res, responseText)) throw new Error(WafTip.MESSAGE);
		let result;
		try { result = JSON.parse(responseText); } catch { throw new Error('服务器响应格式异常'); }
		if (!res.ok) throw new Error(result.message || '登录失败');
		if (result.success) {
			// Token 已由网关通过 HttpOnly Cookie 下发，JS 不再经手
			alert('登录成功');
			location.href = '/dnfs.html';
		} else {
			alert(result.message || '登录失败');
		}
	} catch (err) {
		alert(err.message || '接口请求失败');
	} finally {
		// 无论成败都刷新验证码：Token 已被一次性消费，或需防重试
		captchaInput.value = '';
		refreshCaptcha();
		loginButton.disabled = false;
	}
});
