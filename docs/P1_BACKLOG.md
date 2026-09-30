# P1 Backlog — 唯一的你

**Updated**: 2026-09-29（文档清理批：已解决项与历史 Previous 链全部移除，本文件只载真实未决项；历史处置记录见 git 历史与 `LOG.md`）

## 产品决策（待默默拍板）

1. **[MM-V3] 表情识别（人脸）**：真空缺——`multimodal/` 仅通用视觉（VisionHandler 走 LLM `image_url`），非表情识别模型。需调研+选型；敏感生物特征，本地优先、不落盘原图。
2. **[MM-V4] 语音声学情绪**：真空缺——`voice/` 全是 TTS，ASRHandler 只取文本，声学信息被丢。需调研+选型。
3. **[Q4] 生物特征采集是否启用**：合规方向已调研（端侧只出特征、不上传原图），产品内启用与否归默默裁决。
4. **[OBS-2] HTTPS 未启用**：裸 IP 无法签发 certbot 证书；绑定域名后按 nginx conf 注释走 certbot。暂缓中（域名+证书费用）。
5. **[P1-11] GitHub 仓库名**：`FOURTEEN1416/fourteen.git` vs 项目名 `ai-girlfriend`，可选改名，需默默决策。

## 环境 / 运维

6. **[OBS-3] systemd 服务以 root 运行**：生产 service `User=root`（模板基线 `www-data`）。改运行用户涉及文件权限迁移，需停机窗口规划。

## 低优先级

7. **[P1-9] Proxy**：`http://127.0.0.1:7897` 在 `git push` 时偶发 connection reset（本地网络环境，非代码）。
8. **[P1-13] AGENTS.md PAT 认证**：与 opencode 内置认证可能冗余。
9. **[FE-ENV-1] Lighthouse 本机不可用**：headless Chrome 无法提交帧（NO_FCP），环境约束；性能验证固定改用 CDP `Performance.getMetrics` + 真机网络清单口径。

> 10/11 两项（[PY-DEAD-1] `ASEEngine.get_state()` 死码 / [FE-TYPE-1] `ProactiveEngineState` 前端死链）已于 2026-09-30 遗留待办批执行整删销账，见 `DELETION_LOG.md` 同日节。
