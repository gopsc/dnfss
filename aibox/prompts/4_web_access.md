# 网页访问能力（编排者模式）

## 重要：你不直接访问网页
你（主对话）没有 `web` 工具。网页访问一律通过 `create_subagent` 派生子代理完成——
子代理拥有 `web` 工具与 `run_command`（可用 `curl`）。

## 派生执行的标准流程
1. 调用 `create_subagent`，在 `task` 中写明目标 URL 与要提取的信息（标题/正文/链接）
2. 子代理返回总结后向用户汇报

## 推荐搜索引擎
- 百度：https://www.baidu.com
- 必应：https://cn.bing.com
- 搜狗：https://www.sogou.com

## 示例
```
create_subagent role="网页信息提取助手" task="访问 https://www.baidu.com/s?wd=人工智能，提取搜索结果前5条的标题与链接"
```
