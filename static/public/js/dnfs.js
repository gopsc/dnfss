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
			console.error(`登录接口返回非 JSON 内容（HTTP ${res.status}）：`, responseText);
			throw new Error('服务器响应格式异常，请稍后重试');
		}

		if (!res.ok) {
			throw new Error(result.message || `获取节点失败（HTTP ${res.status}）`);
		}

		//if (result.success) {
		alert(`获取节点成功`);
		//...

		//} else {
		//	alert(result.message || '获取节点失败');
		//}

	} catch (err) {
		alert(err.message || '接口请求失败，请检查后端服务是否启动');
		console.error('登录请求异常：', err);
	} finally {
	}
}

var token = sessionStorage.getItem('q4_token')
var main_node = getNode(token, 0)
