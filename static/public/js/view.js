// ===== 路径解析：从 URL 取当前目录（去除首尾斜杠） =====
const currentPath = decodeURIComponent(window.location.pathname).replace(/^\/+|\/+$/g, '');

// ===== DOM 引用（id 与原版本完全一致，保持后端/前端契约不变） =====
const fileList = document.getElementById("fileList");
const loading = document.getElementById("loading");
const empty = document.getElementById("empty");
const searchInput = document.getElementById("searchInput");
const totalItems = document.getElementById("totalItems");
const totalDirs = document.getElementById("totalDirs");
const totalFiles = document.getElementById("totalFiles");
const totalVideos = document.getElementById("totalVideos");
const totalAudios = document.getElementById("totalAudios");
const btnRefresh = document.getElementById("btnRefresh");
const breadcrumb = document.getElementById("breadcrumb");

// ===== 状态 =====
let items = [];
let searchQuery = "";

// ===== 工具函数 =====
const SIZE_UNITS = ["B", "KB", "MB", "GB"];
function formatSize(bytes) {
    if (bytes === 0) return "0B";
    const i = Math.floor(Math.log(bytes) / Math.log(1024));
    return (bytes / Math.pow(1024, i)).toFixed(1) + SIZE_UNITS[i];
}

const FILE_ICONS = {
    pdf: "📄", doc: "📝", docx: "📝", xls: "📊", xlsx: "📊",
    zip: "📦", rar: "📦", "7z": "📦",
    jpg: "🖼️", jpeg: "🖼️", png: "🖼️", gif: "🖼️",
    txt: "📃", json: "📋", html: "🌐", css: "🎨", js: "⚡"
};
function getIcon(item) {
    if (item.type === "dir") return "📁";
    if (item.type === "video") return "🎬";
    if (item.type === "audio") return "🎵";
    const ext = item.name.split(".").pop().toLowerCase();
    return FILE_ICONS[ext] || "📄";
}

// 转义文本，防止文件名含特殊字符导致 XSS 或模板断裂
function esc(str) {
    return String(str)
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#39;");
}

// ===== 导航行为（与原版本一致） =====
function navigate(path, type) {
    if (type === "dir") window.location.href = "/" + path;
    else if (type === "video" || type === "audio") window.location.href = "/video/" + path;
    else if (/\.html?$/i.test(path)) window.open("/" + path, "_blank");
    else window.location.href = "/" + path;
}
function playMedia(path) { window.location.href = "/video/" + path; }
function downloadFile(path) { window.location.href = "/" + path + "?download=true"; }

// ===== 面包屑 =====
function renderBreadcrumb() {
    const parts = currentPath ? currentPath.split('/') : [];
    const frags = ['<a href="/">根目录</a>'];
    let acc = '';
    parts.forEach((part, i) => {
        if (i === parts.length - 1) {
            frags.push(`<span class="sep">›</span><span class="current">${esc(part)}</span>`);
        } else {
            acc += (i === 0 ? '' : '/') + part;
            frags.push(`<span class="sep">›</span><a href="/${encodeURIComponent(acc)}/">${esc(part)}</a>`);
        }
    });
    breadcrumb.innerHTML = frags.join('');
}

// ===== 列表渲染（使用转义，避免文件名注入） =====
function render() {
    const q = searchQuery.toLowerCase();
    const filtered = searchQuery
        ? items.filter(i => i.name.toLowerCase().includes(q))
        : items;

    totalItems.textContent = filtered.length;
    totalDirs.textContent   = filtered.filter(i => i.type === "dir").length;
    totalFiles.textContent  = filtered.filter(i => i.type === "file").length;
    totalVideos.textContent = filtered.filter(i => i.type === "video").length;
    totalAudios.textContent = filtered.filter(i => i.type === "audio").length;

    if (filtered.length === 0) {
        fileList.innerHTML = "";
        empty.style.display = "block";
        loading.style.display = "none";
        return;
    }
    empty.style.display = "none";
    loading.style.display = "none";

    fileList.innerHTML = filtered.map(item => {
        const path = esc(item.path);
        const type = esc(item.type);
        const name = esc(item.name);
        const sizeOrDir = item.type !== "dir"
            ? `<span>${formatSize(item.size)}</span>`
            : '<span>📁 文件夹</span>';
        const playBtn = (item.type === "video" || item.type === "audio")
            ? `<button class="btn-play" onclick="event.stopPropagation(); playMedia('${path}')">▶</button>`
            : '';
        return `
            <div class="item" onclick="navigate('${path}', '${type}')">
                <span class="icon">${getIcon(item)}</span>
                <div class="info">
                    <div class="name">${name}</div>
                    <div class="meta">
                        ${sizeOrDir}
                        <span>${esc(item.modified)}</span>
                    </div>
                </div>
                <div class="actions">
                    ${playBtn}
                    <button class="btn-download" onclick="event.stopPropagation(); downloadFile('${path}')">⬇</button>
                </div>
            </div>`;
    }).join("");
}

// ===== 数据拉取（接口与参数与原版本一致） =====
let isLoading = false;	// 防重复点击：请求进行中忽略再次触发
async function loadData() {
    if (isLoading) return;
    isLoading = true;
    loading.style.display = "block";
    fileList.innerHTML = "";
    empty.style.display = "none";

    try {
        const url = `/api/filelist?path=${encodeURIComponent(currentPath)}`;
        const res = await fetch(url);
        const responseText = await res.text();
        if (window.WafTip && WafTip.isBlocked(res, responseText)) throw new Error(WafTip.MESSAGE);
        const data = JSON.parse(responseText);
        if (data.success) {
            items = data.items || [];
            render();
        } else {
            throw new Error(data.error || "加载失败");
        }
    } catch (e) {
        fileList.innerHTML = `<div class="error">❌ 加载失败: ${esc(e.message)}</div>`;
        loading.style.display = "none";
    } finally {
        isLoading = false;
    }
}

// ===== 事件绑定（快捷键 / 搜索 / 刷新 与原版本一致） =====
let searchTimer;
searchInput.addEventListener("input", function () {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => {
        searchQuery = this.value.trim();
        render();
    }, 200);
});

document.addEventListener("keydown", function (e) {
    if ((e.ctrlKey && e.key === "f") ||
        (e.key === "/" && !e.ctrlKey && !e.metaKey)) {
        e.preventDefault();
        searchInput.focus();
        searchInput.select();
    }
    if (e.key === "Escape") {
        searchInput.value = "";
        searchQuery = "";
        render();
        searchInput.blur();
    }
});

btnRefresh.addEventListener("click", function (e) {
    e.preventDefault();	// <a href="#"> 默认会跳页顶，拦截
    loadData();
});

// ===== 启动 =====
renderBreadcrumb();
loadData();
