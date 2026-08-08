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
	const ctaBtns = document.querySelectorAll('[data-cta]'); // 首屏与页尾 CTA 按钮
	const logoEl = document.getElementById('logo');

	let loggedIn = false;

	function go(url) { window.location.href = url; }

	logoEl.addEventListener('click', () => go('index.html'));
	enterBtn.addEventListener('click', () => go('dnfs.html'));
	loginBtn.addEventListener('click', () => go('login.html'));
	registerBtn.addEventListener('click', () => go('register.html'));
	ctaBtns.forEach(function (btn) {
		btn.addEventListener('click', () => go(loggedIn ? 'dnfs.html' : 'login.html'));
	});

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
				ctaBtns.forEach(function (btn) { btn.textContent = '进入我的知识树'; });
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

/* ===== 视觉增强：滚动渐显 + 卡片光标光斑（装饰性，与登录逻辑解耦） ===== */
(function () {
	'use strict';

	const reduceMotion = !window.matchMedia('(prefers-reduced-motion: no-preference)').matches;

	/* 滚动进入视口渐显：由 JS 添加 .reveal 隐藏初始态，无 JS / 减弱动效时内容天然可见 */
	if (!reduceMotion && 'IntersectionObserver' in window) {
		const targets = document.querySelectorAll('.section-eyebrow, .section-title, .section-sub, .feature-card, .site-intro, .about-stats, .quick-links, .cta-band');
		targets.forEach(function (el, i) {
			el.classList.add('reveal');
			el.style.transitionDelay = (i % 4) * 70 + 'ms';
		});
		const io = new IntersectionObserver(function (entries) {
			entries.forEach(function (entry) {
				if (!entry.isIntersecting) return;
				const el = entry.target;
				el.classList.add('reveal-in');
				io.unobserve(el);
				/* 动画结束后移除类与延迟，把 transition 控制权交还 hover 等交互样式 */
				setTimeout(function () {
					el.classList.remove('reveal', 'reveal-in');
					el.style.transitionDelay = '';
				}, 900);
			});
		}, { threshold: 0.15, rootMargin: '0px 0px -8% 0px' });
		targets.forEach(function (el) { io.observe(el); });
	}

	/* 特性卡片光标光斑：鼠标位置写入 CSS 变量，由 index.css 径向渐变消费（仅精确指针设备） */
	if (window.matchMedia('(hover: hover) and (pointer: fine)').matches) {
		document.querySelectorAll('.feature-card').forEach(function (card) {
			card.addEventListener('mousemove', function (e) {
				const rect = card.getBoundingClientRect();
				card.style.setProperty('--mx', (e.clientX - rect.left) + 'px');
				card.style.setProperty('--my', (e.clientY - rect.top) + 'px');
			});
		});
	}
})();
