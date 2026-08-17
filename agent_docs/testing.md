# v1.0 测试契约

## 标准门禁

```bash
pnpm lint
pnpm typecheck
pnpm test
pnpm contracts:check
pnpm test:storybook
pnpm test:browser
pnpm build
```

数据库集成和真实认证浏览器需要独立 `TEST_DATABASE_URL`；专项 Playwright 使用 `node scripts/run-playwright.mjs test ...`。

## 风险覆盖

- Core：MatchActor 状态机、权限、幂等、事务、并发席位、暂停/恢复、过期回调。
- 实时语音：ASR task 轮换、TTS Opus 解码/播放、取消、一次重试和二次失败。
- Web：登录回跳、房间创建/加入、席位、设备准备、比赛控制、文字记录、赛后和后台权限。
- 数据：空库升级、已有数据升级、导出权限、日志脱敏和任务幂等。

## 证据规则

测试通过不等于真人体验验收通过。180 秒真人 ASR、11 音色盲听、五场并发和 50 路 LLM 必须有真实环境证据；未执行时明确标记为待验收。
