"""
AI Review 测试页面

启动方式: streamlit run ai_review/test_app.py
"""

import streamlit as st
import requests

API = "http://127.0.0.1:8007"

st.set_page_config(page_title="AI Review 测试", layout="wide")
st.title("AI Review 审核测试")

# ── 左侧：审核区 ──
left, right = st.columns([2, 1])

with left:
    st.subheader("内容审核")

    content = st.text_area("输入待审核文本", height=150,
                            value=st.session_state.get("input_text", ""),
                            key="content")

    if st.button("提交审核", type="primary", use_container_width=True):
        if not content.strip():
            st.warning("请输入内容")
        else:
            resp = requests.post(f"{API}/review", json={"content": content})
            if resp.ok:
                data = resp.json()
                st.session_state["last_result"] = data
                st.session_state["last_content"] = content
            else:
                st.error(f"请求失败: {resp.text}")

    # 显示审核结果
    if "last_result" in st.session_state:
        result = st.session_state["last_result"]
        invalid = result.get("invalid") in (True, "true")

        st.divider()
        if invalid:
            st.error(f"审核结果: 违规")
        else:
            st.success(f"审核结果: 通过")

        with st.expander("原始响应"):
            st.json(result)

        # 人工纠偏
        st.divider()
        st.subheader("人工纠偏")

        if invalid:
            st.caption("AI 判违规但人工认为正常？点击下方按钮纠正。")
            if st.button("人工纠正为正常", type="secondary"):
                correct = st.session_state["last_content"]
                resp = requests.post(
                    f"{API}/rag/feedback",
                    json={"content": correct, "correct_type": "safe"},
                )
                if resp.ok:
                    fb = resp.json()
                    if fb["added"]:
                        st.toast("已写入 RAG 库")
                    else:
                        st.toast("已存在，跳过")
        else:
            st.caption("AI 放行了但人工发现违规？点击下方按钮纠正。")
            if st.button("人工纠正为违规", type="secondary"):
                correct = st.session_state["last_content"]
                resp = requests.post(
                    f"{API}/rag/feedback",
                    json={"content": correct, "correct_type": "violation"},
                )
                if resp.ok:
                    fb = resp.json()
                    if fb["added"]:
                        st.toast("已写入 RAG 库")
                    else:
                        st.toast("已存在，跳过")

# ── 右侧：快捷用例 ──
with right:
    st.subheader("快捷用例")

    test_cases = {
        "违规-色情服务": "加我微信xxxxx，裸聊视频一对一服务，学生妹上门",
        "违规-赌博": "真人百家乐在线投注，包赢不赔，注册送彩金",
        "违规-假证": "专业办理身份证驾驶证，高仿毕业证，货到付款",
        "违规-药品": "迷奸水听话水货到付款，保密发货",
        "安全-日常": "今天天气真好，适合出门散步",
        "安全-技术": "Python的装饰器本质上是一个高阶函数",
        "安全-娱乐": "请问最近有什么好看的电影推荐吗",
        "安全-学习": "机器学习入门需要先掌握线性代数和概率论",
    }

    for label, text in test_cases.items():
        if st.button(label, use_container_width=True):
            st.session_state["input_text"] = text
            st.rerun()

    st.divider()
    st.subheader("服务状态")
    try:
        resp = requests.get(f"{API}/health")
        if resp.ok:
            st.success("服务在线")
        else:
            st.error("服务异常")
    except Exception:
        st.error("无法连接服务")

# ── 底部：RAG 知识库 ──
st.divider()
st.subheader("RAG 知识库")

if st.button("刷新知识库", type="secondary"):
    st.rerun()

try:
    resp = requests.get(f"{API}/rag/list")
    if resp.ok:
        data = resp.json()
        entries = data.get("entries", [])
        total = data.get("total", 0)

        if total == 0:
            st.info("知识库为空")
        else:
            violation_count = sum(1 for e in entries if e["type"] == "violation")
            safe_count = sum(1 for e in entries if e["type"] == "safe")

            col1, col2, col3 = st.columns(3)
            col1.metric("总条目", total)
            col2.metric("违规样本", violation_count)
            col3.metric("安全样本", safe_count)

            rows = []
            for e in entries:
                src = "预设" if e["category"] != "human_feedback" else "人工反馈"
                tag = "违规" if e["type"] == "violation" else "安全"
                rows.append({
                    "标签": tag,
                    "来源": src,
                    "分类": e["category"],
                    "ID": e["id"],
                    "内容": e["text"][:80] + ("..." if len(e["text"]) > 80 else ""),
                })

            st.dataframe(
                rows,
                use_container_width=True,
                column_config={
                    "标签": st.column_config.Column(width="small"),
                    "来源": st.column_config.Column(width="small"),
                    "分类": st.column_config.Column(width="medium"),
                    "ID": st.column_config.Column(width="small"),
                    "内容": st.column_config.Column(width="large"),
                },
            )
    else:
        st.error("无法获取知识库数据")
except Exception as e:
    st.error(f"请求失败: {e}")
