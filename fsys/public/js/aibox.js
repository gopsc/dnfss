'use strict';
/* ============================================================
 * aibox Web 客户端 · 移动端版（经 gateway WS 反向代理，仅管理员可用）
 * 协议与 BotOps/aibox client.py 完全兼容：
 *   发送：{"type":"message","content":...} / {"type":"command","command":"/..."} / {"type":"interrupt",...}
 *   接收：chunk(增量片段) / reasoning_start / reasoning / reasoning_end / ai_break /
 *         complete / line(过程行) / task_process / task_status / task_error / task_added /
 *         dialogue_ended / dialogue_reset / interrupt_ack / error / info /
 *         command_result / connection_ack / notification / pong
 * ============================================================ */

/* gateway [WsProxy] routes 中配置的路径（HttpOnly q4_token Cookie 随握手自动携带，JS 不经手 Token）。
 * 需与 gateway.ini 的 [WsProxy] routes 条目一致，如：routes = /ws/aibox=ws://127.0.0.1:8765; */
const WS_URL = (location.protocol === 'https:' ? 'wss://' : 'ws://') + location.host + '/ws/aibox';
const RECONNECT_MIN = 2000;		/* 指数退避起点（2s→30s 封顶） */
const RECONNECT_MAX = 30000;
const REASONING_TAIL = 3000;	/* 思考区域超长时仅显示末尾字符数 */

/* ---------- Markdown 渲染（链接一律新标签打开且不带 referrer） ---------- */
const md = window.markdownit({ html: false, linkify: true, breaks: true });
const defaultLinkOpen = md.renderer.rules.link_open || ((tokens, idx, options, env, self) => self.renderToken(tokens, idx, options));
md.renderer.rules.link_open = (tokens, idx, options, env, self) => {
	tokens[idx].attrSet('target', '_blank');
	tokens[idx].attrSet('rel', 'noopener noreferrer');
	return defaultLinkOpen(tokens, idx, options, env, self);
};

/* ---------- DOM ---------- */
const chatEl = document.getElementById('chat');
const inputEl = document.getElementById('input');
const sendBtn = document.getElementById('send-btn');
const interruptBtn = document.getElementById('interrupt-btn');
const connBtn = document.getElementById('conn-btn');
const statusEl = document.getElementById('conn-status');
const statusText = document.getElementById('status-text');
const taskPanel = document.getElementById('task-panel');
const taskList = document.getElementById('task-list');
const taskToggle = document.getElementById('task-toggle');
const taskBadge = document.getElementById('task-badge');
const backdrop = document.getElementById('backdrop');

/* ---------- 状态 ---------- */
let ws = null;
let manualClose = false;		/* 手动断开则抑制自动重连 */
let reconnectDelay = RECONNECT_MIN;
let reconnectTimer = null;
let heartbeatTimer = null;		/* 心跳定时器，10s 发送 ping 保持 WS 连接 */
const HEARTBEAT_INTERVAL = 10000;	/* 心跳间隔：需低于前置代理（Nginx/CDN）的 proxy_read_timeout */

function startHeartbeat() {
	stopHeartbeat();
	heartbeatTimer = setInterval(() => {
		if (ws && ws.readyState === WebSocket.OPEN) {
			ws.send(JSON.stringify({ type: 'ping' }));
		}
	}, HEARTBEAT_INTERVAL);
}

function stopHeartbeat() {
	if (heartbeatTimer) { clearTimeout(heartbeatTimer); heartbeatTimer = null; }
}

/* 流式状态：不预创建空气泡，第一个 chunk 到来才挂载（保证思考区必然在正式回答之前） */
let aiBubble = null, aiText = '', aiActive = false;
let reasoningBox = null, reasoningBody = null, reasoningHead = null, reasoningText = '';
let unreadTasks = 0;

/* ---------- 滚动（距底 80px 内才自动跟随，用户上翻阅读不打断） ---------- */
function nearBottom() {
	return chatEl.scrollHeight - chatEl.scrollTop - chatEl.clientHeight < 80;
}
function scrollToBottom(force) {
	if (force || nearBottom()) chatEl.scrollTop = chatEl.scrollHeight;
}

/* ---------- 系统消息 / 任务面板行 ---------- */
function addSystem(text, isError) {
	const div = document.createElement('div');
	div.className = isError ? 'msg msg-error' : 'msg msg-system';
	div.textContent = text;
	chatEl.appendChild(div);
	scrollToBottom();
}

function addTaskLine(text) {
	const div = document.createElement('div');
	div.textContent = text;
	taskList.appendChild(div);
	taskList.scrollTop = taskList.scrollHeight;
	if (!taskPanel.classList.contains('open')) {
		unreadTasks += 1;
		taskBadge.style.display = '';
		taskBadge.textContent = unreadTasks > 99 ? '99+' : String(unreadTasks);
	}
}

/* ---------- 消息气泡 ---------- */
function addUserBubble(text) {
	const div = document.createElement('div');
	div.className = 'msg msg-user';
	div.textContent = text;
	chatEl.appendChild(div);
	scrollToBottom(true);
}

function ensureAiBubble() {
	if (aiBubble) return;
	aiBubble = document.createElement('div');
	aiBubble.className = 'msg msg-ai streaming';
	chatEl.appendChild(aiBubble);
	scrollToBottom(true);
}

/* 服务端 chunk 为增量片段（发送后即清零），直接拼接 */
function appendAi(delta) {
	if (!delta || aiActive === false) return;
	ensureAiBubble();
	aiText += delta;
	aiBubble.innerHTML = md.render(aiText);
	scrollToBottom();
}

/* complete 为收尾信号：chunk 流式是内容权威来源，仅异常丢帧时用 full_text 补建 */
function finishAi(fullText) {
	if (aiActive === false && !aiBubble && !reasoningBox) return;
	if (!aiBubble && fullText) {
		ensureAiBubble();
		aiText = fullText;
		aiBubble.innerHTML = md.render(aiText);
	}
	if (aiBubble) aiBubble.classList.remove('streaming');
	aiBubble = null; aiText = ''; aiActive = false;
	reasoningBox = null; reasoningBody = null; reasoningHead = null; reasoningText = '';
	setStreaming(false);
	scrollToBottom();
}

/* ---------- 思考区域（可折叠） ---------- */
function startReasoning(hint) {
	if (reasoningBox) return;
	reasoningText = '';
	reasoningBox = document.createElement('div');
	reasoningBox.className = 'msg msg-reasoning';
	reasoningHead = document.createElement('div');
	reasoningHead.className = 'reasoning-head';
	reasoningHead.textContent = '💭 ' + (hint || '思考中...');
	reasoningBody = document.createElement('div');
	reasoningBody.className = 'reasoning-body';
	reasoningHead.addEventListener('click', () => reasoningBody.classList.toggle('folded'));
	reasoningBox.appendChild(reasoningHead);
	reasoningBox.appendChild(reasoningBody);
	chatEl.appendChild(reasoningBox);
	scrollToBottom(true);
}
function appendReasoning(delta) {
	if (!delta) return;
	if (aiActive === false) return;	/* 已收尾（中断/完成），丢弃残留片段 */
	if (!reasoningBox) startReasoning();
	reasoningText += delta;
	reasoningBody.textContent = reasoningText.length > REASONING_TAIL
		? '...(前略)\n' + reasoningText.slice(-REASONING_TAIL)
		: reasoningText;
	scrollToBottom();
}
function endReasoning() {
	if (reasoningHead && reasoningText) {
		reasoningHead.textContent = '💭 思考过程（' + reasoningText.length + ' 字符，点击折叠/展开）';
		reasoningBody.classList.add('folded');	/* 结束后默认折叠，避免占屏 */
	}
	/* 清除引用（DOM元素保留显示），下一轮 reasoning_start 创建新的思考区域 */
	reasoningBox = null; reasoningHead = null; reasoningBody = null; reasoningText = '';
}
function abortReasoning() {
	/* 用户中断：更新思考区域标签为已中断，保留已展示内容 */
	if (reasoningHead) {
		if (reasoningText) {
			reasoningHead.textContent = '🛑 思考已中断（' + reasoningText.length + ' 字符）';
		} else {
			reasoningHead.textContent = '🛑 思考已中断';
		}
	}
	/* 清除引用，DOM元素保留显示 */
	reasoningBox = null; reasoningHead = null; reasoningBody = null; reasoningText = '';
}

/* ---------- AI 文本分段（工具调用时 breakAi，后续 chunk 创建新气泡） ---------- */
function breakAi() {
	if (aiBubble) {
		aiBubble.classList.remove('streaming');
		aiBubble = null; aiText = '';
	}
}

/* ---------- 连接状态 ---------- */
function setStatus(cls, text) {
	statusEl.className = cls;
	statusText.textContent = text;
}
function setStreaming(on) {
	interruptBtn.style.display = on ? '' : 'none';
	sendBtn.disabled = false;	/* 流式期间仍可发送（服务端排队），仅中断按钮显隐 */
}

/* ---------- WebSocket 生命周期（指数退避自动重连，手动断开抑制） ---------- */
function connect() {
	if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) return;
	clearTimeout(reconnectTimer);
	manualClose = false;
	setStatus('connecting', '连接中');
	try {
		ws = new WebSocket(WS_URL);
	} catch (e) {
		addSystem('连接失败：' + e.message, true);
		scheduleReconnect();
		return;
	}
	ws.onopen = () => {
		reconnectDelay = RECONNECT_MIN;
		setStatus('connected', '已连接');
		connBtn.textContent = '断开';
		connBtn.classList.add('on');
		startHeartbeat();
	};
	ws.onmessage = (ev) => {
		let data;
		try { data = JSON.parse(ev.data); }
		catch (e) { data = { type: 'chunk', content: ev.data }; }
		routeMessage(data);
	};
	ws.onclose = () => {
		stopHeartbeat();
		setStatus('disconnected', '已断开');
		connBtn.textContent = '连接';
		connBtn.classList.remove('on');
		finishAi('');
		ws = null;
		if (!manualClose) scheduleReconnect();
	};
	ws.onerror = () => { /* onclose 随后触发，统一在 onclose 处理 */ };
}

function scheduleReconnect() {
	if (manualClose) return;
	setStatus('connecting', Math.round(reconnectDelay / 1000) + 's 后重连');
	clearTimeout(reconnectTimer);
	reconnectTimer = setTimeout(() => {
		reconnectDelay = Math.min(reconnectDelay * 2, RECONNECT_MAX);
		connect();
	}, reconnectDelay);
}

function disconnect(manual) {
	manualClose = manual !== false;
	clearTimeout(reconnectTimer);
	stopHeartbeat();
	if (ws) { ws.close(); ws = null; }
	setStatus('disconnected', manualClose ? '已手动断开' : '已断开');
	connBtn.textContent = '连接';
	connBtn.classList.remove('on');
}

/* ---------- aibox 协议消息路由 ---------- */
function routeMessage(data) {
	const t = data.type || '';
	const content = typeof data.content === 'string' ? data.content : (data.content == null ? '' : String(data.content));

	if (t === 'chunk') {
		appendAi(content);
	} else if (t === 'reasoning_start') {
		startReasoning(content || '深度思考中...');
	} else if (t === 'reasoning') {
		appendReasoning(content);
	} else if (t === 'reasoning_end') {
		endReasoning();
	} else if (t === 'ai_break') {
		/* 工具调用前分段：结束当前AI文本段（保留已显示内容），后续chunk创建新气泡 */
		breakAi();
	} else if (t === 'line') {
		/* 过程行：工具调用提示 / 工具输出 → 任务面板；助手前缀 / 思考提示等聊天区已有承载，跳过 */
		const stripped = content.trim();
		if (!stripped) return;
		if (/^(💬 助手|💬 Assistant|🤔 思考中|🤔 Thinking|🤔 深度思考中|🤔 分析工具结果|🤔 Analyzing|🔧 需要调用工具|🔧 Need to call)/.test(stripped)) return;
		addTaskLine(stripped);
	} else if (t === 'complete') {
		finishAi(content);
	} else if (t === 'dialogue_ended') {
		finishAi(aiText);
		if (content) addSystem('✅ ' + content);
	} else if (t === 'task_added') {
		addSystem('➕ ' + content);
	} else if (t === 'dialogue_reset') {
		if (content) addSystem('🔄 ' + content);
	} else if (t === 'pong') {
		/* 心跳响应，忽略 */
	} else if (t === 'task_process') {
		const sub = data.msg_type || '';
		const title = data.task_title || '未知任务';
		if (sub === 'chunk') {
			const tail = content.slice(-80);
			if (tail.trim()) addTaskLine('⚡ ' + title + ': ' + tail);
		} else if (sub === 'complete') {
			addTaskLine('✅ 任务完成: ' + title);
		}
	} else if (t === 'task_status') {
		const icon = { executing: '⚙️', complete: '✅', failed: '❌', added: '➕', batch_start: '🚀', batch_complete: '🏁' }[data.status] || '📋';
		addTaskLine(icon + ' [' + (data.task_id || '?') + '] ' + (data.title || '') + ': ' + (data.message || content));
	} else if (t === 'task_error') {
		addTaskLine('❌ [' + (data.task_id || '?') + '] ' + (data.task_title || '?') + ': ' + (data.error || '未知错误'));
	} else if (t === 'interrupt_ack') {
		/* 服务端确认中断：立即收尾前端状态 */
		abortReasoning();
		finishAi('');
		addSystem('🛑 ' + (content || '已中断'));
	} else if (t === 'error') {
		addSystem('❌ ' + content, true);
	} else if (t === 'info') {
		/* 过滤隐式通知（对话开始等），避免污染聊天流 */
		if (!/(Conversation started|对话开始|🎭)/.test(content)) addSystem('ℹ️ ' + content);
	} else if (t === 'command_result') {
		addSystem(content);
	} else if (t === 'connection_ack') {
		addSystem('✅ ' + content);
	} else if (t === 'notification') {
		addSystem('🔔 ' + content);
	} else if (content) {
		/* 未知消息类型不追加到AI聊天区（避免非AI内容污染回复），记入任务面板 */
		addTaskLine('[未知消息 type=' + t + '] ' + content.slice(0, 200));
	}
}

/* ---------- 发送 ---------- */
function wsSend(obj) {
	if (!ws || ws.readyState !== WebSocket.OPEN) {
		addSystem('未连接到服务器', true);
		return false;
	}
	ws.send(JSON.stringify(obj));
	return true;
}

/* 本地命令表：命中则本地处理，其余 / 命令透传服务器 */
const LOCAL_COMMANDS = {
	'/clear'() {
		chatEl.replaceChildren();
		finishAi('');
	},
	'/help'() {
		addSystem('本地命令：/clear 清屏 · /connect 连接 · /disconnect 断开 · /interrupt 中断 · 其余 / 命令透传服务器');
	},
	'/connect'() { connect(); },
	'/disconnect'() { disconnect(true); },
	'/interrupt'() { sendInterrupt(); },
};

function sendCurrentInput() {
	const text = inputEl.value.trim();
	if (!text) return;
	if (text.startsWith('/')) {
		const local = LOCAL_COMMANDS[text.split(/\s/)[0]];
		if (local) {
			local();
			inputEl.value = '';
			autoResize();
			return;
		}
	}
	/* 与 BotOps 一致：/ 开头按 command 发送，否则 message */
	const data = text.startsWith('/')
		? { type: 'command', command: text }
		: { type: 'message', content: text };
	if (!wsSend(data)) return;
	addUserBubble(text);
	aiActive = true;	/* 开始接收本轮 AI 流 */
	setStreaming(true);
	inputEl.value = '';
	autoResize();
	inputEl.focus();
}

function sendInterrupt() {
	wsSend({ type: 'interrupt', content: '用户按下了 Ctrl+C 中断当前操作' });
}

/* ---------- 输入区自适应高度 ---------- */
function autoResize() {
	inputEl.style.height = 'auto';
	inputEl.style.height = Math.min(inputEl.scrollHeight, 120) + 'px';
}
inputEl.addEventListener('input', autoResize);
inputEl.addEventListener('keydown', (e) => {
	/* Enter 发送，Shift+Enter 换行；isComposing 防中文输入法选词误发 */
	if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) {
		e.preventDefault();
		sendCurrentInput();
	}
});
/* 软键盘弹出动画结束后保持聊天区停留底部 */
inputEl.addEventListener('focus', () => setTimeout(() => scrollToBottom(true), 300));
sendBtn.addEventListener('click', sendCurrentInput);
interruptBtn.addEventListener('click', sendInterrupt);

/* ---------- 连接按钮与任务面板（抽屉开关） ---------- */
connBtn.addEventListener('click', () => {
	if (ws && ws.readyState === WebSocket.OPEN) disconnect(true);
	else connect();
});

function openTaskPanel(open) {
	taskPanel.classList.toggle('open', open);
	backdrop.classList.toggle('show', open);
	if (open) {
		unreadTasks = 0;
		taskBadge.style.display = 'none';
	}
}
taskToggle.addEventListener('click', () => openTaskPanel(!taskPanel.classList.contains('open')));
document.getElementById('task-close').addEventListener('click', () => openTaskPanel(false));
backdrop.addEventListener('click', () => openTaskPanel(false));

/* ---------- 启动：管理员预检（WS 握手由 gateway 强制 is_admin，此处仅提示） ---------- */
(async () => {
	try {
		const res = await fetch('/whoami', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' });
		const data = res.ok ? await res.json() : null;
		const tip = document.getElementById('admin-tip');
		if (!data || !data.login) {
			tip.textContent = '未登录：aibox 仅管理员可用，请先登录管理员账号';
			tip.style.display = '';
		} else if (!data.is_admin) {
			tip.textContent = '当前账号不是管理员，连接将被网关拒绝';
			tip.style.display = '';
		}
	} catch (e) { /* 预检失败不阻塞，由 WS 握手兜底 */ }
	connect();
})();
