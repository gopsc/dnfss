// 认证改为 HttpOnly Cookie（gateway 下发 q4_token），浏览器同源请求自动携带，JS 不再经手 Token

/* by qing.
 *
 * 用于获取一个节点下的所有子节点信息，0为根节点。
 */
async function getNode(id) {
	try {
		const obj = { "pid": id }
		const jsonBody = JSON.stringify(obj)
		console.log(jsonBody)
		const res = await fetch('/get_notes', {
			method: 'POST',
			headers: {
				'Content-Type': 'application/json',	// 告诉服务器内容为json
				'Target-Url': 'http://127.0.0.1:8006'
			},
			body: jsonBody
		});

		const responseText = await res.text();
		// WAF 拦截：弹提示并中止，不能落入 catch（其会向上抛出）
		if (window.WafTip && WafTip.isBlocked(res, responseText)) {
			alert(WafTip.MESSAGE);
			return;
		}
		let result;

		try {
			result = JSON.parse(responseText);
		} catch {
			console.error(`获取笔记接口返回非 JSON 内容（HTTP ${res.status}）：`, responseText);
			throw new Error('服务器响应格式异常，请稍后重试');
		}

		if (res.status === 401) {
			// 凭证失效（过期或网关重启），仅此一种情况跳转重新登录
			location.href = '/login.html';
			return;
		}

		if (!res.ok) {
			throw new Error(result.message || `获取节点失败（HTTP ${res.status}）`);
		}

		//if (result.success) {
		console.log(`获取节点成功`);
		return result
		//...

		//} else {
		//	alert(result.message || '获取节点失败');
		//}

	} catch (err) {
		// 非 401 失败（网络异常 / 5xx / 非 JSON 响应）：提示真实原因并向上抛出，
		// 调用方 .then(load_notes/loadMap) 不再执行，当前页面内容保持不变；
		// 不再把任意错误误判为凭证失效而跳登录页
		console.error('获取节点请求异常：', err);
		alert(err.message || '获取节点失败，请检查网络后重试');
		throw err;
	}
}

/* by qing
 *
 * 用于向dnfs添加节点
 */
async function addNode(pid, title, content) {
	try {
		obj = {
			"pid": pid,
			"title": title,
			"content": content
		}
		jsonBody = JSON.stringify(obj)
		console.log(jsonBody)
		const res = await fetch('/add_note', {
			method: 'POST',
			headers: {
				'Content-Type': 'application/json',	// 告诉服务器内容为json
				'Target-Url': 'http://127.0.0.1:8006'
			},
			body: jsonBody
		});

		const responseText = await res.text();
		if (window.WafTip && WafTip.isBlocked(res, responseText)) throw new Error(WafTip.MESSAGE);
		let result;

		try {
			result = JSON.parse(responseText);
		} catch {
			console.error(`添加笔记接口返回非 JSON 内容（HTTP ${res.status}）：`, responseText);
			throw new Error('服务器响应格式异常，请稍后重试');
		}

		if (res.status === 401) {
			// 凭证失效（过期或网关重启），跳转重新登录
			location.href = '/login.html';
			return;
		}

		if (!res.ok) {
			throw new Error(result.error || `添加节点失败（HTTP ${res.status}）`);
		}

		input_note_title.value = "";
		input_note_content.value = "";
		cancelEdit();	/* 退出编辑模式并重置高度 */
		getNode(stack[stack.length - 1]).then(function(r) { load_notes(r); loadMap(r); })

		//if (result.success) {
		alert(`添加节点成功`, result);
		return result
		//...

		//} else {
		//	alert(result.message || '获取节点失败');
		//}

	} catch (err) {
		alert(err.message || '接口请求失败，请检查后端服务是否启动');
		console.error('添加节点请求异常：', err);
	} finally {
	}
}

async function deleteNode(id) {
	if (confirm("确认删除此节点？") == false) {
		return;
	}
	try {
		obj = {"id": id};
		jsonBody = JSON.stringify(obj);
		console.log(jsonBody);

		const res = await fetch('/delete_note', {
			method: 'POST',
			headers: {
				'Content-Type': 'application/json',
				'Target-Url': 'http://127.0.0.1:8006'
			},
			body: jsonBody
		});

		const responseText = await res.text();
		if (window.WafTip && WafTip.isBlocked(res, responseText)) throw new Error(WafTip.MESSAGE);
		let result;

		try {
			result = JSON.parse(responseText);
		} catch {
			console.error(`删除笔记接口返回非 JSON 内容（HTTP ${res.status}）：`, responseText);
			throw new Error('服务器响应格式异常，请稍后重试');
		}

		if (res.status === 401) {
			// 凭证失效（过期或网关重启），跳转重新登录
			location.href = '/login.html';
			return;
		}

		if (!res.ok) {
			throw new Error(`删除节点失败（HTTP ${res.status}）`);
		}

		stack.pop();
		stack_operate.pop();
		getNode(stack[stack.length - 1]).then(function(r) { load_notes(r); loadMap(r); })

		console.log(`删除节点成功`);
		return result

	} catch (err) {
		alert(err.message || '接口请求失败，请检查后端服务是否启动');
		console.error('删除节点请求异常：', err);
	}
}

/* by qing — 卡片上的"删除"：删除子节点后停留在当前层，仅刷新列表（不弹栈） */
async function deleteChild(id) {
	if (confirm("确认删除此节点？") == false) {
		return;
	}
	try {
		obj = {"id": id};
		jsonBody = JSON.stringify(obj);

		const res = await fetch('/delete_note', {
			method: 'POST',
			headers: {
				'Content-Type': 'application/json',
				'Target-Url': 'http://127.0.0.1:8006'
			},
			body: jsonBody
		});

		const responseText = await res.text();
		if (window.WafTip && WafTip.isBlocked(res, responseText)) throw new Error(WafTip.MESSAGE);
		let result;

		try {
			result = JSON.parse(responseText);
		} catch {
			console.error(`删除笔记接口返回非 JSON 内容（HTTP ${res.status}）：`, responseText);
			throw new Error('服务器响应格式异常，请稍后重试');
		}

		if (res.status === 401) {
			// 凭证失效（过期或网关重启），跳转重新登录
			location.href = '/login.html';
			return;
		}

		if (!res.ok) {
			throw new Error(result.error === "It's not your note" ? '只能删除自己的节点' : (result.error || `删除节点失败（HTTP ${res.status}）`));
		}

		// 若删掉的正是编辑中的节点，顺带退出编辑模式
		if (editing_id == id) {
			cancelEdit();
			input_note_title.value = "";
			input_note_content.value = "";
		}
		var cur = (stack.length == 0) ? 0 : stack[stack.length - 1];
		getNode(cur).then(function(r) { load_notes(r); loadMap(r); });

		console.log(`删除节点成功`);
		return result;

	} catch (err) {
		alert(err.message || '接口请求失败，请检查后端服务是否启动');
		console.error('删除节点请求异常：', err);
	}
}

/* by qing — 保存编辑：调用 /update_note，成功后刷新当前层并退出编辑模式 */
async function updateNode(id, pid, title, content) {
	try {
		obj = {
			"id": Number(id),
			"pid": pid,
			"title": title,
			"content": content
		}
		jsonBody = JSON.stringify(obj)
		const res = await fetch('/update_note', {
			method: 'POST',
			headers: {
				'Content-Type': 'application/json',
				'Target-Url': 'http://127.0.0.1:8006'
			},
			body: jsonBody
		});

		const responseText = await res.text();
		if (window.WafTip && WafTip.isBlocked(res, responseText)) throw new Error(WafTip.MESSAGE);
		let result;

		try {
			result = JSON.parse(responseText);
		} catch {
			console.error(`更新笔记接口返回非 JSON 内容（HTTP ${res.status}）：`, responseText);
			throw new Error('服务器响应格式异常，请稍后重试');
		}

		if (res.status === 401) {
			// 凭证失效（过期或网关重启），跳转重新登录
			location.href = '/login.html';
			return;
		}

		if (!res.ok) {
			throw new Error(result.error === "It's not your note" ? '只能编辑自己的节点' : (result.error || `更新节点失败（HTTP ${res.status}）`));
		}

		input_note_title.value = "";
		input_note_content.value = "";
		cancelEdit();
		var cur = (stack.length == 0) ? 0 : stack[stack.length - 1];
		getNode(cur).then(function(r) { load_notes(r); loadMap(r); });

		alert('修改成功');
		return result;

	} catch (err) {
		alert(err.message || '接口请求失败，请检查后端服务是否启动');
		console.error('更新节点请求异常：', err);
	}
}

/* by qing — 节点移动：本质是 pid 变更的 update_note，title/content 原样携带 */
async function moveNode(id, pid, title, content) {
	try {
		obj = {
			"id": Number(id),
			"pid": pid,
			"title": title,
			"content": content
		}
		jsonBody = JSON.stringify(obj)
		const res = await fetch('/update_note', {
			method: 'POST',
			headers: {
				'Content-Type': 'application/json',
				'Target-Url': 'http://127.0.0.1:8006'
			},
			body: jsonBody
		});

		const responseText = await res.text();
		if (window.WafTip && WafTip.isBlocked(res, responseText)) throw new Error(WafTip.MESSAGE);
		let result;

		try {
			result = JSON.parse(responseText);
		} catch {
			console.error(`更新笔记接口返回非 JSON 内容（HTTP ${res.status}）：`, responseText);
			throw new Error('服务器响应格式异常，请稍后重试');
		}

		if (res.status === 401) {
			// 凭证失效（过期或网关重启），跳转重新登录
			location.href = '/login.html';
			return;
		}

		if (!res.ok) {
			throw new Error(result.error === "It's not your note" ? '只能移动自己的节点' : (result.error || `移动节点失败（HTTP ${res.status}）`));
		}

		var cur = (stack.length == 0) ? 0 : stack[stack.length - 1];
		getNode(cur).then(function(r) { load_notes(r); loadMap(r); });

		alert('移动成功');
		return result;

	} catch (err) {
		alert(err.message || '接口请求失败，请检查后端服务是否启动');
		console.error('移动节点请求异常：', err);
	}
}

/* by qing — 点击卡片"移动"：暂存节点信息，底部弹出移动条 */
function startMove(item) {
	moving_node = { id: Number(item.id), pid: item.pid, title: item.title, content: item.content };
	renderMoveBar();
}

/* by qing — 渲染底部移动条：显示待移动节点，提供"移动到当前层"和"取消" */
function renderMoveBar() {
	movebar.replaceChildren();
	if (moving_node == null) {
		return;
	}
	let tip = document.createElement('span');
	tip.className = "move-title";
	tip.textContent = "移动中：" + moving_node.title;
	let ok_btn = document.createElement('button');
	ok_btn.className = "act-btn act-edit";
	ok_btn.textContent = "移动到当前层";
	ok_btn.addEventListener('click', confirmMove);
	let cancel_btn = document.createElement('button');
	cancel_btn.className = "act-btn act-del";
	cancel_btn.textContent = "取消";
	cancel_btn.addEventListener('click', function() {
		moving_node = null;
		renderMoveBar();
	});
	movebar.appendChild(tip);
	movebar.appendChild(ok_btn);
	movebar.appendChild(cancel_btn);
}

/* by qing — 执行移动：把待移动节点挂到当前层（stack 顶，根为 0）。
 * 后端 update_note 会走 AI 内容审核，耗时较长，期间禁用移动条与表单按钮防止重复提交 */
async function confirmMove() {
	if (moving_node == null) {
		return;
	}
	var target_pid = (stack.length == 0) ? 0 : stack[stack.length - 1];
	if (target_pid == moving_node.id) {
		alert('不能移动到节点自身内部');
		return;
	}
	if (target_pid == moving_node.pid) {
		alert('该节点已在当前层');
		return;
	}
	// 防重复点击：请求进行中禁用按钮（成功/失败都在 finally 恢复）
	movebar.querySelectorAll('button').forEach(function(b) { b.disabled = true; });
	input_note_submit.disabled = true;
	input_note_clear.disabled = true;
	delete_note.disabled = true;
	try {
		var result = await moveNode(moving_node.id, target_pid, moving_node.title, moving_node.content);
		if (result != null) {
			moving_node = null;
			renderMoveBar();
		}
	} finally {
		movebar.querySelectorAll('button').forEach(function(b) { b.disabled = false; });
		input_note_submit.disabled = false;
		input_note_clear.disabled = false;
		delete_note.disabled = false;
	}
}

/* by shufeng2012 */
function renderMarkdown(element, content) {
	/*
	Markdown渲染
	*/
	const md = window.markdownit({
		html: false,
		highlight: function(code, lang) {
			if (lang && hljs.getLanguage(lang)) {
				return `<pre><code class="hljs language-${lang}">${hljs.highlight(code, {language: lang}).value}</code></pre>`
			} else {
				return `<pre><code class="hljs">${md.utils.escapeHtml(code)}</code></pre>`
			}
		}
	});
	element.innerHTML = md.render(content);
}

/* ===== by qing — 特殊节点 =====
 * 信封：content 为 {"_sp":"js|file|style|theme", ...} JSON，普通内容走 Markdown。
 * 发布权限由后端 dnfs 强制（节点拥有者或管理员），前端仅做展示与表单切换。 */

/* 与后端 base64 上限（3.5M 字符）对齐：原始文件约 2.5MB */
var SP_MAX_FILE_BYTES = 2.5 * 1024 * 1024;

function parseSp(content) {
	if (typeof content !== 'string') return null;
	var s = content.trim();
	if (s.indexOf('{"_sp"') !== 0) return null;
	try {
		var obj = JSON.parse(s);
	} catch (e) {
		return null;	// 非法信封按普通文本展示（后端发布时已拦截，这里是兜底）
	}
	if (obj && typeof obj === 'object' && ['js', 'file', 'style', 'theme'].indexOf(obj._sp) !== -1) return obj;
	return null;
}

/* 内容渲染分发：特殊节点按类型渲染，否则 Markdown */
function renderContent(container, item) {
	var sp = parseSp(item.content);
	if (!sp) {
		renderMarkdown(container, item.content);
		return;
	}
	function appendNote() {
		if (typeof sp.note === 'string' && sp.note.trim()) {
			var note = document.createElement('div');
			note.className = 'sp-note';
			note.textContent = sp.note;
			container.appendChild(note);
		}
	}
	if (sp._sp === 'js') renderSpJs(container, sp, appendNote);
	else if (sp._sp === 'file') renderSpFile(container, sp, appendNote);
	else if (sp._sp === 'style' || sp._sp === 'theme') renderSpCss(container, sp, appendNote, item);
}

/* 前端代码节点：含前端内容的节点在渲染到当前页面时自动执行（隔离 iframe 沙箱）。
 * 采用 iframe 沙箱而非注入父页面全局作用域，原因：
 *   - 特效脚本运行在独立 iframe 内，无法访问父页面 DOM/JS，任何报错、死循环、变量覆盖
 *     都不会污染父页面，从而保证"运行特效后卡片点击/节点导航依然正常"；
 *   - 全屏特效 iframe 挂载到 document.body（z-index:9999 在根层叠上下文生效，
 *     不被后续卡片盖住），pointer-events:none 不拦截父页面点击，不影响卡片点击/节点导航；
 *   - 不使用 new Function/eval（CSP 未开 unsafe-eval），srcdoc 内联脚本由 CSP 'unsafe-inline' 放行。
 * 仅节点拥有者或管理员可发布（后端强制）。 */
/* 无特效模式：true 为关闭自动执行（用户面板开关控制，localStorage 持久化） */
var sp_effects_disabled = (function() {
	try { return localStorage.getItem('sp_effects_disabled') === '1'; } catch (e) { return false; }
})();
/* __ 隐藏节点可见：true 时 __ 前缀节点正常显示（用户面板开关控制，localStorage 持久化） */
var __show_hidden = (function() {
	try { return localStorage.getItem('dnfs_show_hidden') === '1'; } catch (e) { return false; }
})();
/* 清除当前页面已运行的全部特效沙箱与注入样式（进入新节点 / 关闭特效时调用） */
function clearAllEffects() {
	document.querySelectorAll('.sp-effect-frame').forEach(function(f) { f.remove(); });
	document.querySelectorAll('.sp-injected-style').forEach(function(s) { s.remove(); });
}
function renderSpJs(container, sp, appendNote) {
	var bar = document.createElement('div');
	bar.className = 'sp-actions';
	var audit_btn = document.createElement('button');
	audit_btn.className = 'act-btn act-edit';
	audit_btn.textContent = '审计源码';
	var toggle_btn = document.createElement('button');
	toggle_btn.className = 'act-btn act-move';
	toggle_btn.textContent = '停止特效';
	var pre = document.createElement('pre');
	pre.className = 'sp-code';
	pre.style.display = 'none';
	pre.textContent = sp.code;
	var out = document.createElement('div');
	out.className = 'sp-run';
	out.style.display = 'none';
	var frame = null;	/* 当前特效 iframe 沙箱实例，null 表示未运行 */
	audit_btn.addEventListener('click', function(e) {
		e.stopPropagation();
		var hidden = (pre.style.display === 'none');
		pre.style.display = hidden ? 'block' : 'none';
		audit_btn.textContent = hidden ? '收起源码' : '审计源码';
	});
	/* 启动特效：创建隔离 iframe 沙箱，完整前端代码（含 <style>/<script>/<body>）由 srcdoc 承载，
	 * 脚本在独立上下文执行，无法影响父页面导航。
	 * 两种模式：
	 *   - 代码含 "<!-- in-node -->" 标记 → 渲染在节点内部（relative 定位、可交互、随节点滚动），
	 *     iframe 挂载在节点容器内（随卡片隐藏/移除）；
	 *   - 否则 → 全屏 fixed 定位，视觉特效悬浮覆盖整个页面（pointer-events:none 不拦截父页面点击），
	 *     iframe 直接挂载到 document.body：若挂在卡片内部，卡片入场动画等 opacity<1 的属性
	 *     会在卡片上产生层叠上下文，iframe 的 z-index:9999 就被困在该卡片内部生效，
	 *     DOM 序靠后的兄弟卡片会把整张卡片（含特效）压在下面；挂到 body 后 z-index
	 *     直接在根层叠上下文竞争，恒位于所有卡片之上。
	 * 清理不受影响：clearAllEffects 按 class 全文档查找，两种挂载位置都能正确回收。 */
	function startEffect() {
		if (frame) return;
		frame = document.createElement('iframe');
		frame.className = 'sp-effect-frame';
		frame.setAttribute('sandbox', 'allow-scripts');
		frame.srcdoc = sp.code;
		var inNode = /<!--\s*in-node\s*-->/.test(sp.code || '');
		if (inNode) {
			frame.style.cssText = 'position:relative;display:block;width:100%;height:420px;border:1px solid rgba(255,255,255,.08);border-radius:12px;overflow:hidden;background:transparent;';
			container.appendChild(frame);
		} else {
			frame.style.cssText = 'position:fixed;top:0;left:0;width:100%;height:100%;border:0;pointer-events:none;z-index:9999;';
			document.body.appendChild(frame);
		}
		toggle_btn.textContent = '停止特效';
		out.style.display = 'block';
		out.textContent = inNode ? '已载入' : '特效已启动';
	}
	function stopEffect() {
		if (frame) { frame.remove(); frame = null; }
		toggle_btn.textContent = '重新运行';
		out.style.display = 'none';
	}
	toggle_btn.addEventListener('click', function(e) {
		e.stopPropagation();
		if (frame) stopEffect(); else startEffect();
	});
	bar.appendChild(audit_btn);
	bar.appendChild(toggle_btn);
	container.appendChild(bar);
	container.appendChild(pre);
	container.appendChild(out);
	appendNote();
	/* 含前端内容的节点渲染到当前页面时自动执行（无特效模式下跳过） */
	if (!sp_effects_disabled) startEffect();
}

/* 文件节点：base64 → Blob → 触发下载（blob: 下载不受 CSP 限制） */
function renderSpFile(container, sp, appendNote) {
	var bar = document.createElement('div');
	bar.className = 'sp-actions';
	var btn = document.createElement('button');
	btn.className = 'act-btn act-edit';
	btn.textContent = '下载 ' + (sp.name || '文件') + '（' + formatBytes(Math.floor((sp.data || '').length * 3 / 4)) + '）';
	btn.addEventListener('click', function(e) {
		e.stopPropagation();
		try {
			var bin = atob(sp.data);
			var bytes = new Uint8Array(bin.length);
			for (var i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
			var blob = new Blob([bytes], { type: sp.mime || 'application/octet-stream' });
			var a = document.createElement('a');
			a.href = URL.createObjectURL(blob);
			a.download = sp.name || 'download';
			document.body.appendChild(a);
			a.click();
			a.remove();
			setTimeout(function() { URL.revokeObjectURL(a.href); }, 10000);
		} catch (err) {
			alert('文件解码失败：' + err.message);
		}
	});
	bar.appendChild(btn);
	container.appendChild(bar);
	appendNote();
}

/* ===== 样式类节点（style/theme）：CSS 注入 <style> 控制整个界面 =====
 * 生命周期：节点作用域——进入该节点所在层时注入生效，切换/返回节点时随
 * clearAllEffects 移除，界面自动还原；卡片上可逐个停用/启用。
 * 安全：节点拥有者或管理员可发布（后端强制）且经 AI 送审；CSS 无法执行脚本，风险面仅为界面外观。
 * theme 是 style 的克制子集——仅允许覆盖 CSS 变量（:root 块），
 * 变量名/值合法性由后端 validate_sp 强制，前端这里只做兜底过滤。 */

function themeVarsToCss(vars) {
	var lines = [':root {'];
	for (var k in vars) {
		if (!Object.prototype.hasOwnProperty.call(vars, k)) continue;
		if (!/^--[A-Za-z0-9_-]+$/.test(k)) continue;
		var v = String(vars[k]);
		if (/[{}<>]/.test(v)) continue;
		lines.push('  ' + k + ': ' + v + ';');
	}
	lines.push('}');
	return lines.join('\n');
}

/* vars 对象 → 编辑/审计用的行文本（每行 "--var: value;"），与 theme 提交输入格式一致；
 * 非 -- 前缀 key 后端不会接受，这里同口径过滤防止原型链属性混入展示 */
function themeVarsToText(vars) {
	var lines = [];
	for (var k in vars) {
		if (!Object.prototype.hasOwnProperty.call(vars, k)) continue;
		if (k.indexOf('--') !== 0) continue;
		lines.push(k + ': ' + vars[k] + ';');
	}
	return lines.join('\n');
}

/* 将 theme/style 信封注入为 <style> 元素到 <head>（供卡片渲染和祖先继承复用）。
 * 返回创建的 styleEl，cssText 为空时返回 null。 */
function injectSpCss(sp, noteId) {
	var is_theme = (sp._sp === 'theme');
	var cssText = is_theme ? themeVarsToCss(sp.vars || {}) : String(sp.css || '');
	if (!cssText) return null;
	/* 同一 noteId 的旧注入先移除 */
	if (noteId) {
		document.querySelectorAll('.sp-injected-style[data-note-id="' + noteId + '"]')
			.forEach(function(s) { s.remove(); });
	}
	var styleEl = document.createElement('style');
	styleEl.className = 'sp-injected-style';
	if (noteId) styleEl.setAttribute('data-note-id', noteId);
	styleEl.textContent = cssText;
	document.head.appendChild(styleEl);
	return styleEl;
}

function renderSpCss(container, sp, appendNote, item) {
	var is_theme = (sp._sp === 'theme');
	var kindLabel = is_theme ? '主题' : '样式';
	var cssText = is_theme ? themeVarsToCss(sp.vars || {}) : String(sp.css || '');
	var displayText = is_theme ? themeVarsToText(sp.vars || {}) : String(sp.css || '');
	var noteId = (item && item.id != null) ? String(item.id) : null;

	var bar = document.createElement('div');
	bar.className = 'sp-actions';
	var audit_btn = document.createElement('button');
	audit_btn.className = 'act-btn act-edit';
	audit_btn.textContent = '审计源码';
	var toggle_btn = document.createElement('button');
	toggle_btn.className = 'act-btn act-move';
	toggle_btn.textContent = '停用' + kindLabel;
	var pre = document.createElement('pre');
	pre.className = 'sp-code';
	pre.style.display = 'none';
	pre.textContent = displayText;
	var out = document.createElement('div');
	out.className = 'sp-run';
	out.style.display = 'none';
	var styleEl = null;	/* 当前注入的 <style> 实例，null 表示未生效 */

	audit_btn.addEventListener('click', function(e) {
		e.stopPropagation();
		var hidden = (pre.style.display === 'none');
		pre.style.display = hidden ? 'block' : 'none';
		audit_btn.textContent = hidden ? '收起源码' : '审计源码';
	});
	function applyCss() {
		if (styleEl) return;
		styleEl = injectSpCss(sp, noteId);
		if (!styleEl) return;
		toggle_btn.textContent = '停用' + kindLabel;
		out.style.display = 'block';
		out.textContent = kindLabel + '已生效（本层）';
	}
	function stopCss() {
		if (styleEl) { styleEl.remove(); styleEl = null; }
		toggle_btn.textContent = '启用' + kindLabel;
		out.style.display = 'none';
	}
	toggle_btn.addEventListener('click', function(e) {
		e.stopPropagation();
		if (styleEl) stopCss(); else applyCss();
	});
	bar.appendChild(audit_btn);
	bar.appendChild(toggle_btn);
	container.appendChild(bar);
	container.appendChild(pre);
	container.appendChild(out);
	appendNote();
	/* 渲染即生效 */
	applyCss();
}

function formatBytes(n) {
	var units = ['B', 'KB', 'MB', 'GB'];
	var i = 0;
	while (n >= 1024 && i < units.length - 1) { n /= 1024; i++; }
	return n.toFixed(1) + units[i];
}

/* ===== 祖先主题/样式继承 =====
 * 当前节点不存在 theme/style 子节点时，沿 stack 向根搜索最近祖先的对应节点，
 * 自动注入其 CSS。已存在的不重复搜索（hasTheme/hasStyle 分别控制）。
 * 用静默 fetch 替代 getNode（不弹 alert、不抛异常），仅日志输出。 */
var _inherit_gen = 0;
function inheritThemeStyle() {
	var hasTheme = false, hasStyle = false;
	for (var i = 0; i < current_children.length; i++) {
		var sp = parseSp(current_children[i].content);
		if (!sp) continue;
		if (sp._sp === 'theme') hasTheme = true;
		if (sp._sp === 'style') hasStyle = true;
	}
	if (hasTheme && hasStyle) return;

	console.log('[继承] stack=', JSON.stringify(stack),
		'current_children.length=', current_children.length,
		'hasTheme=', hasTheme, 'hasStyle=', hasStyle);
	var gen = ++_inherit_gen;
	(async function() {
		for (var i = stack.length - 1; i >= 0; i--) {
			if (hasTheme && hasStyle) break;
			if (gen !== _inherit_gen) return;
			var pid = (i === 0) ? 0 : stack[i - 1];
			console.log('[继承] 查询 pid=', pid, '(stack[' + i + '] 的父节点)');
			try {
				var res = await fetch('/get_notes', {
					method: 'POST',
					headers: { 'Content-Type': 'application/json' },
					body: JSON.stringify({ pid: pid, noread: true })
				});
				if (gen !== _inherit_gen) return;
				if (!res.ok) { console.log('[继承] pid=', pid, 'HTTP', res.status); continue; }
				var data = await res.json();
				if (gen !== _inherit_gen) return;
				if (!Array.isArray(data)) { console.log('[继承] pid=', pid, '非数组'); continue; }
				console.log('[继承] pid=', pid, '返回', data.length, '个子节点');
				for (var j = 0; j < data.length; j++) {
					var sp = parseSp(data[j].content);
					if (!sp) continue;
					if (!hasTheme && sp._sp === 'theme') {
						console.log('[继承] 找到主题:', data[j].title, 'id=', data[j].id);
						injectSpCss(sp, 'inh-' + data[j].id);
						hasTheme = true;
					}
					if (!hasStyle && sp._sp === 'style') {
						console.log('[继承] 找到样式:', data[j].title, 'id=', data[j].id);
						injectSpCss(sp, 'inh-' + data[j].id);
						hasStyle = true;
					}
				}
			} catch (e) {
				console.error('[继承] 异常 pid=', pid, e);
			}
		}
		console.log('[继承] 完成 hasTheme=', hasTheme, 'hasStyle=', hasStyle);
	})();
}

/* by qing — 互动统计图标：inline SVG，stroke=currentColor 自动跟随主题色 */
const ICON_EYE = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg>';
const ICON_HEART = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M20.84 4.61a5.5 5.5 0 0 0-7.78 0L12 5.67l-1.06-1.06a5.5 5.5 0 0 0-7.78 7.78l1.06 1.06L12 21.23l7.78-7.78 1.06-1.06a5.5 5.5 0 0 0 0-7.78z"/></svg>';
const ICON_SHARE = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="18" cy="5" r="3"/><circle cx="6" cy="12" r="3"/><circle cx="18" cy="19" r="3"/><line x1="8.59" y1="13.51" x2="15.42" y2="17.49"/><line x1="15.41" y1="6.51" x2="8.59" y2="10.49"/></svg>';
const ICON_EDIT = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17 3a2.828 2.828 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5L17 3z"/></svg>';
const ICON_TRASH = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>';
const ICON_MOVE = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="5 9 2 12 5 15"/><polyline points="9 5 12 2 15 5"/><polyline points="15 19 12 22 9 19"/><polyline points="19 9 22 12 19 15"/><line x1="2" y1="12" x2="22" y2="12"/><line x1="12" y1="2" x2="12" y2="22"/></svg>';

/* by qing — 生成一个统计项：图标 + 数字，count 缺省显示 0 */
function makeStatItem(iconSvg, count) {
	let span = document.createElement('span');
	span.className = "stat-item";
	span.innerHTML = iconSvg;	// 上方固定常量字符串，不含用户输入
	let num = document.createElement('span');
	num.className = "stat-num";
	num.textContent = (count == null) ? "0" : String(count);
	span.appendChild(num);
	return span;
}

/* ---- by qing ---- */
const stack = [];	/* 用于存储当前节点的路径，也就是经过的每一个节点的id， 否则就回不去上一层*/
/* 与 stack 平行的权限栈：进入每个节点时记录其 can_operate（后端按 owner/is_admin 判定，
 * 与 delete_note 鉴权口径一致），用于"删除当前节点"按钮显隐；入栈/出栈必须与 stack 同步 */
const stack_operate = [];
var article_title = document.getElementById('article-title');
var article_content = document.getElementById('article-content');
var input_note_title = document.getElementById('input-note-title');
var input_note_content = document.getElementById('input-note-content');
var input_note_submit = document.getElementById('input-note-submit');
var input_note_clear = document.getElementById('input-note-clear');
var recommend_list = document.getElementById('recommend-list');
var delete_note = document.getElementById('delete-note');
var minimap = document.getElementById("minimap");
var movebar = document.getElementById("movebar");
/* 特殊节点面板（管理员专属，loadUserPanel 判定后显示） */
var sp_panel = document.getElementById('sp-panel');
var sp_type = document.getElementById('sp-type');
var sp_file = document.getElementById('sp-file');
var sp_file_tip = document.getElementById('sp-file-tip');
var sp_edit_original = null;	/* 编辑文件节点时的原信封字符串：未选新文件则原样提交 */
/* 搜索框：当前层卡片标题/内容关键词过滤 */
var searchtext = document.getElementById('searchtext');
var searchsubmit = document.getElementById('searchsubmit');
var current_children = [];		/* 当前层子节点列表（load_notes 时更新，搜索据此过滤） */
/* 面板默认展开的最小视口宽度（px）：窄屏下面板展开会遮挡内容，默认折叠；宽屏默认展开 */
var PANEL_EXPAND_MIN_WIDTH = 768;
var is_expand = window.innerWidth >= PANEL_EXPAND_MIN_WIDTH;
var editing_id = null;	/* 编辑模式：正在编辑的节点 id，null 表示当前是新增模式 */
var moving_node = null;	/* 移动模式：待移动的节点 {id, pid, title, content}，null 表示未在移动 */

/* ===== 输入框自动高度调整 ===== */
/* 中文输入法 composition 期间每个拼音字母都会触发 input；若每次都做塌陷测量，
 * 页面总高度反复变化，滚到页面底部输入时 scrollTop 被浏览器钳制又回弹，
 * 表现为页面位置反复横跳。故 composition 中跳过，compositionend 后统一调整一次。 */
var _content_composing = false;
function autoResizeTextarea(el) {
	var doc = document.scrollingElement || document.documentElement;
	var prevTop = doc.scrollTop;
	el.style.height = 'auto';
	el.style.height = Math.min(el.scrollHeight, 400) + 'px';
	el.style.overflowY = el.scrollHeight > 400 ? 'auto' : 'hidden';
	/* 塌陷测量会触发整页同步布局：若页面瞬时变矮导致滚动位置被钳制，恢复原位 */
	if (doc.scrollTop < prevTop) doc.scrollTop = prevTop;
}
input_note_content.addEventListener('compositionstart', function() { _content_composing = true; });
input_note_content.addEventListener('compositionend', function() {
	_content_composing = false;
	autoResizeTextarea(input_note_content);
});
input_note_content.addEventListener('input', function(e) {
	if (_content_composing || (e && e.isComposing)) return;
	autoResizeTextarea(input_note_content);
});
function resetTextareaHeight() {
	input_note_content.style.height = '120px';
	input_note_content.style.overflowY = 'hidden';
}
autoResizeTextarea(input_note_content);	/* 初始化高度 */

/* ===== 特殊节点表单：类型切换控制输入区形态 ===== */
sp_type.addEventListener('change', function() {
	var t = sp_type.value;
	var is_file = (t === 'file');
	sp_file.style.display = is_file ? '' : 'none';
	input_note_content.style.display = is_file ? 'none' : '';
	sp_file_tip.textContent = '';
	sp_file_tip.className = is_file ? 'tip-yellow' : '';
	if (!is_file) {
		sp_file.value = '';
		sp_edit_original = null;
	}
	if (t === 'js') input_note_content.placeholder = '请输入完整前端代码（可含 HTML/CSS/JS）';
	else if (t === 'style') input_note_content.placeholder = '请输入 CSS（如 .card { border-radius: 16px }），发布后全界面生效';
	else if (t === 'theme') input_note_content.placeholder = '每行一个变量，如 --accent: #e8853a';
	else input_note_content.placeholder = '';
});

function readFileAsDataURL(f) {
	return new Promise(function(resolve, reject) {
		var r = new FileReader();
		r.onload = function() { resolve(r.result); };
		r.onerror = function() { reject(new Error('文件读取失败')); };
		r.readAsDataURL(f);
	});
}

/* 解析主题变量输入：每行一条 "--变量名: 值"（末尾分号可选），返回 vars 对象；非法返回 null（已弹提示） */
function parseThemeVars(text) {
	var vars = {};
	var lines = text.split('\n');
	for (var i = 0; i < lines.length; i++) {
		var line = lines[i].trim();
		if (!line) continue;
		if (line.indexOf('--') !== 0) { alert('第 ' + (i + 1) + ' 行不是 CSS 变量（须以 -- 开头）：' + line); return null; }
		var idx = line.indexOf(':');
		if (idx <= 0) { alert('第 ' + (i + 1) + ' 行缺少冒号：' + line); return null; }
		var k = line.substring(0, idx).trim();
		var v = line.substring(idx + 1).trim().replace(/;+$/, '');
		if (!/^--[A-Za-z0-9_-]+$/.test(k)) { alert('非法变量名：' + k); return null; }
		if (!v) { alert('变量缺少值：' + k); return null; }
		if (/[{}<>]/.test(v)) { alert('变量值含非法字符（{}<>）：' + k); return null; }
		vars[k] = v;
	}
	if (Object.keys(vars).length === 0) { alert('请至少输入一个 CSS 变量，如 --accent: #e8853a'); return null; }
	return vars;
}

/* by qing — 构造提交内容：普通节点返回原文本，特殊节点返回信封 JSON 字符串。
 * 校验失败返回 null（已弹提示）；未登录永远按普通文本提交。 */
async function buildSubmitContent() {
	var t = (current_user && current_user.login) ? sp_type.value : 'text';
	if (t === 'text') return input_note_content.value;
	if (t === 'js') {
		var code = input_note_content.value;
		if (!code.trim()) { alert('请输入前端代码'); return null; }
		return JSON.stringify({ _sp: 'js', code: code });
	}
	if (t === 'style') {
		var css = input_note_content.value;
		if (!css.trim()) { alert('请输入 CSS 样式'); return null; }
		return JSON.stringify({ _sp: 'style', css: css });
	}
	if (t === 'theme') {
		var vars = parseThemeVars(input_note_content.value);
		if (!vars) return null;
		return JSON.stringify({ _sp: 'theme', vars: vars });
	}
	/* file：优先使用新选择的文件；编辑模式下未选新文件则保留原信封 */
	if (sp_file.files.length === 0) {
		if (sp_edit_original) return sp_edit_original;
		alert('请选择要上传的文件');
		return null;
	}
	var f = sp_file.files[0];
	if (f.size > SP_MAX_FILE_BYTES) {
		alert('文件过大（上限约 2.5MB）：' + f.name);
		return null;
	}
	var dataUrl = await readFileAsDataURL(f);
	return JSON.stringify({ _sp: 'file', name: f.name, mime: f.type || 'application/octet-stream', data: dataUrl.split(',')[1] });
}

input_note_submit.addEventListener('click', function(event) {
	if (input_note_submit.disabled) return;	// 防重复点击
	var title = input_note_title.value
	var pid = (stack.length == 0) ? 0 : stack[stack.length - 1];
	//alert(stack.length)
	//alert(pid)
	// 发布期间同时禁用重置/删除，避免误触干扰
	input_note_submit.disabled = true;
	input_note_clear.disabled = true;
	delete_note.disabled = true;
	// 编辑模式下保存修改，否则新增节点；特殊节点先异步构造信封（可能涉及读文件）
	buildSubmitContent()
		.then(function(content) {
			if (content == null) return null;	// 校验失败，已弹提示
			return (editing_id != null) ? updateNode(editing_id, pid, title, content) : addNode(pid, title, content);
		})
		.catch(function(err) {
			alert(err.message || '内容处理失败');
			console.error('构造提交内容异常：', err);
		})
		.finally(() => {
			input_note_submit.disabled = false;
			input_note_clear.disabled = false;
			delete_note.disabled = false;
		});
})

input_note_clear.addEventListener('click', function(event) {
	input_note_title.value = "";
	input_note_content.value = "";
	cancelEdit();	// 重置时同时退出编辑模式
})

delete_note.addEventListener('click', (event) => {
	if (delete_note.disabled) return;	// 防重复点击
	var id = (stack.length == 0) ? 0 : stack[stack.length - 1];
	if (id == 0) {
		alert('无法删除根节点');
		console.log("节点未删除");
	}
	else {
		delete_note.disabled = true;
		// confirm 取消时 deleteNode 直接返回 undefined，统一用 Promise.resolve 兜底恢复按钮
		Promise.resolve(deleteNode(id))
			.finally(() => { delete_note.disabled = false; });
	}
})

/* by qing — 点击卡片"编辑"：把节点标题/内容载入下方表单，进入编辑模式。
 * 特殊节点：js/style/theme 回填对应文本到文本域；file 记住原信封（未选新文件则原样提交） */
function startEdit(item) {
	editing_id = item.id;
	input_note_title.value = item.title;
	var sp = parseSp(item.content);
	sp_edit_original = null;
	if (sp && sp._sp === 'js') {
		sp_type.value = 'js';
		sp_type.dispatchEvent(new Event('change'));
		input_note_content.value = sp.code || '';
	} else if (sp && sp._sp === 'style') {
		sp_type.value = 'style';
		sp_type.dispatchEvent(new Event('change'));
		input_note_content.value = sp.css || '';
	} else if (sp && sp._sp === 'theme') {
		sp_type.value = 'theme';
		sp_type.dispatchEvent(new Event('change'));
		input_note_content.value = themeVarsToText(sp.vars || {});
	} else if (sp) {
		sp_type.value = sp._sp;
		sp_type.dispatchEvent(new Event('change'));
		sp_edit_original = item.content;
		sp_file_tip.textContent = '不选择新文件则保持原文件';
		input_note_content.value = '';
	} else {
		sp_type.value = 'text';
		sp_type.dispatchEvent(new Event('change'));
		input_note_content.value = item.content;
	}
	input_note_submit.textContent = "保存修改";
	autoResizeTextarea(input_note_content);	/* 根据载入内容调整高度 */
	input_note_title.scrollIntoView({ behavior: 'smooth', block: 'center' });
	input_note_title.focus();
}

/* by qing — 退出编辑模式，表单恢复为新增状态 */
function cancelEdit() {
	editing_id = null;
	sp_edit_original = null;
	sp_file.value = '';
	sp_file_tip.textContent = '';
	if (sp_type.value !== 'text') {
		sp_type.value = 'text';
		sp_type.dispatchEvent(new Event('change'));
	}
	input_note_submit.textContent = "提交";
	resetTextareaHeight();	/* 恢复初始高度 */
}

/* 导航守卫：表单有未提交内容时先 confirm；确认放弃则清空表单放行，否则本次导航不执行。
 * 必须在调用方修改 stack 之前调用（点卡片 push 前 / 返回上一层 pop 前），
 * 否则用户选择"不放弃"时 stack 已被改动，后续导航与新增节点的 pid 全部错位 */
function confirmLeaveIfDirty() {
	if (input_note_title.value == "" && input_note_content.value == "") return true;
	if (confirm("是否放弃当前输入的内容？") == false) return false;
	input_note_title.value = "";
	input_note_content.value = "";
	cancelEdit();
	return true;
}

/* by qing — "删除当前节点"按钮显隐：仅当前节点的 can_operate 为真（本人或管理员，
 * 后端 get_notes 判定，与 delete_note 鉴权口径一致）时显示；
 * 根节点（无主人）/未登录/非本人节点一律隐藏。
 * 所有改变 stack 的导航（进入/返回/删除当前节点）最终都会走到 load_notes，在此统一刷新 */
function updateDeleteBtnVisibility() {
	if (stack.length == 0) {
		delete_note.style.display = 'none';
		return;
	}
	delete_note.style.display = (stack_operate[stack_operate.length - 1] === true) ? '' : 'none';
}

function load_notes(main_node) {

	/* 这个不能设置为空的 */
	//article_title.textContent = "";
	//article_content.textContent = "";

	if (input_note_title.value != "" || input_note_content.value != "") {
		if (confirm("是否放弃当前输入的内容？") == true) {
			input_note_title.value = "";
			input_note_content.value = "";
			cancelEdit();
		} else {
			if (stack.length != 0) {
				return;
			}
		}
	} else {
			input_note_title.value = "";
			input_note_content.value = "";
			cancelEdit();
	}

	/* 切换到新节点：清空搜索关键词，清除上一节点特效，再渲染当前层 */
	current_children = Array.isArray(main_node) ? main_node : [];
	if (searchtext) searchtext.value = "";
	clearAllEffects();
	renderCards(current_children);
	inheritThemeStyle();
	updateDeleteBtnVisibility();
	/* 层级切换后回到页面顶部，避免滚动条停留在上一层的深度 */
	window.scrollTo(0, 0);
};

/* 对 __ 隐藏节点应用特效（注入 CSS / 运行 js），卡片不渲染 */
function applyHiddenNodeEffects(item) {
	var sp = parseSp(item.content);
	if (!sp) return;
	var noteId = (item.id != null) ? 'hid-' + String(item.id) : null;
	if (sp._sp === 'style' || sp._sp === 'theme') {
		injectSpCss(sp, noteId);
	} else if (sp._sp === 'js' && !sp_effects_disabled) {
		/* 仅全屏模式（非 in-node）：无卡片容器，in-node 无法合理放置，忽略 */
		if (/<!--\s*in-node\s*-->/.test(sp.code || '')) return;
		var frame = document.createElement('iframe');
		frame.className = 'sp-effect-frame';
		frame.setAttribute('sandbox', 'allow-scripts');
		frame.srcdoc = sp.code;
		frame.style.cssText = 'position:fixed;top:0;left:0;width:100%;height:100%;border:0;pointer-events:none;z-index:9999;';
		document.body.appendChild(frame);
	}
}

/* 把卡片列表渲染到 recommend_list（从 load_notes/搜索过滤复用） */
function renderCards(list) {
	recommend_list.replaceChildren();
	for (let i in list) {
		let item = list[i]
		if (item.title && typeof item.title === 'string' && item.title.indexOf('__') === 0) {
			if (!__show_hidden) { applyHiddenNodeEffects(item); continue; }
		}
		console.log(item)
		let card = document.createElement('div');
		card.className = "card";
		card.__item = item;		/* 供搜索过滤时读取原始数据 */
		let ttl = document.createElement('center');
		let ttl1 =document.createElement('h3');
		ttl1.textContent = item.title;
		card.id = item.title;
		/* 仅标题可点击进入子节点（内容/统计/操作区不触发跳转） */
		ttl1.addEventListener('click', function(event) {
			if (!confirmLeaveIfDirty()) return;	/* 未放弃输入则不跳转，stack 保持原样 */
			var id = Number(item.id)
			stack.push(id)
			stack_operate.push(item.can_operate === true)
			getNode(id).then(function(r) { load_notes(r); loadMap(r); })
		});
		ttl.appendChild(ttl1);
		card.appendChild(ttl);
		let wrt_div = document.createElement('p');
		// 发帖用户：头像圆点（取昵称首字符）+ 昵称，样式见 theme.css 的 Owner Chip 段
		wrt_div.className = "card-owner";
		let owner_text = (item.owner == null || item.owner === "") ? "匿名" : String(item.owner);
		let owner_avatar = document.createElement('span');
		owner_avatar.className = "owner-avatar";
		owner_avatar.textContent = owner_text.trim().charAt(0).toUpperCase() || "?";
		let owner_name = document.createElement('span');
		owner_name.className = "owner-name";
		owner_name.textContent = owner_text;
		wrt_div.appendChild(owner_avatar);
		wrt_div.appendChild(owner_name);
		// 最后编辑时间（新增时 created_at = updated_at，仅更新时 updated_at 变化）
		let time_span = document.createElement('span');
		time_span.className = "owner-time";
		if (item.updated_at) {
			time_span.textContent = ' · ' + item.updated_at.substring(0, 16);
		}
		wrt_div.appendChild(time_span);
		// 互动统计：眼睛=观看数（后端 views 列）、爱心=点赞、转发=子节点数（后端 child_count）。
		// views / child_count 由 dnfs2 get_notes 返回；like_count 后端暂无字段，缺省显示 0。
		let sts_div = document.createElement('div');
		sts_div.className = "card-stats";
		sts_div.appendChild(makeStatItem(ICON_EYE, item.views));
		sts_div.appendChild(makeStatItem(ICON_HEART, item.like_count));
		sts_div.appendChild(makeStatItem(ICON_SHARE, item.child_count));
		// 卡片操作按钮：编辑 / 移动 / 删除（stopPropagation 防止触发卡片自身的点入跳转）
		// 按钮组整体按 can_operate 显隐：操作权由后端 get_notes 按 owner / is_admin 判定，
		// 与 delete_note / update_note 鉴权口径一致（移动本质也是 update_note）；
		// 未登录或无权时不渲染整个按钮组
		ttl.appendChild(wrt_div);
		ttl.appendChild(sts_div);
		let ctt = document.createElement('p');
		renderContent(ctt, item);
		card.appendChild(ctt)
		if (item.can_operate === true) {
			let act_div = document.createElement('div');
			act_div.className = "card-actions";
			let edit_btn = document.createElement('button');
			edit_btn.className = "act-btn act-edit";
			edit_btn.innerHTML = ICON_EDIT + "编辑";
			edit_btn.addEventListener('click', function(event) {
				event.stopPropagation();
				startEdit(item);
			});
			let move_btn = document.createElement('button');
			move_btn.className = "act-btn act-move";
			move_btn.innerHTML = ICON_MOVE + "移动";
			move_btn.addEventListener('click', function(event) {
				event.stopPropagation();
				startMove(item);
			});
			let del_btn = document.createElement('button');
			del_btn.className = "act-btn act-del";
			del_btn.innerHTML = ICON_TRASH + "删除";
			del_btn.addEventListener('click', function(event) {
				event.stopPropagation();
				deleteChild(item.id);
			});
			act_div.appendChild(edit_btn);
			act_div.appendChild(move_btn);
			act_div.appendChild(del_btn);
			card.appendChild(act_div);
		}
		recommend_list.appendChild(card)
		recommend_list.appendChild(document.createElement('br'))
	}
};

/* ===== 当前层搜索：从 item 中提取可搜索文本（title + 内容文本；信封类排除 base64 数据） ===== */
function itemSearchText(item) {
	var parts = [];
	if (item.title) parts.push(item.title);
	if (item.owner) parts.push(item.owner);
	var c = item.content;
	if (typeof c === 'string') {
		var sp = parseSp(c);
		if (!sp) {
			parts.push(c);
		} else if (sp._sp === 'js') {
			if (sp.code) parts.push(sp.code);
			if (sp.note) parts.push(sp.note);
		} else if (sp._sp === 'style') {
			if (sp.css) parts.push(sp.css);
			if (sp.note) parts.push(sp.note);
		} else if (sp._sp === 'theme') {
			parts.push(themeVarsToText(sp.vars || {}));
			if (sp.note) parts.push(sp.note);
		} else {	/* file：不把 base64 数据纳入搜索 */
			if (sp.name) parts.push(sp.name);
			if (sp.note) parts.push(sp.note);
		}
	}
	return parts.join(' ').toLowerCase();
}

function applySearch() {
	var kw = (searchtext && searchtext.value || '').trim().toLowerCase();
	var cards = recommend_list.querySelectorAll('.card');
	var anyMatch = false;
	for (var i = 0; i < cards.length; i++) {
		var card = cards[i];
		var item = card.__item;
		var matched = !kw || (item && itemSearchText(item).indexOf(kw) !== -1);
		card.style.display = matched ? '' : 'none';
		if (matched) anyMatch = true;
	}
	/* 无匹配结果提示（避免空列表看不出是已过滤） */
	var noMatchTip = document.getElementById('search-no-match');
	if (kw && !anyMatch) {
		if (!noMatchTip) {
			noMatchTip = document.createElement('div');
			noMatchTip.id = 'search-no-match';
			noMatchTip.className = 'card';
			noMatchTip.style.textAlign = 'center';
			noMatchTip.style.color = 'var(--fg-secondary)';
			noMatchTip.style.cursor = 'default';
			recommend_list.appendChild(noMatchTip);
		}
		noMatchTip.textContent = '未找到匹配「' + searchtext.value + '」的节点';
		noMatchTip.style.display = '';
	} else if (noMatchTip) {
		noMatchTip.style.display = 'none';
	}
}

function loadMap(children_json) {
	let need_return = false;
    minimap.replaceChildren();
	var fold_div = document.createElement("div");
	fold_div.className = "tag fold-btn";
	if (is_expand == true) {
		fold_div.textContent = "v 折叠";
		minimap.appendChild(fold_div);
	} else {
		fold_div.textContent = "> 展开";
		minimap.appendChild(fold_div);
		need_return = true;
	}
	fold_div.addEventListener("click", () => {
		if (is_expand == true) {
			is_expand = false;
			getNode(stack.length == 0 ? 0 : stack[stack.length - 1]).then(loadMap);
		} else {
			is_expand = true;
			getNode(stack.length == 0 ? 0 : stack[stack.length - 1]).then(loadMap);
		}
	});
	if (need_return == true) {
		return;
	}
    if (stack.length > 0) {
        let back = document.createElement("div");
        back.className = "tag back";
        back.textContent = "← 返回上一层";
        back.addEventListener("click", () => {
            if (!confirmLeaveIfDirty()) return;	/* 未放弃输入则不返回，stack 保持原样 */
            stack.pop();
            stack_operate.pop();
            /* 栈空（回到根）时取 0：原判断 stack.length >= 0 恒真，空栈取 stack[-1] 得 undefined */
            var id = (stack.length > 0) ? stack[stack.length - 1] : 0;
            getNode(id).then(function(r) { load_notes(r); loadMap(r); });
        });
        minimap.appendChild(back);
    }
    for (let i in children_json) {
        let item = children_json[i];
        let title = item.title;
        if (title && typeof title === 'string' && title.indexOf('__') === 0 && !__show_hidden) continue;
        let tag = document.createElement("div");
        tag.className = "tag";
        tag.textContent = title;
        tag.addEventListener("click", () => {
            window.location.href = `#${title}`;
            console.log(`跳转到${title}`);
        })
        minimap.appendChild(tag);
    }
}

/* by qing — 用户信息面板：左上角，默认折叠；展示用户名 / 角色 / 退出登录。
 * 身份来自 dnfs /whoami（HttpOnly Cookie 模式下 JS 无法读 Token，由后端解 payload 返回） */
var userpanel = document.getElementById("userpanel");
var userpanel_expand = window.innerWidth >= PANEL_EXPAND_MIN_WIDTH;	// 与 minimap 同策略：窄屏默认折叠
var current_user = null;

async function loadUserPanel() {
	try {
		const res = await fetch('/whoami', {
			method: 'POST',
			headers: {'Content-Type': 'application/json'},
			body: '{}'
		});
		if (!res.ok) return;
		current_user = await res.json();
		/* 登录用户显示特殊节点面板（发布权限由后端按"节点拥有者或管理员"强制，
		 * 非拥有者在他人节点下提交特殊节点会被 403 拒绝并提示） */
		if (current_user.login) {
			sp_panel.style.display = '';
		}
		renderUserPanel();
	} catch (e) {
		console.error('获取用户信息失败：', e);
	}
}

function renderUserPanel() {
	if (current_user == null) return;
	userpanel.replaceChildren();
	let fold = document.createElement("div");
	if (userpanel_expand) {
		fold.className = "tag fold-btn";
		fold.textContent = "v 折叠";
	} else {
		fold.className = "user-avatar-fold";
		fold.textContent = current_user.login ? (current_user.user || "?").trim().charAt(0).toUpperCase() : "?";
	}
	fold.addEventListener("click", () => {
		userpanel_expand = !userpanel_expand;
		renderUserPanel();
	});
	userpanel.appendChild(fold);
	if (!userpanel_expand || !current_user.login) return;

	let back_to_index = document.createElement("div");
	back_to_index.className = "tag";
	back_to_index.textContent = "回到主页";
	back_to_index.addEventListener("click", () => {
		window.location.href = "/";
	})
	userpanel.appendChild(back_to_index);

	let name_tag = document.createElement("div");
	name_tag.className = "tag no-hover";
	name_tag.textContent = current_user.user;
	userpanel.appendChild(name_tag);

	let role_tag = document.createElement("div");
	role_tag.className = "tag no-hover";
	role_tag.textContent = current_user.is_admin ? "管理员" : "普通用户";
	userpanel.appendChild(role_tag);

	/* 无特效模式开关（所有用户）：关闭后特效节点不再自动执行 */
	let effect_btn = document.createElement("div");
	effect_btn.className = "tag";
	effect_btn.textContent = sp_effects_disabled ? "无特效模式" : "特效模式";
	effect_btn.addEventListener("click", () => {
		sp_effects_disabled = !sp_effects_disabled;
		try { localStorage.setItem('sp_effects_disabled', sp_effects_disabled ? '1' : '0'); } catch (e) {}
		effect_btn.textContent = sp_effects_disabled ? "无特效模式" : "特效模式";
		/* 切换时清除已有特效并按新状态重新渲染当前层卡片 */
		clearAllEffects();
		renderCards(current_children);
	});
	userpanel.appendChild(effect_btn);

	/* __ 系统节点显隐开关（所有用户） */
	let hidden_btn = document.createElement("div");
	hidden_btn.className = "tag";
	hidden_btn.textContent = __show_hidden ? "隐藏系统节点" : "显示系统节点";
	hidden_btn.addEventListener("click", () => {
		__show_hidden = !__show_hidden;
		try { localStorage.setItem('dnfs_show_hidden', __show_hidden ? '1' : '0'); } catch (e) {}
		/* 切换时重新渲染当前层卡片和 minimap */
		clearAllEffects();
		renderCards(current_children);
		var cur = (stack.length == 0) ? 0 : stack[stack.length - 1];
		getNode(cur).then(function(r) { loadMap(r); });
		renderUserPanel();
	});
	userpanel.appendChild(hidden_btn);

	/* 管理员专属入口：运维机器人 / 进程管理器 */
	if (current_user.is_admin) {
		let ops_btn = document.createElement("div");
		ops_btn.className = "tag";
		ops_btn.textContent = "运维机器人";
		ops_btn.addEventListener("click", () => { window.location.href = "/aibox.html"; });
		userpanel.appendChild(ops_btn);

		let mgmt_btn = document.createElement("div");
		mgmt_btn.className = "tag";
		mgmt_btn.textContent = "进程管理器";
		mgmt_btn.addEventListener("click", () => { window.location.href = "/mgmt/mgmt.html"; });
		userpanel.appendChild(mgmt_btn);
	}

	let logout_tag = document.createElement("div");
	logout_tag.className = "tag back";
	logout_tag.textContent = "退出登录";
	logout_tag.addEventListener("click", async () => {
		try { await fetch('/logout', {method: 'POST'}); } catch (e) {}
		location.href = '/login.html';
	});
	userpanel.appendChild(logout_tag);
}

getNode(0).then(function(r) { load_notes(r); loadMap(r); })
loadUserPanel();

/* ===== 搜索框事件绑定：实时过滤当前层卡片；ESC 清空；回车不提交 ===== */
if (searchtext) {
	searchtext.addEventListener('input', applySearch);
	searchtext.addEventListener('keydown', function(e) {
		if (e.key === 'Enter') { e.preventDefault(); applySearch(); }
		if (e.key === 'Escape') { searchtext.value = ''; applySearch(); }
	});
}
if (searchsubmit) {
	searchsubmit.addEventListener('click', function(e) { e.preventDefault(); applySearch(); });
}
