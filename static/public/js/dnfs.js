// 认证改为 HttpOnly Cookie（gateway 下发 q4_token），浏览器同源请求自动携带，JS 不再经手 Token

/* by qing.
 *
 * 用于获取一个节点下的所有子节点信息，0为根节点。
 */
async function getNode(id) {
	try {
		obj = { "pid": id }
		jsonBody = JSON.stringify(obj)
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
		let result;

		try {
			result = JSON.parse(responseText);
		} catch {
			console.error(`获取笔记接口返回非 JSON 内容（HTTP ${res.status}）：`, responseText);
			throw new Error('服务器响应格式异常，请稍后重试');
		}

		if (res.status === 401) {
			// 凭证失效（过期或网关重启），跳转重新登录
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
		// 获取节点失败（非 JSON 响应 / HTTP 错误 / 网络异常），统一跳转登录页
		console.error('获取节点请求异常：', err);
		location.href = '/login.html';
	} finally {
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
	const md = window.markdownit();
	element.innerHTML = md.render(content, {html: false});
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
/* 面板默认展开的最小视口宽度（px）：窄屏下面板展开会遮挡内容，默认折叠；宽屏默认展开 */
var PANEL_EXPAND_MIN_WIDTH = 768;
var is_expand = window.innerWidth >= PANEL_EXPAND_MIN_WIDTH;
var editing_id = null;	/* 编辑模式：正在编辑的节点 id，null 表示当前是新增模式 */
var moving_node = null;	/* 移动模式：待移动的节点 {id, pid, title, content}，null 表示未在移动 */

input_note_submit.addEventListener('click', function(event) {
	if (input_note_submit.disabled) return;	// 防重复点击
	var title = input_note_title.value
	var content = input_note_content.value
	var pid = (stack.length == 0) ? 0 : stack[stack.length - 1];
	//alert(stack.length)
	//alert(pid)
	// 发布期间同时禁用重置/删除，避免误触干扰
	input_note_submit.disabled = true;
	input_note_clear.disabled = true;
	delete_note.disabled = true;
	// 编辑模式下保存修改，否则新增节点
	var req = (editing_id != null) ? updateNode(editing_id, pid, title, content) : addNode(pid, title, content);
	Promise.resolve(req)
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

/* by qing — 点击卡片"编辑"：把节点标题/内容载入下方表单，进入编辑模式 */
function startEdit(item) {
	editing_id = item.id;
	input_note_title.value = item.title;
	input_note_content.value = item.content;
	input_note_submit.textContent = "保存修改";
	input_note_title.scrollIntoView({ behavior: 'smooth', block: 'center' });
	input_note_title.focus();
}

/* by qing — 退出编辑模式，表单恢复为新增状态 */
function cancelEdit() {
	editing_id = null;
	input_note_submit.textContent = "提交";
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
		renderMarkdown(ctt, item.content);
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
            stack.pop();
            var id = (stack.length >= 0) ? stack[stack.length - 1] : 0;
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
