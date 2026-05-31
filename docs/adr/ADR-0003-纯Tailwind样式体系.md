# ADR-0003：采用纯 Tailwind CSS 样式体系

## 状态
Accepted

## 上下文
前端管理台 UI 存在两套样式体系并行：Tailwind CSS（主力）和自定义 CSS 类（`index.css` 中 maze-* 前缀的约 136 行）。两套并行导致：
- 样式来源不明确，改样式得查两个地方
- 新增组件不知道该用哪个体系的类
- CSS 文件体积膨胀

## 决策
删除所有 maze-* 自定义 CSS 类，全项目只使用 Tailwind。如果 Tailwind 无法表达的设计，直接用内联 style 或提取为 Tailwind 组件类（`@apply`）。

## 影响
- 正面：样式来源唯一，新人不用猜
- 正面：减少 CSS 文件体积约 136 行
- 代价：部分旧组件需切换到 Tailwind 语法

## 对应 Fitness Function
FF-0004：CI 中检查 `index.css` 不包含 `maze-` 前缀的类定义
