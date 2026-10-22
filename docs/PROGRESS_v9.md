# v9（2.0 收尾：预测可信度 + 官方适配器健壮性）进度
时间均为北京时间（UTC+8）。本轮只做不依赖真实第三方密钥的部分。

- [x] 2026-10-22 预测回测：`app/domains/analysis/backtest.py`，滚动起点回测（默认 4 个窗口 × 7 天），
      比较 维持现价(基准) / baseline / STL+ARIMA，输出 MAPE、MAE、RMSE、90% 区间实际覆盖率、最优引擎、相对基准提升、可靠度（高/中/低）。
      历史不足时返回明确原因。API：`GET /api/products/{pid}/backtest?horizon=&folds=`。
- [x] 商品详情「买入时机建议」里加「回测预测准确度」按钮与结果表（最优引擎加星标）。
- [x] 官方适配器健壮性：429/5xx/网络错误指数退避重试，遵守 Retry-After（上限 30 秒）；4xx 不重试；
      按适配器的熔断器（连续失败达阈值后冷却，期间不再发请求）。可配置 max_retries / backoff_base / breaker_threshold / breaker_cooldown。
      API：`GET /api/official/health`、`POST /api/official/adapters/{id}/reset_breaker`。这是为了尊重对方限流，不做任何反爬绕过。
- [x] mock_official_api 增加 /flaky 端点；新测试 tests/test_v9_backtest_resilience.py、tests/test_v9_ui.py；截图 screenshots/v9/。
- [x] 回测发现 STL+ARIMA 的 90% 区间过窄（实测覆盖仅 14%），已改为取残差与一阶差分波动较大者并按 sqrt(步长) 放宽，覆盖率回到 96–100%（略保守）。
- Prophet 未纳入回测（单次拟合较慢），后续可按需开启。
- 仍未做：真实官方 API 签名与联调（需用户密钥）、分布式采集节点、TimescaleDB/ClickHouse、移动端、pgvector 扩展安装。
