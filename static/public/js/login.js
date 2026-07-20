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
const submit = document.getElementById('loginBtn');

const resetUser = document.getElementById('resetUser');
const registerBtn = document.getElementById('registerBtn');

resetUser.addEventListener('click', function() {
	clearTipStyle();
})

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
	const val = usernameInput.value.trim();
	msgTip.innerHTML = '';
	msgTip.classList.remove('tip-red', 'tip-yellow', 'tip-green');

	if (!val) {
		msgTip.innerHTML = '用户名不可为空';
		msgTip.classList.add('tip-red');
		return false;
	}
	if (val.length > 20) {
		msgTip.innerHTML = '用户名不得长于20字符！';
		msgTip.classList.add('tip-yellow');
		return false;
	}

	msgTip.innerHTML = '√ 用户名格式正确';
	msgTip.classList.add('tip-green');
	return val;
}

/**
 * @returns {string|false}
 */
function checkPwd() {
	const val = passwordInput.value.trim();
	pwdTip.innerHTML = '';
	pwdTip.classList.remove('tip-red', 'tip-yellow', 'tip-green');

	if (!val) {
		pwdTip.innerHTML = '密码不可为空！';
		pwdTip.classList.add('tip-red');
		return false;
	}

	pwdTip.innerHTML = '河狸';
	pwdTip.classList.add('tip-green');
	return val;
}

/**
 * @returns {string|false}
 */
function checkEml() {
	const val = emailInput.value.trim();
	const emlStand = /^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$/;
	emlTip.innerHTML = '';
	emlTip.classList.remove('tip-red', 'tip-yellow', 'tip-green');

	if (!val) {
		emlTip.innerHTML = '邮箱不可为空！';
		emlTip.classList.add('tip-red');
		return false;
	} else if (!emlStand.test(val)) {
		emlTip.innerHTML = '邮箱格式不正确！';
		emlTip.classList.add('tip-yellow');
		return false;
	}

	emlTip.innerHTML = '河狸';
	emlTip.classList.add('tip-green');
	return val;
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
})

/**
 * @param {string}
 * @returns {string}
 */
function htmlEscape(str) {
    if (typeof str !== 'string') return str;
    return str
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#039;');
}

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
		const res = await fetch('/login', {
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
		} catch (err) {
			throw new Error(`服务器返回了非 JSON 内容（HTTP ${res.status}）：${responseText.slice(0, 100)}`);
		}

		if (!res.ok) {
			throw new Error(result.message || `登录失败（HTTP ${res.status}）`);
		}

        if (result.code === 0) {
            alert(`${result.message}，账号：${result.data.username}`);
            sessionStorage.setItem('token', result.data.token);
            location.href = 'main.html';
        } else {
			alert(result.message || '登录失败');
        }
    } catch (err) {
		alert(err.message || '接口请求失败，请检查后端服务是否启动');
        console.error('登录请求异常：', err);
    }
});
