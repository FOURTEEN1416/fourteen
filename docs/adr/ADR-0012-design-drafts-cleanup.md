# ADR-0012: 前端设计稿清除，代码即标准

**状态**：已采纳 ✅
**日期**：2026-05-30

## 背景

项目存在早期设计稿（v2/, concepts-overview.html, .superpowers/brainstorm/），已与实际代码严重脱节。原策略是「禁止删除」，导致设计债累积。

## 决策

删除所有设计稿文件，前端设计以当前代码为准。

## 影响

- 删除 v2/ (index.html + screenshot.png)
- 删除 concepts-overview.html
- 删除 .superpowers/brainstorm/
- MAP.md 和 COMPASS.md 更新：移除设计稿引用
