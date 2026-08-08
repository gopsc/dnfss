/**
 * Token 生命周期管理器
 * - 定时静默刷新：在 Token 80% 生命周期时提前调 /refresh_token
 * - 全局 fetch 拦截器：收到 401 先尝试刷新一次，成功则重试原请求，失败则原样返回 401
 * - 依赖：gateway POST /refresh_token（Cookie q4_token 自动携带）
 * - 无侵入：与 dnfs.js 完全解耦
 */

let _tokenExpireAt = null;
let _refreshTimer = null;

const REFRESH_ADVANCE = 0.8;       // 在 80% 生命周期时提前刷新
const REFRESH_MIN_GAP = 60;        // 两次刷新最少间隔（秒）
const RETRY_COOLDOWN = 15;         // 刷新失败后冷却期（秒）

/* ---------- 过期时间估算：通过 /whoami 获取 iat + keep_time ---------- */

async function estimateExpiry() {
    try {
        const res = await _rawFetch('/whoami', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: '{}'
        });
        if (!res.ok) return false;
        const data = await res.json();
        if (!data.login) return false;

        // 优先 created_at（刷新时更新），回退 iat（兼容旧 Token）
        // 网关以 created_at + keep_time 判定令牌有效性，iat 刷新后不变，不能用于估算过期
        const issueStr = data.created_at || data.iat;
        const keepTime = data.keep_time;
        if (!issueStr || !keepTime) return false;

        const issueDate = new Date(issueStr.replace(' ', 'T') + 'Z');
        if (isNaN(issueDate.getTime())) return false;

        _tokenExpireAt = issueDate.getTime() + keepTime * 1000;
        scheduleRefresh();
        return true;
    } catch (e) {
        console.warn('TokenManager: 过期时间估算失败', e.message);
        return false;
    }
}

/* ---------- 定时刷新 ---------- */

function scheduleRefresh() {
    clearTimeout(_refreshTimer);
    if (!_tokenExpireAt) return;

    const remainingSec = (_tokenExpireAt - Date.now()) / 1000;
    if (remainingSec <= 0) {
        // Token 已过期，立即尝试刷新
        tryRefresh();
        return;
    }

    // 80% 位置触发，但至少等 REFRESH_MIN_GAP 秒
    const delaySec = Math.max(remainingSec * (1 - REFRESH_ADVANCE), REFRESH_MIN_GAP);
    _refreshTimer = setTimeout(tryRefresh, delaySec * 1000);
}

async function tryRefresh() {
    try {
        const res = await _rawFetch('/refresh_token', { method: 'POST' });
        if (!res.ok) {
            const data = await res.json().catch(() => ({}));
            if (data.message) console.warn('TokenManager: 刷新被拒 -', data.message);
            // 冷却后重新估算
            _refreshTimer = setTimeout(() => estimateExpiry(), RETRY_COOLDOWN * 1000);
            return;
        }
        const data = await res.json();
        if (data.success && data.data) {
            console.log(`TokenManager: 已刷新 (第${data.data.refresh_count}次, 剩余${data.data.refreshes_remaining}次)`);
        }
        await estimateExpiry();
    } catch (e) {
        console.warn('TokenManager: 刷新网络异常', e.message);
        _refreshTimer = setTimeout(() => estimateExpiry(), RETRY_COOLDOWN * 1000);
    }
}

/* ---------- 全局 fetch 401 拦截器 ---------- */

const _rawFetch = window.fetch;

window.fetch = async function (url, options) {
    let resp = await _rawFetch(url, options);

    // 仅处理 401；/refresh_token 自身不重试防循环
    if (resp.status !== 401 || (typeof url === 'string' && url.includes('/refresh_token'))) {
        return resp;
    }

    try {
        const refreshRes = await _rawFetch('/refresh_token', { method: 'POST' });
        if (!refreshRes.ok) return resp;

        const data = await refreshRes.json();
        if (!data.success) return resp;

        console.log('TokenManager: 401 拦截 → 刷新成功，重试原请求');
        await estimateExpiry();

        // Cookie 已由 gateway Set-Cookie 自动更新，直接重试
        return await _rawFetch(url, options);
    } catch (e) {
        console.warn('TokenManager: 401 刷新异常', e.message);
    }

    return resp;
};

/* ---------- 启动 ---------- */

estimateExpiry();
