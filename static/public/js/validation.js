(function () {
	function username(value) {
		const normalizedValue = value.trim();

		if (!normalizedValue) {
			return { valid: false, message: '用户名不可为空', className: 'tip-red' };
		}
		if (normalizedValue.length > 20) {
			return { valid: false, message: '用户名不得长于20字符！', className: 'tip-yellow' };
		}

		return {
			valid: true,
			value: normalizedValue,
			message: '用户名格式正确',
			className: 'tip-green'
		};
	}

	function password(value, minLength) {
		const requiredLength = minLength || 1;

		if (!value) {
			return { valid: false, message: '密码不可为空！', className: 'tip-red' };
		}
		if (value.length < requiredLength) {
			return {
				valid: false,
				message: `密码至少${requiredLength}位字符！`,
				className: 'tip-red'
			};
		}

		return {
			valid: true,
			value,
			message: '密码格式正确',
			className: 'tip-green'
		};
	}

	function email(value) {
		const normalizedValue = value.trim();
		const emailPattern = /^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$/;

		if (!normalizedValue) {
			return { valid: false, message: '邮箱不可为空！', className: 'tip-red' };
		}
		if (!emailPattern.test(normalizedValue)) {
			return { valid: false, message: '邮箱格式不正确！', className: 'tip-yellow' };
		}

		return {
			valid: true,
			value: normalizedValue,
			message: '邮箱格式正确',
			className: 'tip-green'
		};
	}

	function note_title(value) {
		return true;
	}

	function note_content(value) {
		return true;
	}

	window.FormValidators = { username, password, email };
}());

/* WAF 拦截识别：waf 命中规则时返回 403 + 纯文本 "blocked by WAF"
 * （见 waf/internal/proxy/proxy.go block()）；gateway 的 403（Target-Url
 * 白名单拒绝）body 为 JSON 错误消息，不含此特征，不会误判。 */
window.WafTip = {
	MESSAGE: '请求被 WAF 安全规则拦截，请检查输入内容',
	isBlocked(res, responseText) {
		return res.status === 403
			&& typeof responseText === 'string'
			&& responseText.indexOf('blocked by WAF') !== -1;
	}
};
