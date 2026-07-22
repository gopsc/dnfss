async function getNode(token, id) {
	try {
		obj = { "pid": String(id) }
		jsonBody = JSON.stringify(obj)
		alert(jsonBody)
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
		alert(`获取节点成功`);
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

async function addNode(token, pid, title, content) {
	try {
		obj = {
			"pid": String(pid),
			"title": title,
			"content": content
		}
		jsonBody = JSON.stringify(obj)
		alert(jsonBody)
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

		//if (result.success) {
		alert(`添加节点成功`);
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


var article_title = document.getElementById('article-title');
var article_content = document.getElementById('article-content');
var input_note_title = document.getElementById('input-note-title');
var input_note_content = document.getElementById('input-note-content');
var input_note_submit = document.getElementById('input-note-submit');
var input_note_clear = document.getElementById('input-note-clear');
var recommend_list = document.getElementById('recommend-list');

input_note_submit.addEventListener('click', function(event) {
	var title = input_note_title.value
	var content = input_note_content.value
	addNode(token, 0, title, content).then(result => {
		alert(result.message);
	})
})

input_note_clear.addEventListener('click', function(event) {
	input_note_title.value = "";
	input_note_content.value = "";
})

var token = sessionStorage.getItem('q4_token')
getNode(token, 0).then(main_node => {
	article_title.textContent = "";
	article_content.textContent = "";
	console.log(main_node)
	recommend_list.replaceChildren();
	for (i in main_node) {
		item = main_node[i]
		var ttl = document.createElement('h3');
		ttl.textContent = item.title
		document.getElementById('recommend-list').appendChild(ttl)
		//document.getElementById('recommend-list').appendChild(document.createElement('br'))
		var ctt = document.createElement('p');
		ctt.textContent = item.content
		document.getElementById('recommend-list').appendChild(ctt)
		//document.getElementById('recommend-list').appendChild(document.createElement('br'))
	}
});
