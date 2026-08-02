/* index.js — 社区首页逻辑
 *
 * 登录态检测：POST /whoami（HttpOnly q4_token Cookie 自动携带，JS 不经手 Token）
 * 已登录：显示头像/用户名与"进入笔记/退出"按钮，首屏 CTA 指向笔记页
 * 未登录：显示"登录/注册"，首屏 CTA 指向登录页
 */
(function () {
	'use strict';

	const nameEl = document.getElementById('name');
	const avatarEl = document.getElementById('avatar');
	const enterBtn = document.getElementById('enterBtn');
	const logoutBtn = document.getElementById('logoutBtn');
	const loginBtn = document.getElementById('loginBtn');
	const registerBtn = document.getElementById('registerBtn');
	const ctaBtn = document.getElementById('ctaBtn');
	const logoEl = document.getElementById('logo');

	let loggedIn = false;

	function go(url) { window.location.href = url; }

	logoEl.addEventListener('click', () => go('index.html'));
	enterBtn.addEventListener('click', () => go('dnfs.html'));
	loginBtn.addEventListener('click', () => go('login.html'));
	registerBtn.addEventListener('click', () => go('register.html'));
	ctaBtn.addEventListener('click', () => go(loggedIn ? 'dnfs.html' : 'login.html'));

	async function checkLogin() {
		try {
			const res = await fetch('/whoami', { method: 'POST' });
			if (!res.ok) return;
			const data = await res.json();
			if (data && data.login && data.user) {
				loggedIn = true;
				nameEl.textContent = String(data.user);
				nameEl.style.display = '';
				avatarEl.style.display = '';
				enterBtn.style.display = '';
				logoutBtn.style.display = '';
				loginBtn.style.display = 'none';
				registerBtn.style.display = 'none';
				ctaBtn.textContent = '进入我的知识树';
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
