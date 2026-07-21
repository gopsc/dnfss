const API = "";
let currentPid = 0, parentStack = [], editingId = null, deletingId = null;
let currentFileInfo = { // 存储当前文件信息
    name: "",
    size: 0,
    base64: "",
    uploadTime: "",
    metadataError: false
};
/* 缓存所有节点的文件信息，方便直接下载
    * 20250907(qing): 文件多了会卡 */
let nodeFileCache = {};
let lastLoadedNodes = []; // 缓存最近加载的节点列表，用于移动模式下重新渲染
let movingNode = null; // 正在迁移的节点 {id, title, content}

// 缓存DOM元素引用，不用每次都去找
const domElements = {
    deleteModal: null,
    deleteMessage: null,
    deleteNodeTitle: null,
    deleteFileHint: null,
    toastContainer: null,
    fileInfo: null,
    fileMetaContainer: null,
    fileMetaInfo: null,
    metaFilename: null,
    metaFilesize: null,
    metaUploadtime: null,
    fileProgress: null,
    fileProgressBar: null,
    fileUpload: null,
    hiddenBase64: null,
    fileMetadata: null,
    downloadSection: null,
    dataRepairHint: null
};

// 初始化DOM元素引用
function initDomElements() {
    domElements.deleteModal = document.getElementById("delete-modal");
    domElements.deleteMessage = document.getElementById("delete-message");
    domElements.deleteNodeTitle = document.getElementById("delete-node-title-container");
    domElements.deleteFileHint = document.getElementById("delete-file-hint");
    domElements.toastContainer = document.getElementById("toast-container");
    domElements.fileInfo = document.getElementById("file-info");
    domElements.fileMetaContainer = document.getElementById("file-meta-container");
    domElements.fileMetaInfo = document.getElementById("file-meta-info");
    domElements.metaFilename = document.getElementById("meta-filename");
    domElements.metaFilesize = document.getElementById("meta-filesize");
    domElements.metaUploadtime = document.getElementById("meta-uploadtime");
    domElements.fileProgress = document.querySelector(".file-progress");
    domElements.fileProgressBar = document.getElementById("file-progress-bar");
    domElements.fileUpload = document.getElementById("file-upload");
    domElements.hiddenBase64 = document.getElementById("hidden-base64");
    domElements.fileMetadata = document.getElementById("file-metadata");
    domElements.downloadSection = document.getElementById("download-section");
    domElements.dataRepairHint = document.getElementById("data-repair-hint");
    
    // 验证关键元素是否存在
    const missingElements = [];
    for (const [key, element] of Object.entries(domElements)) {
    if (!element) {
        missingElements.push(key);
    }
    }
    
    if (missingElements.length > 0) {
    console.error("关键DOM元素缺失:", missingElements);
    alert("系统初始化失败，关键组件缺失，请刷新页面重试。");
    }
}

// 提示消息
function showToast(msg, type = 'info') {
    if (!domElements.toastContainer) {
    console.error("toast容器未找到，无法显示提示");
    return;
    }
    
    const t = document.createElement('div');
    t.className = `toast ${type} show`;
    t.innerHTML = `<i class="fas fa-${type === 'success' ? 'check' : type === 'error' ? 'exclamation-triangle' : type === 'warning' ? 'exclamation-circle' : 'info-circle'}"></i> ${msg}`;
    domElements.toastContainer.appendChild(t);
    setTimeout(() => t.remove(), 3000); // 3秒后删除
}

// HTML转义字符还原
// 我在编辑节点时，转义符号没有能够被替换
function htmlUnescape(str) {
    if (!str || typeof str !== 'string') {
    return str;
    }
    return str
    .replace(/&amp;/g, '&')
    .replace(/&lt;/g, '<')
    .replace(/&gt;/g, '>')
    .replace(/&quot;/g, '"')
    .replace(/&#039;/g, "'")
    .replace(/&#x60;/g, '`');
}
//-----
// 安全的JSON解析函数 - 新增HTML转义还原处理
function safeJsonParse(jsonString, defaultValue = {}) {
    try {
    // 先进行HTML转义还原
    const unescapedString = htmlUnescape(jsonString);
    
    // 再进行基本的格式检查
    if (!unescapedString || typeof unescapedString !== 'string' || 
        unescapedString.trim() === '' || 
        !unescapedString.startsWith('{') || !unescapedString.endsWith('}')) {
        console.error('无效的JSON格式：不是有效的对象字符串');
        return defaultValue;
    }
    
    return JSON.parse(unescapedString);
    } catch (e) {
    console.error('JSON解析失败:', e);
    console.error('问题数据:', jsonString);
    
    // 尝试提取可能有用的信息
    try {
        // 先进行HTML转义还原，再清理可能导致错误的字符
        const cleanedString = htmlUnescape(jsonString).replace(/[^\x20-\x7E]/g, '');
        if (cleanedString !== jsonString) {
        console.log('尝试清理无效字符后重新解析');
        return JSON.parse(cleanedString);
        }
    } catch (cleanError) {
        console.error('清理后仍无法解析:', cleanError);
    }
    
    return defaultValue;
    }
}

// 安全的JSON字符串化函数
function safeJsonStringify(data) {
    try {
    return JSON.stringify(data);
    } catch (e) {
    console.error('JSON序列化失败:', e);
    // 尝试使用更宽松的序列化方式
    try {
        return JSON.stringify(data, (key, value) => {
        if (typeof value === 'bigint') {
            return value.toString();
        }
        return value;
        });
    } catch (fallbackError) {
        console.error('备用序列化方式也失败:', fallbackError);
        return '{}'; // 返回空对象作为最后的 fallback
    }
    }
}

// API请求
async function request(endpoint, data = {}) {
    try {
    const res = await fetch(API + endpoint, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(data)
    });
    return res.ok ? await res.json() : Promise.reject(res.status);
    } catch (e) {
    showToast(`请求失败: ${e}`, 'error');
    throw e;
    }
}

// 检查节点是否有子节点
async function hasChildren(nodeId) {
    try {
    const children = await request("/get_notes", { pid: nodeId });
    return Array.isArray(children) && children.length > 0;
    } catch (e) {
    showToast("检查子节点失败", 'error');
    return false;
    }
}

// 解析节点内容，分离说明文本和文件信息
function parseNodeContent(content) {
    try {
    // 检查内容是否包含文件信息标记
    if (content.startsWith('FILE_METADATA:')) {
        const parts = content.split('\n---\n');
        if (parts.length >= 3) {
        const metaStr = parts[1] || '{}'; // 确保有默认值
        const base64 = parts[2] || '';
        const textContent = parts.slice(3).join('\n---\n');
        
        // 使用安全的JSON解析函数，已包含HTML转义还原
        const meta = safeJsonParse(metaStr, {
            name: '未知文件',
            size: 0,
            uploadTime: '未知时间'
        });
        
        // 检查解析结果是否有效
        const isValidMeta = meta && typeof meta === 'object' && 
                            typeof meta.name === 'string' && 
                            !isNaN(meta.size);
                            
        if (!isValidMeta) {
            console.error('解析的元数据无效，使用默认值');
            return {
            hasFile: true,
            text: textContent,
            meta: {
                name: '损坏的文件',
                size: 0,
                uploadTime: '未知时间'
            },
            base64: base64,
            metadataError: true // 标记元数据解析错误
            };
        }
        
        return {
            hasFile: true,
            text: textContent,
            meta: meta,
            base64: base64,
            metadataError: false
        };
        }
    }
    
    // 普通文本内容
    return {
        hasFile: false,
        text: content,
        meta: null,
        base64: '',
        metadataError: false
    };
    } catch (e) {
    console.error('解析节点内容失败:', e);
    showToast('节点内容解析失败，可能是格式错误', 'error');
    return {
        hasFile: false,
        text: content || '解析失败的内容',
        meta: null,
        base64: '',
        metadataError: false
    };
    }
}

// 渲染节点列表（不在页面显示Base64内容）
function renderNodes(nodes) {
    const container = document.getElementById("node-list");
    if (!container) {
    console.error("节点列表容器未找到");
    return;
    }
    
    // 缓存节点列表，用于移动模式下重新渲染
    lastLoadedNodes = nodes;
    
    // 清空缓存
    nodeFileCache = {};
    
    // 移动模式下过滤掉正在迁移的节点
    if (movingNode) {
    nodes = nodes.filter(n => n.id !== movingNode.id);
    }
    
    if (!nodes || !nodes.length) {
    container.innerHTML = `<div class="empty-state"><i class="fas fa-folder-open"></i><p>目录为空</p><p>使用上方表单添加新节点</p></div>`;
    return;
    }

    container.innerHTML = nodes.map(n => {
    // 解析节点内容，分离文本和文件信息
    const parsed = parseNodeContent(n.content);
    const safeTitle = encodeURIComponent(escapeHtml(n.title));
    const safeContent = encodeURIComponent(escapeHtml(n.content));
    const displayTitle = escapeHtml(n.title);
    const deleteBtnId = `delete-btn-${n.id}`;
    
    // 缓存文件信息，用于直接下载
    if (parsed.hasFile) {
        nodeFileCache[n.id] = {
        name: parsed.meta.name,
        base64: parsed.base64,
        metadataError: parsed.metadataError
        };
    }
    
    // 节点内容显示文本说明，不显示Base64
    const contentDisplay = parsed.text 
        ? `<div class="node-content">${escapeHtml(parsed.text)}</div>`
        : '';
        
    // 文件错误提示
    const fileErrorIndicator = parsed.metadataError 
        ? `<div style="color: #e74c3c; font-size: 0.8em; margin-top: 5px;">
            <i class="fas fa-exclamation-circle"></i> 文件信息损坏，建议重新上传
            </div>`
        : '';
        
    // 文件指示器和下载按钮
    const fileIndicator = parsed.hasFile 
        ? `<div class="file-indicator">
            <i class="fas fa-file"></i> 包含文件: ${escapeHtml(parsed.meta.name)} (${formatFileSize(parsed.meta.size)})
            <button class="download-btn" onclick="decodeAndDownload(${n.id}, '${encodeURIComponent(escapeHtml(parsed.meta.name))}')">
                <i class="fas fa-download"></i> 下载
            </button>
            </div>
            ${fileErrorIndicator}`
        : '';
    
    return `
        <div class="node ${parsed.hasFile ? 'has-file' : ''}">
        <div class="node-title" onclick="enterNode(${n.id})">
            <i class="fas ${parsed.hasFile ? 'fa-file' : 'fa-folder'}"></i> ${displayTitle}
        </div>
        ${contentDisplay}
        ${fileIndicator}
        <div class="node-actions">
            <button onclick="editNode(${n.id}, '${safeTitle}', '${safeContent}')" class="btn btn-warning"><i class="fas fa-edit"></i> 编辑</button>
            ${!movingNode ? `<button onclick="startMoveNode(${n.id}, '${displayTitle}', '${safeContent}')" class="btn btn-move"><i class="fas fa-arrows-alt"></i> 移动</button>` : ''}
            <button id="${deleteBtnId}" onclick="checkAndShowDeleteModal(event, ${n.id}, '${encodeURIComponent(escapeHtml(n.title))}', ${parsed.hasFile})" class="btn btn-danger"><i class="fas fa-trash"></i> 删除</button>
        </div>
        </div>
    `;
    }).join('');
}

// HTML转义
function escapeHtml(unsafe) {
    if (!unsafe) return '';
    return unsafe
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;")
    .replace(/`/g, "&#x60;");
}

// 加载节点
async function load(pid = 0) {
    try {
    const numericPid = Number(pid);
    if (isNaN(numericPid)) {
        throw new Error("无效的节点ID");
    }
    
    const nodes = await request("/get_notes", { pid: numericPid });
    if (Array.isArray(nodes)) {
        renderNodes(nodes);
    } else {
        throw new Error("返回数据格式不正确");
    }
    } catch (e) {
    showToast(`加载失败: ${e.message}`, 'error');
    console.error("加载节点错误:", e);
    }
}

// 进入节点
function enterNode(id) {
    const numericId = Number(id);
    if (isNaN(numericId)) {
    showToast("无效的节点ID", 'error');
    return;
    }
    
    parentStack.push(currentPid);
    currentPid = numericId;
    resetForm();
    load(currentPid);
    showToast(`进入节点 ${numericId}`, 'info');
}

// 返回上级
function goBack() {
    if (parentStack.length) {
    currentPid = parentStack.pop();
    resetForm();
    load(currentPid);
    showToast('返回上级', 'info');
    } else {
    showToast('已是顶级目录', 'info');
    }
}

// 重置表单
function resetForm() {
    const editIdEl = document.getElementById("edit-id");
    const nodeTitleEl = document.getElementById("node-title");
    const nodeContentEl = document.getElementById("node-content");
    const formModeTextEl = document.getElementById("form-mode-text");
    
    if (editIdEl && nodeTitleEl && nodeContentEl && formModeTextEl) {
    editIdEl.value = "";
    nodeTitleEl.value = "";
    nodeContentEl.value = "";
    formModeTextEl.textContent = "新增节点";
    nodeTitleEl.focus();
    editingId = null;
    
    // 重置文件相关字段
    currentFileInfo = { name: "", size: 0, base64: "", uploadTime: "", metadataError: false };
    
    if (domElements.fileUpload) domElements.fileUpload.value = "";
    if (domElements.fileInfo) domElements.fileInfo.textContent = "未选择文件";
    if (domElements.fileMetaContainer) domElements.fileMetaContainer.style.display = "none";
    if (domElements.fileMetaInfo) {
        domElements.fileMetaInfo.className = "file-meta-info"; // 重置样式
    }
    if (domElements.dataRepairHint) domElements.dataRepairHint.style.display = "none";
    if (domElements.fileProgress) domElements.fileProgress.style.display = "none";
    if (domElements.fileProgressBar) domElements.fileProgressBar.style.width = "0%";
    if (domElements.hiddenBase64) domElements.hiddenBase64.value = "";
    if (domElements.fileMetadata) domElements.fileMetadata.value = "";
    if (domElements.downloadSection) domElements.downloadSection.style.display = "none";
    } else {
    console.error("表单元素缺失，无法重置表单");
    showToast("表单操作失败", 'error');
    }
}

// 编辑节点 - 确保不显示任何编码内容
function editNode(id, title, content) {
    try {
    const numericId = Number(id);
    if (isNaN(numericId)) {
        showToast("无效的节点ID", 'error');
        return;
    }
    
    // 先解码URI组件，再还原HTML转义字符
    const decodedTitle = htmlUnescape(decodeURIComponent(title));
    const decodedContent = htmlUnescape(decodeURIComponent(content));
    
    // 解析内容，提取文本说明和文件信息
    const parsed = parseNodeContent(decodedContent);
    
    const editIdEl = document.getElementById("edit-id");
    const nodeTitleEl = document.getElementById("node-title");
    const nodeContentEl = document.getElementById("node-content");
    const formModeTextEl = document.getElementById("form-mode-text");
    
    if (editIdEl && nodeTitleEl && nodeContentEl && formModeTextEl) {
        editingId = numericId;
        editIdEl.value = numericId;
        nodeTitleEl.value = decodedTitle;
        nodeContentEl.value = parsed.text; // 只显示文本说明
        formModeTextEl.textContent = "编辑节点";
        nodeTitleEl.focus();
        
        // 如果元数据解析有错误，给予用户提示并标记样式
        if (parsed.metadataError) {
        showToast('注意：此节点的文件信息可能已损坏，建议重新上传文件', 'warning');
        if (domElements.fileMetaInfo) {
            domElements.fileMetaInfo.className = "file-meta-info error";
        }
        // 显示数据修复提示
        if (domElements.dataRepairHint) {
            domElements.dataRepairHint.style.display = "block";
        }
        } else {
        if (domElements.fileMetaInfo) {
            domElements.fileMetaInfo.className = "file-meta-info";
        }
        if (domElements.dataRepairHint) {
            domElements.dataRepairHint.style.display = "none";
        }
        }
        
        // 如果有文件信息，加载和显示文件元数据
        if (parsed.hasFile) {
        currentFileInfo = {
            name: parsed.meta.name,
            size: parsed.meta.size,
            base64: parsed.base64,  // 后台使用但不显示
            uploadTime: parsed.meta.uploadTime,
            metadataError: parsed.metadataError
        };
        
        // 存储Base64但不显示
        if (domElements.hiddenBase64) domElements.hiddenBase64.value = parsed.base64;
        if (domElements.fileMetadata) {
            // 使用安全的JSON序列化
            domElements.fileMetadata.value = safeJsonStringify(parsed.meta);
        }
        
        // 显示文件元信息
        if (domElements.fileMetaContainer) domElements.fileMetaContainer.style.display = "block";
        if (domElements.metaFilename) domElements.metaFilename.textContent = parsed.meta.name;
        if (domElements.metaFilesize) domElements.metaFilesize.textContent = formatFileSize(parsed.meta.size);
        if (domElements.metaUploadtime) domElements.metaUploadtime.textContent = parsed.meta.uploadTime;
        if (domElements.fileInfo) domElements.fileInfo.textContent = `已选择: ${parsed.meta.name}`;
        if (domElements.downloadSection) domElements.downloadSection.style.display = "block";
        }
        
        const formSection = document.querySelector('.form-section');
        if (formSection) {
        formSection.scrollIntoView({ behavior: 'smooth' });
        }
    } else {
        throw new Error("表单元素缺失");
    }
    } catch (e) {
    showToast("编辑节点失败", 'error');
    console.error("编辑节点错误:", e);
    }
}

// 保存节点（编码内容完全在后台处理）
async function saveNode() {
    try {
    const nodeTitleEl = document.getElementById("node-title");
    const nodeContentEl = document.getElementById("node-content");
    const editIdEl = document.getElementById("edit-id");
    
    if (!nodeTitleEl || !nodeContentEl || !editIdEl) {
        throw new Error("表单元素缺失");
    }
    
    const title = nodeTitleEl.value.trim();
    const textContent = nodeContentEl.value.trim();
    const nodeId = editIdEl.value;
    
    if (!title) {
        showToast("标题不能为空", 'error');
        return;
    }

    // 构建特殊格式的内容，编码内容完全在后台处理
    let content = '';
    const hasFile = !!domElements.hiddenBase64?.value.trim();
    
    if (hasFile) {
        // 有文件时，使用特殊格式存储，包含文件元数据和Base64（用户不可见）
        // 使用安全的JSON序列化
        const safeMetadata = safeJsonStringify(JSON.parse(domElements.fileMetadata.value || '{}'));
        content = `FILE_METADATA:\n---\n${safeMetadata}\n---\n${domElements.hiddenBase64.value}\n---\n${textContent}`;
    } else {
        // 没有文件时，直接存储文本内容
        content = textContent;
    }

    if (nodeId) {
        const numericId = Number(nodeId);
        if (isNaN(numericId)) {
        throw new Error("无效的节点ID");
        }
        
        await request("/update_note", { 
        id: numericId, 
        pid: currentPid, 
        title, 
        content 
        });
        showToast('节点已更新', 'success');
    } else {
        await request("/add_note", { 
        pid: currentPid, 
        title, 
        content 
        });
        showToast('节点已添加', 'success');
    }
    
    resetForm();
    load(currentPid);
    } catch (e) {
    showToast(`${nodeId ? '更新' : '添加'}失败: ${e.message}`, 'error');
    console.error("保存节点错误:", e);
    }
}

// 检查并显示删除模态框
async function checkAndShowDeleteModal(event, id, title, hasFile) {
    // 防止事件冒泡和默认行为
    if (event) {
    event.stopPropagation();
    event.preventDefault();
    }

    // 解码 encodeURIComponent(escapeHtml(...)) 双重编码的标题
    try {
    title = htmlUnescape(decodeURIComponent(title));
    } catch (e) {
    console.error("标题解码失败:", e);
    }

    // 显示文件提示
    if (domElements.deleteFileHint) {
    domElements.deleteFileHint.style.display = hasFile ? "block" : "none";
    }
    
    // 验证必要参数
    if (!title || title.trim() === "") {
    showToast("删除失败：节点信息不完整", 'error');
    console.error("删除失败：标题为空或未定义");
    resetDeleteButton(event, id);
    return;
    }
    
    const numericId = Number(id);
    if (isNaN(numericId)) {
    showToast("无效的节点ID", 'error');
    resetDeleteButton(event, id);
    return;
    }
    
    // 检查DOM元素是否已初始化
    if (!domElements.deleteModal) {
    initDomElements(); // 重新尝试初始化
    }
    
    // 再次检查关键元素
    if (!domElements.deleteModal || !domElements.deleteMessage || !domElements.deleteNodeTitle) {
    showToast("删除功能初始化失败，请刷新页面重试", 'error');
    console.error("删除确认框关键元素缺失");
    resetDeleteButton(event, id);
    return;
    }
    
    // 获取并禁用删除按钮
    let deleteButton = getDeleteButton(event, numericId);
    if (deleteButton) {
    deleteButton.disabled = true;
    deleteButton.innerHTML = '<i class="fas fa-spinner fa-spin"></i> 检查中';
    }
    
    try {
    // 检查是否有子节点
    const childrenExist = await hasChildren(numericId);
    
    if (childrenExist) {
        // 有子节点，不允许删除
        showToast("该节点包含子节点，无法删除", 'error');
    } else {
        // 没有子节点，可以删除，显示确认框
        deletingId = numericId;
        domElements.deleteNodeTitle.textContent = title;
        domElements.deleteMessage.textContent = "确定删除以下节点吗？";
        domElements.deleteModal.classList.add("show");
    }
    } catch (e) {
    showToast(`删除操作失败: ${e.message}`, 'error');
    console.error("删除检查错误:", e);
    } finally {
    // 恢复按钮状态
    resetDeleteButton(event, numericId);
    }
}

// 辅助函数：获取删除按钮
function getDeleteButton(event, nodeId) {
    if (event && event.currentTarget) {
    return event.currentTarget;
    }
    return document.getElementById(`delete-btn-${nodeId}`);
}

// 辅助函数：重置删除按钮状态
function resetDeleteButton(event, nodeId) {
    const deleteButton = getDeleteButton(event, nodeId);
    if (deleteButton && typeof deleteButton.disabled !== 'undefined') {
    deleteButton.disabled = false;
    deleteButton.innerHTML = '<i class="fas fa-trash"></i> 删除';
    }
}

// 确认删除
async function confirmDelete() {
    if (deletingId === null) return;
    
    // 确保模态框元素存在
    if (!domElements.deleteModal) {
    showToast("删除功能异常，请刷新页面重试", 'error');
    return;
    }
    
    try {
    await request("/delete_note", { id: deletingId });
    domElements.deleteModal.classList.remove("show");
    resetForm();
    load(currentPid);
    showToast('节点已删除', 'success');
    } catch (e) {
    showToast('删除失败', 'error');
    }
}

// 关闭删除模态框
function closeDeleteModal() {
    if (domElements.deleteModal) {
    domElements.deleteModal.classList.remove("show");
    }
}

// ===== 节点迁移功能 =====

// 开始迁移模式：记录节点信息，显示迁移栏
function startMoveNode(id, title, content) {
    // content 经过 encodeURIComponent(escapeHtml()) 双重编码，需还原
    const decodedContent = htmlUnescape(decodeURIComponent(content));
    movingNode = { id: id, title: title, content: decodedContent };
    const moveBar = document.getElementById('move-mode-bar');
    const moveTitle = document.getElementById('moving-node-title');
    if (moveBar && moveTitle) {
    moveTitle.textContent = title;
    moveBar.style.display = 'flex';
    }
    // 重新渲染当前节点列表（隐藏被迁移的节点，其余节点去掉移动按钮）
    renderNodes(lastLoadedNodes);
    showToast(`已选择节点「${title}」，请导航到目标位置后点击「移动到此」`, 'info');
}

// 确认移动：将节点移到当前目录
async function confirmMoveHere() {
    if (!movingNode) return;
    
    try {
    // 防止移入自身
    if (movingNode.id === currentPid) {
        showToast('不能将节点移到自身内部', 'error');
        return;
    }
    
    // 后端 update_note 要求 title 和 content 非空，保留原始内容
    const title = movingNode.title;
    let content = movingNode.content;
    if (!content) content = ' '; // 空内容时使用空格占位
    
    await request("/update_note", {
        id: movingNode.id,
        pid: currentPid,
        title: title,
        content: content
    });
    
    showToast(`节点「${title}」已移动到当前位置`, 'success');
    cancelMove();
    load(currentPid);
    } catch (e) {
    showToast(`移动失败: ${e.message || e}`, 'error');
    console.error("移动节点错误:", e);
    }
}

// 取消迁移模式
function cancelMove() {
    movingNode = null;
    const moveBar = document.getElementById('move-mode-bar');
    if (moveBar) moveBar.style.display = 'none';
    renderNodes(lastLoadedNodes);
}

// 处理文件上传并转换为Base64（完全在后台处理，不显示编码）
function handleFileUpload(files) {
    if (!files || files.length === 0) return;
    
    const file = files[0];
    currentFileInfo.name = file.name;
    currentFileInfo.size = file.size;
    currentFileInfo.uploadTime = new Date().toLocaleString();
    currentFileInfo.metadataError = false;
    
    // 更新文件信息显示
    if (domElements.fileInfo) {
    domElements.fileInfo.textContent = `正在处理: ${file.name} (${formatFileSize(file.size)})`;
    }
    
    // 隐藏修复提示
    if (domElements.dataRepairHint) {
    domElements.dataRepairHint.style.display = "none";
    }
    
    // 显示进度条
    if (domElements.fileProgress) {
    domElements.fileProgress.style.display = "block";
    }
    if (domElements.fileProgressBar) {
    domElements.fileProgressBar.style.width = "0%";
    }
    
    const reader = new FileReader();
    
    // 进度更新
    reader.onprogress = function(e) {
    if (e.lengthComputable && domElements.fileProgressBar) {
        const percent = (e.loaded / e.total) * 100;
        domElements.fileProgressBar.style.width = percent + "%";
    }
    };
    
    // 读取完成（编码在后台处理，不向用户展示）
    reader.onload = function(e) {
    try {
        // 提取Base64数据部分（完全在后台处理）
        const base64String = e.target.result.split(',')[1];
        currentFileInfo.base64 = base64String;
        
        // 将Base64字符串放入隐藏字段（用户不可见）
        if (domElements.hiddenBase64) {
        domElements.hiddenBase64.value = base64String;
        }
        
        // 保存文件元数据，使用安全的JSON序列化
        const metadata = {
        name: currentFileInfo.name,
        size: currentFileInfo.size,
        uploadTime: currentFileInfo.uploadTime
        };
        
        if (domElements.fileMetadata) {
        domElements.fileMetadata.value = safeJsonStringify(metadata);
        }
        
        // 更新标题（如果为空）
        const nodeTitleEl = document.getElementById("node-title");
        if (nodeTitleEl && !nodeTitleEl.value.trim()) {
        nodeTitleEl.value = file.name;
        }
        
        // 重置错误样式
        if (domElements.fileMetaInfo) {
        domElements.fileMetaInfo.className = "file-meta-info";
        }
        
        // 只显示文件元信息，不显示编码
        if (domElements.fileMetaContainer) domElements.fileMetaContainer.style.display = "block";
        if (domElements.metaFilename) domElements.metaFilename.textContent = currentFileInfo.name;
        if (domElements.metaFilesize) domElements.metaFilesize.textContent = formatFileSize(currentFileInfo.size);
        if (domElements.metaUploadtime) domElements.metaUploadtime.textContent = currentFileInfo.uploadTime;
        
        // 显示下载区域
        if (domElements.downloadSection) domElements.downloadSection.style.display = "block";
        
        // 更新状态信息
        if (domElements.fileInfo) {
        domElements.fileInfo.textContent = `已完成: ${file.name} 已上传`;
        }
        
        showToast(`文件 "${file.name}" 已上传`, 'success');
    } catch (error) {
        showToast(`文件处理失败: ${error.message}`, 'error');
        console.error("文件处理错误:", error);
    }
    };
    
    // 读取失败
    reader.onerror = function() {
    showToast("文件读取失败，请重试", 'error');
    if (domElements.fileInfo) {
        domElements.fileInfo.textContent = `读取失败: ${file.name}`;
    }
    };
    
    // 读取文件并转换为DataURL（包含Base64，完全在后台处理）
    reader.readAsDataURL(file);
}

// 移除已上传的文件
function removeFile() {
    currentFileInfo = { name: "", size: 0, base64: "", uploadTime: "", metadataError: false };
    
    if (domElements.fileUpload) domElements.fileUpload.value = "";
    if (domElements.fileInfo) domElements.fileInfo.textContent = "未选择文件";
    if (domElements.fileMetaContainer) domElements.fileMetaContainer.style.display = "none";
    if (domElements.fileMetaInfo) {
    domElements.fileMetaInfo.className = "file-meta-info"; // 重置样式
    }
    if (domElements.dataRepairHint) domElements.dataRepairHint.style.display = "none";
    if (domElements.fileProgress) domElements.fileProgress.style.display = "none";
    if (domElements.fileProgressBar) domElements.fileProgressBar.style.width = "0%";
    if (domElements.hiddenBase64) domElements.hiddenBase64.value = "";
    if (domElements.fileMetadata) domElements.fileMetadata.value = "";
    if (domElements.downloadSection) domElements.downloadSection.style.display = "none";
    
    showToast("文件已移除", 'info');
}

// 将Base64内容解码并下载为文件（完全在后台处理解码）
function decodeAndDownload(nodeId, fileName, isEditing = false) {
    let base64String, name, metadataError = false;
    
    // 判断是从列表下载还是编辑模式下载
    if (isEditing) {
    // 编辑模式，使用当前表单中的文件数据（后台处理）
    base64String = domElements.hiddenBase64?.value.trim();
    name = currentFileInfo.name;
    metadataError = currentFileInfo.metadataError;
    } else if (nodeId !== null && nodeId !== undefined) {
    // 列表模式，使用缓存的文件数据（后台处理）
    const fileData = nodeFileCache[nodeId];
    if (!fileData) {
        showToast("文件数据不存在或已过期", 'error');
        return;
    }
    base64String = fileData.base64;
    name = (fileName ? htmlUnescape(decodeURIComponent(fileName)) : null) || fileData.name;
    metadataError = fileData.metadataError;
    } else {
    showToast("无法确定下载来源", 'error');
    return;
    }
    
    if (!base64String) {
    showToast("没有可解码的文件数据", 'error');
    return;
    }
    
    if (!name) {
    showToast("文件信息缺失，无法下载", 'error');
    return;
    }
    
    // 如果元数据有错误，提前警告用户
    if (metadataError) {
    if (!confirm('此文件的元数据可能已损坏，下载的文件可能无法正常使用。是否继续？')) {
        return;
    }
    }
    
    try {
    // 检查是否是有效的Base64（后台处理）
    if (!isBase64(base64String)) {
        throw new Error("文件数据损坏，无法下载");
    }
    
    // 解码Base64（完全在后台处理，用户不可见）
    const byteCharacters = atob(base64String);
    const byteNumbers = new Array(byteCharacters.length);
    
    for (let i = 0; i < byteCharacters.length; i++) {
        byteNumbers[i] = byteCharacters.charCodeAt(i);
    }
    
    const byteArray = new Uint8Array(byteNumbers);
    const blob = new Blob([byteArray], { type: 'application/octet-stream' });
    
    // 创建下载链接
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = name;
    document.body.appendChild(a);
    a.click();
    
    // 清理
    setTimeout(() => {
        document.body.removeChild(a);
        URL.revokeObjectURL(url);
    }, 0);
    
    showToast(`文件 "${name}" 已下载`, 'success');
    } catch (error) {
    showToast(`下载失败: ${error.message}`, 'error');
    console.error("文件下载错误:", error);
    }
}

// 检查字符串是否为有效的Base64（后台处理）
function isBase64(str) {
    try {
    return btoa(atob(str)) === str;
    } catch (err) {
    return false;
    }
}

// 格式化文件大小显示
function formatFileSize(bytes) {
    if (bytes === 0) return '0 Bytes';
    const k = 1024;
    const sizes = ['Bytes', 'KB', 'MB', 'GB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
}

// 添加拖拽文件支持
function initDragAndDrop() {
    const uploadArea = document.querySelector('.file-upload-area');
    if (!uploadArea) return;
    
    uploadArea.addEventListener('dragover', (e) => {
    e.preventDefault();
    uploadArea.style.borderColor = '#888';
    uploadArea.style.backgroundColor = '#f0f0f0';
    });
    
    uploadArea.addEventListener('dragleave', () => {
    uploadArea.style.borderColor = '#ccc';
    uploadArea.style.backgroundColor = 'transparent';
    });
    
    uploadArea.addEventListener('drop', (e) => {
    e.preventDefault();
    uploadArea.style.borderColor = '#ccc';
    uploadArea.style.backgroundColor = 'transparent';
    
    if (e.dataTransfer.files.length) {
        handleFileUpload(e.dataTransfer.files);
    }
    });
}

// 初始化
document.addEventListener('DOMContentLoaded', () => {
    // 页面加载时初始化DOM元素引用
    initDomElements();
    initDragAndDrop();
    load();
    showToast('系统就绪', 'success');
    
    // 键盘事件
    document.addEventListener('keydown', e => {
    if (e.ctrlKey && e.key === 'n') {
        e.preventDefault();
        resetForm();
    } else if (e.ctrlKey && e.key === 's') {
        e.preventDefault();
        saveNode();
    } else if (e.ctrlKey && e.key === 'b') {
        e.preventDefault();
        goBack();
    } else if (e.key === 'Escape') {
        if (movingNode) {
        cancelMove();
        showToast('已取消移动', 'info');
        } else {
        closeDeleteModal();
        }
    }
    });
});