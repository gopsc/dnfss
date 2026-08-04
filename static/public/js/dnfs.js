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
			throw new Error(`添加节点失败（HTTP ${res.status}）`);
		}

		input_note_title.value = "";
		input_note_content.value = "";
		cancelEdit();	/* 退出编辑模式并重置高度 */
		getNode(stack[stack.length - 1]).then(load_notes);
		getNode(stack[stack.length - 1]).then(loadMap)

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
		getNode(stack[stack.length - 1]).then(load_notes);
		getNode(stack[stack.length - 1]).then(loadMap)

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
		getNode(cur).then(load_notes);
		getNode(cur).then(loadMap);

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
		getNode(cur).then(load_notes);
		getNode(cur).then(loadMap);

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
		getNode(cur).then(load_notes);
		getNode(cur).then(loadMap);

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

/* ===== by qing — 特殊节点（管理员专属）=====
 * 信封：content 为 {"_sp":"link|js|image|file", ...} JSON，普通内容走 Markdown。
 * 发布权限由后端 dnfs 强制（is_admin），前端仅做展示与表单切换。 */

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
	if (obj && typeof obj === 'object' && ['link', 'js', 'image', 'file'].indexOf(obj._sp) !== -1) return obj;
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
	if (sp._sp === 'link') renderSpLink(container, sp, appendNote);
	else if (sp._sp === 'js') renderSpJs(container, sp, appendNote);
	else if (sp._sp === 'image') renderSpImage(container, sp, appendNote);
	else if (sp._sp === 'file') renderSpFile(container, sp, appendNote);
}

/* 链接节点：服务端代理拉取的 favicon + 可点击链接（卡片内交互需 stopPropagation） */
function renderSpLink(container, sp, appendNote) {
	var box = document.createElement('div');
	box.className = 'sp-link';
	var icon = document.createElement('img');
	icon.className = 'sp-favicon';
	icon.alt = '';
	icon.src = '/api/favicon?url=' + encodeURIComponent(sp.url);
	icon.addEventListener('error', function() { icon.style.display = 'none'; });
	var anchor = document.createElement('a');
	anchor.className = 'sp-anchor';
	anchor.href = sp.url;
	anchor.target = '_blank';
	anchor.rel = 'noopener noreferrer';
	anchor.textContent = sp.url;
	anchor.addEventListener('click', function(e) { e.stopPropagation(); });
	box.appendChild(icon);
	box.appendChild(anchor);
	container.appendChild(box);
	appendNote();
}

/* 前端代码节点：审计（展开源码，textContent 防注入）+ 运行（注入当前页面执行，支持完整 HTML/CSS/JS）。
 * 不使用 new Function/eval（CSP 未开 unsafe-eval）；解析完整 HTML 文档后注入当前页面：
 *   - <style> 注入到页面 head
 *   - <script> 在当前页面全局作用域执行（可操作整页 DOM 加特效）
 *   - <body> 内容渲染到节点下方容器
 * console.log/error/warn 输出捕获到下方输出区。仅管理员可发布（后端强制）。 */
function renderSpJs(container, sp, appendNote) {
	var bar = document.createElement('div');
	bar.className = 'sp-actions';
	var audit_btn = document.createElement('button');
	audit_btn.className = 'act-btn act-edit';
	audit_btn.textContent = '审计源码';
	var run_btn = document.createElement('button');
	run_btn.className = 'act-btn act-move';
	run_btn.textContent = '运行';
	var pre = document.createElement('pre');
	pre.className = 'sp-code';
	pre.style.display = 'none';
	pre.textContent = sp.code;
	var out = document.createElement('div');
	out.className = 'sp-run';
	out.style.display = 'none';
	audit_btn.addEventListener('click', function(e) {
		e.stopPropagation();
		var hidden = (pre.style.display === 'none');
		pre.style.display = hidden ? 'block' : 'none';
		audit_btn.textContent = hidden ? '收起源码' : '审计源码';
	});
	run_btn.addEventListener('click', function(e) {
		e.stopPropagation();
		out.style.display = out.style.display === 'none' ? 'block' : 'none';
		if (out.dataset.executed) return;	// 已执行过，仅切换显示
		out.dataset.executed = '1';
		/* 首次注入 console 捕获：保留原 console 行为，同时备份到 __spLines（避免影响页面其他脚本） */
		if (!window.__spLines) {
			window.__spLines = [];
			var _orig = window.console;
			window.console.log = function() {
				window.__spLines.push(Array.prototype.slice.call(arguments).join(' '));
				_orig.log.apply(_orig, arguments);
			};
			window.console.error = function() {
				window.__spLines.push('Error: ' + Array.prototype.slice.call(arguments).join(' '));
				_orig.error.apply(_orig, arguments);
			};
			window.console.warn = function() {
				window.__spLines.push('Warn: ' + Array.prototype.slice.call(arguments).join(' '));
				_orig.warn.apply(_orig, arguments);
			};
			window.addEventListener('error', function(ev) {
				window.__spLines.push('Error: ' + ev.message);
			});
		}
		/* 解析完整 HTML 文档 */
		var doc;
		try {
			doc = new DOMParser().parseFromString(sp.code, 'text/html');
		} catch (err) {
			window.__spLines.push('Error: 解析 HTML 失败: ' + (err.message || err));
			out.textContent = window.__spLines.join('\n');
			return;
		}
		/* 注入 <style> 到页面 head */
		Array.prototype.forEach.call(doc.querySelectorAll('style'), function(st) {
			var s = document.createElement('style');
			s.textContent = st.textContent;
			document.head.appendChild(s);
		});
		/* 注入 <script>：包一层 IIFE 隔离变量作用域，避免污染页面全局（如覆盖 getNode/stack 等同名
		 * 变量导致卡片点击失效）；函数体内仍可访问 document/window 对整页施加特效。 */
		Array.prototype.forEach.call(doc.querySelectorAll('script'), function(sc) {
			var s = document.createElement('script');
			if (sc.getAttribute('src')) {
				s.src = sc.getAttribute('src');
			} else {
				s.textContent = '(function(){\n' + sc.textContent + '\n})();';
			}
			document.head.appendChild(s);
		});
		/* 渲染 <body> 内容到节点下方容器（跳过 script，避免 appendChild 触发其重复执行） */
		if (doc.body) {
			Array.prototype.forEach.call(doc.body.childNodes, function(node) {
				if (node.nodeName && node.nodeName.toLowerCase() === 'script') return;
				out.appendChild(node);
			});
		}
		/* console 输出区 */
		var logBox = document.createElement('div');
		logBox.className = 'sp-log';
		out.appendChild(logBox);
		var render = function() {
			var lines = window.__spLines.slice();
			logBox.textContent = lines.join('\n') || '执行完成';
		};
		render();
		/* 异步输出（setTimeout 等）轮询刷新 */
		var timer = setInterval(render, 400);
	});
	bar.appendChild(audit_btn);
	bar.appendChild(run_btn);
	container.appendChild(bar);
	container.appendChild(pre);
	container.appendChild(out);
	appendNote();
}

/* 图片节点：DataURL 直接显示（CSP img-src 允许 data:） */
function renderSpImage(container, sp, appendNote) {
	var img = document.createElement('img');
	img.className = 'sp-image';
	img.src = sp.data;
	img.alt = sp.name || '图片';
	container.appendChild(img);
	appendNote();
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

function formatBytes(n) {
	var units = ['B', 'KB', 'MB', 'GB'];
	var i = 0;
	while (n >= 1024 && i < units.length - 1) { n /= 1024; i++; }
	return n.toFixed(1) + units[i];
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
var sp_edit_original = null;	/* 编辑图片/文件节点时的原信封字符串：未选新文件则原样提交 */
/* 面板默认展开的最小视口宽度（px）：窄屏下面板展开会遮挡内容，默认折叠；宽屏默认展开 */
var PANEL_EXPAND_MIN_WIDTH = 768;
var is_expand = window.innerWidth >= PANEL_EXPAND_MIN_WIDTH;
var editing_id = null;	/* 编辑模式：正在编辑的节点 id，null 表示当前是新增模式 */
var moving_node = null;	/* 移动模式：待移动的节点 {id, pid, title, content}，null 表示未在移动 */

/* ===== 输入框自动高度调整 ===== */
function autoResizeTextarea(el) {
	el.style.height = 'auto';
	el.style.height = Math.min(el.scrollHeight, 400) + 'px';
	el.style.overflowY = el.scrollHeight > 400 ? 'auto' : 'hidden';
}
input_note_content.addEventListener('input', function() {
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
	var is_file = (t === 'image' || t === 'file');
	sp_file.style.display = is_file ? '' : 'none';
	input_note_content.style.display = is_file ? 'none' : '';
	sp_file_tip.textContent = '';
	sp_file_tip.className = is_file ? 'tip-yellow' : '';
	if (!is_file) {
		sp_file.value = '';
		sp_edit_original = null;
	}
	if (t === 'link') input_note_content.placeholder = '请输入链接 URL（https://…）';
	else if (t === 'js') input_note_content.placeholder = '请输入完整前端代码（可含 HTML/CSS/JS）';
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

/* by qing — 构造提交内容：普通节点返回原文本，特殊节点返回信封 JSON 字符串。
 * 校验失败返回 null（已弹提示）；非管理员永远按普通文本提交。 */
async function buildSubmitContent() {
	var t = (current_user && current_user.is_admin) ? sp_type.value : 'text';
	if (t === 'text') return input_note_content.value;
	if (t === 'link') {
		var url = input_note_content.value.trim();
		if (!/^https?:\/\//i.test(url)) { alert('链接必须以 http:// 或 https:// 开头'); return null; }
		return JSON.stringify({ _sp: 'link', url: url });
	}
	if (t === 'js') {
		var code = input_note_content.value;
		if (!code.trim()) { alert('请输入前端代码'); return null; }
		return JSON.stringify({ _sp: 'js', code: code });
	}
	/* image / file：优先使用新选择的文件；编辑模式下未选新文件则保留原信封 */
	if (sp_file.files.length === 0) {
		if (sp_edit_original) return sp_edit_original;
		alert('请选择要上传的' + (t === 'image' ? '图片' : '文件'));
		return null;
	}
	var f = sp_file.files[0];
	if (f.size > SP_MAX_FILE_BYTES) {
		alert('文件过大（上限约 2.5MB）：' + f.name);
		return null;
	}
	var dataUrl = await readFileAsDataURL(f);
	if (t === 'image') {
		if (dataUrl.indexOf('data:image/') !== 0) { alert('所选文件不是图片'); return null; }
		return JSON.stringify({ _sp: 'image', name: f.name, data: dataUrl });
	}
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
 * 特殊节点：link/js 回填 url/code 到文本域；image/file 记住原信封（未选新文件则原样提交） */
function startEdit(item) {
	editing_id = item.id;
	input_note_title.value = item.title;
	var sp = parseSp(item.content);
	sp_edit_original = null;
	if (sp && (sp._sp === 'link' || sp._sp === 'js')) {
		sp_type.value = sp._sp;
		sp_type.dispatchEvent(new Event('change'));
		input_note_content.value = (sp._sp === 'link') ? (sp.url || '') : (sp.code || '');
	} else if (sp) {
		sp_type.value = sp._sp;
		sp_type.dispatchEvent(new Event('change'));
		sp_edit_original = item.content;
		sp_file_tip.textContent = '不选择新文件则保持原' + (sp._sp === 'image' ? '图片' : '文件');
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


	recommend_list.replaceChildren();
	for (let i in main_node) {
		let item = main_node[i]
		console.log(item)
		let card = document.createElement('div');
		card.className = "card";
		card.addEventListener('click', function(event) {
			if (!confirmLeaveIfDirty()) return;	/* 未放弃输入则不跳转，stack 保持原样 */
			var id = Number(item.id)
			stack.push(id)
			getNode(id).then(load_notes)
			getNode(id).then(loadMap)
		});
		let ttl = document.createElement('center');
		let ttl1 =document.createElement('h3');
		ttl1.textContent = item.title;
		card.id = item.title;
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
		// 互动统计：眼睛=观看、爱心=点赞、转发=分享（后端 Notes 表暂无计数字段，缺省显示 0）
		let sts_div = document.createElement('div');
		sts_div.className = "card-stats";
		sts_div.appendChild(makeStatItem(ICON_EYE, item.visit_count));
		sts_div.appendChild(makeStatItem(ICON_HEART, item.like_count));
		sts_div.appendChild(makeStatItem(ICON_SHARE, item.share_count));
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
            /* 栈空（回到根）时取 0：原判断 stack.length >= 0 恒真，空栈取 stack[-1] 得 undefined */
            var id = (stack.length > 0) ? stack[stack.length - 1] : 0;
            getNode(id).then(load_notes);
            getNode(id).then(loadMap);
        });
        minimap.appendChild(back);
    }
    for (let i in children_json) {
        let item = children_json[i];
        let tag = document.createElement("div");
        let title = item.title;
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
		/* 管理员显示特殊节点面板（普通用户始终普通文本） */
		if (current_user.login && current_user.is_admin) {
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
	fold.className = "tag fold-btn";
	if (userpanel_expand) {
		fold.textContent = "v 折叠";
	} else {
		fold.textContent = "> " + (current_user.login ? current_user.user : "未登录");
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

	let logout_tag = document.createElement("div");
	logout_tag.className = "tag back";
	logout_tag.textContent = "退出登录";
	logout_tag.addEventListener("click", async () => {
		try { await fetch('/logout', {method: 'POST'}); } catch (e) {}
		location.href = '/login.html';
	});
	userpanel.appendChild(logout_tag);
}

getNode(0).then(load_notes);
getNode(0).then(loadMap)
loadUserPanel();
