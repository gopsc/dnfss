const msgTip = document.getElementById('msgTip');
const pwdTip = document.getElementById('pwdTip');
const emlTip = document.getElementById('emlTip');
const agreeTip = document.getElementById('agreeTip');
const form = document.getElementById('form');

const usernameInput = document.getElementById('loginUsername');
const passwordInput = document.getElementById('loginPassword');
const emailInput = document.getElementById('loginEmail');
const agreeBox = document.getElementById('agreeBox');
const protocol = document.getElementById('agreement');
const resetUser = document.getElementById('resetUser');
const registerBtn = document.getElementById('registerBtn');
const loginButton = document.getElementById('loginBtn');

resetUser.addEventListener('click', function() {
	clearTipStyle();
});

function clearTipStyle() {
	msgTip.innerHTML = '';
	pwdTip.innerHTML = '';
	agreeTip.innerHTML = '';
	emlTip.innerHTML = '';

	msgTip.classList.remove('tip-red', 'tip-yellow', 'tip-green');
	pwdTip.classList.remove('tip-red', 'tip-yellow', 'tip-green');
	agreeTip.classList.remove('tip-red', 'tip-yellow', 'tip-green');
	emlTip.classList.remove('tip-red', 'tip-yellow', 'tip-green');
	protocol.classList.remove('tip-red');
	protocol.classList.remove('tip-green');
}
clearTipStyle();

/**
 * @returns {string|false}
 */
function checkUsername() {
	const result = FormValidators.username(usernameInput.value);
	msgTip.innerHTML = '';
	msgTip.classList.remove('tip-red', 'tip-yellow', 'tip-green');
	msgTip.textContent = result.message;
	msgTip.classList.add(result.className);
	return result.valid ? result.value : false;
}

/**
 * @returns {string|false}
 */
function checkPwd() {
	const result = FormValidators.password(passwordInput.value);
	pwdTip.innerHTML = '';
	pwdTip.classList.remove('tip-red', 'tip-yellow', 'tip-green');
	pwdTip.textContent = result.message;
	pwdTip.classList.add(result.className);
	return result.valid ? result.value : false;
}

/**
 * @returns {string|false}
 */
function checkEml() {
	const result = FormValidators.email(emailInput.value);
	emlTip.innerHTML = '';
	emlTip.classList.remove('tip-red', 'tip-yellow', 'tip-green');
	emlTip.textContent = result.message;
	emlTip.classList.add(result.className);
	return result.valid ? result.value : false;
}

/**
 * @returns {boolean}
 */
function checkAgree() {
	agreeTip.innerHTML = '';
	agreeTip.classList.remove('tip-red');
	protocol.classList.remove('tip-green', 'tip-red');
	if (!agreeBox.checked) {
		agreeTip.innerHTML = '请同意用户协议！';
		agreeTip.classList.add('tip-red');
		protocol.classList.add('tip-red');
		return false;
	}
	protocol.classList.add('tip-green');
	return true;
}

usernameInput.addEventListener('focus', checkUsername);
passwordInput.addEventListener('focus', checkPwd);

usernameInput.addEventListener('input', checkUsername);
passwordInput.addEventListener('input', checkPwd);

usernameInput.addEventListener('blur', checkUsername);
passwordInput.addEventListener('blur', checkPwd);

emailInput.addEventListener('focus', checkEml);
emailInput.addEventListener('input', checkEml);
emailInput.addEventListener('blur', checkEml);

agreeBox.addEventListener('change', checkAgree);

registerBtn.addEventListener('click', function() {
	location.href = 'register.html';
});

form.addEventListener('submit', async function(e) {
	e.preventDefault();
	clearTipStyle();

	const userOk = checkUsername();
	const pwdOk = checkPwd();
	const emlOk = checkEml();
	const agreeOk = checkAgree();

	if (!(userOk && pwdOk && emlOk && agreeOk)) {
		return;
	}

	const jsonBody = JSON.stringify({
		username: userOk,
		password: pwdOk
	});
	
	try {
		loginButton.disabled = true;
		const res = await fetch('/login', {
			method: 'POST',
			headers: {
				'Content-Type': 'application/json'	// 告诉服务器内容为json
			},
			body: jsonBody
		});

		const responseText = await res.text();
		let result;

		try {
			result = JSON.parse(responseText);
		} catch {
			console.error(`登录接口返回非 JSON 内容（HTTP ${res.status}）：`, responseText);
			throw new Error('服务器响应格式异常，请稍后重试');
		}

		if (!res.ok) {
			throw new Error(result.message || `登录失败（HTTP ${res.status}）`);
		}

		if (result.success) {
			alert(`登陆成功`);
			sessionStorage.setItem('token', result.data.token);
			location.href = 'dnfs.html';
		} else {
			alert(result.message || '登录失败');
		}

	} catch (err) {
		alert(err.message || '接口请求失败，请检查后端服务是否启动');
		console.error('登录请求异常：', err);
	} finally {
		loginButton.disabled = false;
	}
});
