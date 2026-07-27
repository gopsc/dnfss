const msgTip = document.getElementById('msgTip');
const pwdTip = document.getElementById('pwdTip');
const agreeTip = document.getElementById('agreeTip');
const form = document.getElementById('form');
const usernameInput = document.getElementById('loginUsername');
const passwordInput = document.getElementById('loginPassword');
const agreeBox = document.getElementById('agreeBox');
const resetUser = document.getElementById('resetUser');
const registerBtn = document.getElementById('registerBtn');
const loginButton = document.getElementById('loginBtn');

resetUser.addEventListener('click', function() { clearTipStyle(); });

function clearTipStyle() {
	msgTip.innerHTML = '';
	pwdTip.innerHTML = '';
	agreeTip.innerHTML = '';
	msgTip.classList.remove('tip-red', 'tip-yellow', 'tip-green');
	pwdTip.classList.remove('tip-red', 'tip-yellow', 'tip-green');
	agreeTip.classList.remove('tip-red');
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
	const agreeOk = checkAgree();
	if (!userOk || !pwdOk || !agreeOk) return;

	const jsonBody = JSON.stringify({ username: userOk, password: pwdOk });
	try {
		loginButton.disabled = true;
		const res = await fetch('/login', {
			method: 'POST',
			headers: {'Content-Type': 'application/json'},
			body: jsonBody
		});
		const responseText = await res.text();
		let result;
		try { result = JSON.parse(responseText); } catch { throw new Error('服务器响应格式异常'); }
		if (!res.ok) throw new Error(result.message || '登录失败');
		if (result.success) {
			localStorage.setItem('q4_token', result.data.token);
			sessionStorage.setItem('q4_token', result.data.token);
			alert('登录成功');
			location.href = '/dnfs.html';
		} else {
			alert(result.message || '登录失败');
		}
	} catch (err) {
		alert(err.message || '接口请求失败');
	} finally {
		loginButton.disabled = false;
	}
});
