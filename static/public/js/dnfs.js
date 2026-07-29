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
		alert(err.message || '接口请求失败，请检查后端服务是否启动');
		console.error('获取节点请求异常：', err);
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

/* by shufeng2012 */
function renderMarkdown(element, content) {
	/*
	Markdown渲染
	*/
	const md = window.markdownit();
	element.innerHTML = md.render(content, {html: false});
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
var is_expand = true;

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
	addNode(pid, title, content)
		.finally(() => {
			input_note_submit.disabled = false;
			input_note_clear.disabled = false;
			delete_note.disabled = false;
		});
})

input_note_clear.addEventListener('click', function(event) {
	input_note_title.value = "";
	input_note_content.value = "";
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

function load_notes(main_node) {

	/* 这个不能设置为空的 */
	//article_title.textContent = "";
	//article_content.textContent = "";

	if (input_note_title.value != "" || input_note_content.value != "") {
		if (confirm("是否放弃当前输入的内容？") == true) {
			input_note_title.value = "";
			input_note_content.value = "";
		} else {
			if (stack.length != 0) {
				return;
			}
		}
	} else {
			input_note_title.value = "";
			input_note_content.value = "";
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
		//let vit_div = document.createElement('p');
		//let lke_div = document.createElement('p');
		//let srd_div = document.createElement('p');
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
		//vit_div.textContent = "观看数：" + item.visit_count;
		//lke_div.textContent = "点赞数：" + item.like_count;
		//srd_div.textContent = "分享数：" + item.share_count;
		ttl.appendChild(wrt_div);
		//ttl.appendChild(vit_div);
		//ttl.appendChild(lke_div);
		//ttl.appendChild(srd_div);
		let ctt = document.createElement('p');
		renderMarkdown(ctt, item.content);
		card.appendChild(ctt)
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

getNode(0).then(load_notes);
getNode(0).then(loadMap)
