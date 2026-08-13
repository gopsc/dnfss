# 任务管理能力

## 可用工具
- **memo**: 任务管理

## 操作方法
- 添加任务：`memo action="add" title="任务标题" content="任务内容" reminder_time="+1h"`
- 查看任务：`memo action="list"`
- 完成任务：`memo action="complete" memo_id=1`
- 搜索任务：`memo action="search" keyword="关键词"`
- 任务统计：`memo action="stats"`

## 重复任务
- daily: 每天重复
- weekly: 每周重复
- monthly: 每月重复
- custom: 自定义间隔（需要指定 repeat_interval 和 repeat_interval_unit）

## 即时任务
设置 `is_immediate=true` 可以立即执行任务
