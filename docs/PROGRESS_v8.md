# v8（2.0 智能预测 + 官方接口适配器）进度
时间均为北京时间（UTC+8）。

- [ ] 智能预测升级（季节性 / 弹性 / 买入概率与置信区间）
- [ ] 官方接口适配器框架 + 内置模板 + mock 试跑
- [ ] 渲染池配置、浏览器扩展包、pgvector 可选
- [ ] UI / e2e / 截图 screenshots/v8/
- [ ] PRD/ARCHITECTURE 2.0 与 PDF；分批测试；重启 :8800；重打 Linux 包
- [x] 2026-10-22 01:20 智能预测：forecast_v2（Prophet→STL+ARIMA→baseline）、弹性粗估、买入概率与 90% CI；advice/forecast API 已接
- [x] 2026-10-22 01:25 官方适配器框架 OfficialApiAdapter + 7 个内置模板（淘宝为 stub）；凭证 Fernet 加密；mock_official_api；API /api/official/*
- [x] 2026-10-22 01:28 渲染池 workers/queue 可配；Chrome/Edge MV3 扩展 zip；pgvector 可选后端接口
- [x] 2026-10-22 01:22 test_v8_forecast + test_v8_official 5 passed；PRD/ARCHITECTURE 2.0 章节已写
- [x] 2026-10-22 01:30 UI e2e 通过，截图 screenshots/v8/01-03
