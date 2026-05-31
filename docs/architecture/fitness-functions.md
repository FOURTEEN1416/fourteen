# Fitness Functions（架构约束检查）

> FF 是"自动化的架构围墙"——一旦违反，CI 就红。
> 从 3 条开始，逐步增加。

---

## 三层执行

| 层 | 时机 | 耗时 | 失败行为 | 当前状态 |
|----|------|------|----------|---------|
| 实时 | PR gate（GitHub Actions） | <2min | 阻塞合并 | ✅ 已配置 |
| 周期 | 每周 CI 定时 | 不限 | 告警 | 📋 待开 |
| 节奏 | 每季度人工审查 | 半天 | 改进计划 | 📋 待开 |

---

## FF-0001：ADR 目录完整性

**对应 ADR**：ADR-0001
**级别**：实时（PR gate）
**实现方式**：CI 中检查 `docs/adr/` 目录存在且非空

**验证命令**：
```bash
test -d docs/adr && test -n "$(ls -A docs/adr)"
```

---

## FF-0002：ADR 引用完整性

**对应 ADR**：ADR-0001
**级别**：实时（PR gate）
**实现方式**：提取 Supersedes 行，检查目标 ADR 文件是否存在

**规则**：
- Supersedes ADR-N → 必须存在 `docs/adr/ADR-N-*.md`
- 找不到时仅警告（不阻塞 PR，但纳入周报）

---

## FF-0003：前端禁止直接 import 旧 API 文件

**对应 ADR**：ADR-0002（统一 API 全集）
**级别**：实时（PR gate）
**实现方式**：CI job `ff-no-old-api-imports` grep 检查 `pages/` 和 `hooks/`

**验证命令（CI 已自动执行）**：
```bash
FORBIDDEN="from.*'\.\./api/characterApi\|from.*shisiClient\|..."
grep -rn "$FORBIDDEN" frontend/src/pages/ frontend/src/hooks/
```


---

## FF-0004：CSS 中无 maze-* 前缀

**对应 ADR**：ADR-0003
**级别**：实时（PR gate）
**实现方式**：grep 检查 `index.css`

**验证命令**：
```bash
! grep -q "maze-" frontend/src/index.css
```

---

## FF-0005：代码中无 'dark' 主题引用

**对应 ADR**：ADR-0004
**级别**：实时（PR gate）
**实现方式**：grep 检查 `'dark'` 字符串（排除 node_modules）

**验证命令**：
```bash
! grep -rn "theme.*dark\|'dark'" frontend/src/ --include="*.tsx" --include="*.ts"
```

---

## FF-0006：client.ts 无 async function 定义

**对应 ADR**：ADR-0005
**级别**：实时（PR gate）
**实现方式**：CI job `ff-client-ts-no-functions` grep 检查函数定义

**验证命令（CI 已自动执行）**：
```bash
grep -qE "export (async )?function|export const.*=.*\(.*\)" frontend/src/api/client.ts
```

---

## FF-0007：Zustand store 禁止直接调用 API

**对应 ADR**：ADR-0006
**级别**：实时（PR gate）
**实现方式**：CI job `ff-stores-no-api-imports` grep 检查 `stores/` 中 API import

---

## FF-0008：测试覆盖关键路径（规划中）

**对应原则**：设计原则 5
**级别**：周期（每周）
**实现方式**：阈值检查后端测试通过率 ≥ 95%，前端 ≥ 80%（待实现）

---

## CI 中当前激活的 FF

| FF | 检查项 | CI Job | 阻塞？ |
|----|--------|--------|--------|
| FF-0001 | ADR 目录非空 | `adr-integrity` | ✅ 是 |
| FF-0002 | Supersedes 链接有效 | `adr-integrity` | ❌ 仅警告 |
| FF-0003 | 前端无旧 API import | `ff-no-old-api-imports` | ✅ 是 |
| FF-0006 | client.ts 无函数定义 | `ff-client-ts-no-functions` | ✅ 是 |
| FF-0007 | Store 无 API 直接调用 | `ff-stores-no-api-imports` | ✅ 是 |
| backend | pytest 通过 | `backend` | ✅ 是 |
| frontend | bun run build 通过 | `frontend` | ✅ 是 |
| backend-weekly | pytest + coverage 报告 | `backend` (schedule) | 📊 仅报告 |

### 三层执行（更新后）

| 层 | 时机 | 当前状态 |
|----|------|---------|
| 实时 | PR gate（push/PR） | ✅ 7 个 blocking job 已激活 |
| 周期 | 每周一 08:00 UTC | ✅ `schedule` trigger + coverage 报告 |
| 节奏 | 每季度人工审查 | 📋 待建立（见 `docs/architecture/bus-factor.md`） |
