# 多微信用户模式设计方案

## 背景
当前 AI 女友系统是"一个管理端对应一个用户"的设计，资源浪费且需重复部署。需要改为单实例服务多个微信用户。

## 核心架构

新增 **GirlfriendManager（女友管理器）**，作为多用户核心调度层：

```
微信消息(from_user=小明)
    → WeChatConnector（只做转发）
    → GirlfriendManager（按 user_id 分配女友实例）
    → Orchestrator（按 user_id 隔离处理）
```

### 共享组件（所有用户共用）
- LLM 大模型网关
- 安全过滤层（内容安全、PII、注入检测）
- 工具系统（天气/搜索/日历等）
- RAG 公共知识库
- TTS 语音合成

### 独立组件（每个用户独有）
- 聊天记忆（按 user_id 过滤 sqlite + chroma_db）
- 情感状态（独立的 EmotionEngine state）
- 角色卡/人设（可独立分配）
- 主动消息引擎（独立判断时机）

## 后端改造

### 新增 girlfriend_manager.py
- GirlfriendManager 类
- 管理用户 → 女友实例的映射
- 懒加载创建实例
- 提供统一的 process_message 入口

### 改造 connector.py
- WeChatConnector 消息发给 GirlfriendManager
- 不再直接持有 Orchestrator

### 改造 emotion_engine.py
- 支持多用户心情隔离（内部 dict 按 user_id 存储）

### 改造 memory 层
- 所有 sqlite 查询加 WHERE user_id = ?
- chroma_db metadata 过滤

### 改造 rest_api.py
- 新增 /api/users/* 接口
- 用户列表、详情、聊天记录、情感状态、角色分配、重置

## 前端改造

### 新增页面
- 用户管理列表页：查看所有活跃用户
- 用户详情页：聊天记录 + 情感 + 角色分配

### API 接口
- GET /api/users
- GET /api/users/{user_id}
- GET /api/users/{user_id}/chat
- GET /api/users/{user_id}/emotion
- POST /api/users/{user_id}/role
- POST /api/users/{user_id}/reset
