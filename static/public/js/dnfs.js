
/* by qing.
 *
 * 用于获取一个节点下的所有子节点信息，0为根节点。
 */
async function getNode(token, id) {
	try {
		obj = { "pid": String(id) }
		jsonBody = JSON.stringify(obj)
		console.log(jsonBody)
		const res = await fetch('/get_notes', {
			method: 'POST',
			headers: {
				'Content-Type': 'application/json',	// 告诉服务器内容为json
				'Authorization': `Bearer ${token}`,
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
async function addNode(token, pid, title, content) {
	try {
		obj = {
			"pid": String(pid),
			"title": title,
			"content": content
		}
		jsonBody = JSON.stringify(obj)
		console.log(jsonBody)
		const res = await fetch('/add_note', {
			method: 'POST',
			headers: {
				'Content-Type': 'application/json',	// 告诉服务器内容为json
				'Authorization': `Bearer ${token}`,
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

		if (!res.ok) {
			throw new Error(result.message || `添加节点失败（HTTP ${res.status}）`);
		}

		getNode(token, stack[stack.length - 1]).then(load_notes);

		//if (result.success) {
		console.log(`添加节点成功`, result);
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

async function deleteNode(token, id) {
	try {
		obj = {"id": String(id)};
		jsonBody = JSON.stringify(obj);
		console.log(jsonBody);

		const res = await fetch('/delete_note', {
			method: 'POST',
			headers: {
				'Content-Type': 'application/json',
				'Authorization': `Bearer ${token}`,
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

		if (!res.ok) {
			throw new Error(result.message || `删除节点失败（HTTP ${res.status}）`);
		}

		stack.pop();
		getNode(token, stack[stack.length - 1]).then(load_notes);

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

input_note_submit.addEventListener('click', function(event) {
	var title = input_note_title.value
	var content = input_note_content.value
	var pid = (stack.length == 0) ? 0 : stack[stack.length - 1];
	alert(stack.length)
	alert(pid)
	addNode(token, pid, title, content).then(result => {
		alert(result.message);
	})
})

input_note_clear.addEventListener('click', function(event) {
	input_note_title.value = "";
	input_note_content.value = "";
})

delete_note.addEventListener('click', (event) => {
	var id = (stack.length == 0) ? 0 : stack[stack.length - 1];
	if (id == 0) {
		alert('无法删除根节点');
		console.log("节点未删除");
	}
	else {
		deleteNode(token, id);
	}
})

function load_notes(main_node) {
	article_title.textContent = "";
	article_content.textContent = "";

	recommend_list.replaceChildren();
	//alert(stack.length)
	if (stack.length > 0) {	/* 如果不在根节点 */
		var bak = document.createElement('center');
		bak.addEventListener('click', function (event) {
			stack.pop();
			var id = (stack.length > 1) ? stack[stack.length - 1] : 0;
			getNode(token, id).then(load_notes)
		});
		var bak1 = document.createElement('h1');
		bak1.textContent = "返回上一层";
		bak.appendChild(bak1)
		recommend_list.appendChild(bak)
	}
	for (let i in main_node) {
		let item = main_node[i]
		let card = document.createElement('div'); 
		card.className = "card";
		card.addEventListener('click', function(event) {
			var id = Number(item.id)
			stack.push(id)
			getNode(token, id).then(load_notes)
		});
		let ttl = document.createElement('center');
		let ttl1 =document.createElement('h3');
		ttl1.textContent = item.title;
		ttl.appendChild(ttl1);
		card.appendChild(ttl);
		let vit_div = document.createElement('p');
		let lke_div = document.createElement('p');
		let srd_div = document.createElement('p');
		vit_div.textContent = "观看数：" + item.visit_count;
		lke_div.textContent = "点赞数：" + item.like_count;
		srd_div.textContent = "分享数：" + item.share_count;
		ttl.appendChild(vit_div);
		ttl.appendChild(lke_div);
		ttl.appendChild(srd_div);
		let ctt = document.createElement('p');
		renderMarkdown(ctt, item.content);
		card.appendChild(ctt)
		recommend_list.appendChild(card)
		recommend_list.appendChild(document.createElement('br'))
	}
};

var token = sessionStorage.getItem('q4_token')
getNode(token, 0).then(load_notes);
