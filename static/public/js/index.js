/* index.js — 社区首页逻辑
 *
 * 登录态检测：POST /whoami（HttpOnly q4_token Cookie 自动携带，JS 不经手 Token）
 * 已登录：显示用户名与"进入笔记/退出"按钮；未登录：显示"登录/注册"
 */
(function () {
	'use strict';

	const nameEl = document.getElementById('name');
	const enterBtn = document.getElementById('enterBtn');
	const logoutBtn = document.getElementById('logoutBtn');
	const loginBtn = document.getElementById('loginBtn');
	const registerBtn = document.getElementById('registerBtn');

	async function checkLogin() {
		try {
			const res = await fetch('/whoami', { method: 'POST' });
			if (!res.ok) return;
			const data = await res.json();
			if (data && data.login && data.user) {
				nameEl.textContent = String(data.user);
				enterBtn.style.display = '';
				logoutBtn.style.display = '';
				loginBtn.style.display = 'none';
				registerBtn.style.display = 'none';
			}
		} catch (e) {
			console.warn('index: 登录态检测失败', e.message);
		}
	}

	logoutBtn.addEventListener('click', async function () {
		try {
			await fetch('/logout', { method: 'POST' });
		} catch (e) {
			console.warn('index: 退出登录请求异常', e.message);
		}
		location.reload();
	});

	checkLogin();
})();
