# PriceHub v14 进度：冷启动 / 首屏 / 桌面观感

时间：2026-10-29（UTC+8）

## 做了什么

### 冷启动
- **语义 / CLIP 模型懒加载**：`app/domains/search/semantic.py` 不再在 import 时加载 SentenceTransformer；首次搜索或后台预热时再加载。`/readyz` 与 `/api/health` 不再被模型拖住。
- **后台预热**：`app/main.py` 启动后开守护线程预热 ST/CLIP（`PRICEHUB_WARM_ST=0` 可关）。
- **加载状态**：`engine_status()` 区分「按需加载 / 加载中 / 已就绪 / 回退」，避免把「正在加载」误报成 TF-IDF。
- **桌面闪屏先行**：`PriceHub.pyw` / `run_windows.py` / `desktop.py` 先用内嵌 HTML 打开 pywebview 窗口，服务 `readyz` 后再 `load_url` 进入工作台；就绪轮询 0.15s。
- **Windows 精简包默认** `PRICEHUB_NO_ST=1` / `PRICEHUB_NO_CLIP=1`，进一步加快安装版冷启动（与精简依赖一致）。

### 首屏
- **Chart.js 按需加载**：从 `<head>` 同步标签移除，仪表盘/详情/深度对比绘图前再拉（`ensureChart()`）。
- **启动闪屏**：`#ph-boot` 纯 CSS 闪屏 + 品牌图；Alpine 初始化后揭开；不拦截点击。
- **静态缓存 + gzip**：`app/core/perf_middleware.py` — `/static/vendor/`、`/static/icons/` 一周缓存，其它静态 5 分钟；响应 gzip。

### 桌面观感
- 菜单栏品牌图标（`pricehub-32.png`）；保留 pywebview 独立窗口 + `PriceHub.ico`。
- 安装包版本号升至 **1.14.x**。

### 顺带修复
- **statsmodels / pandas 不兼容**：ARIMA 的 `deprecate_kwarg` 在现环境导入失败；改为 STL 可用即可启用 `stl+arima`，ARIMA 失败时回退线性趋势（预测与回测恢复）。
- **v14 不覆盖 v7 init**：只包装原 `init`，团队登录 / 邀请页逻辑保持。

## 测试
- 新增 `tests/test_v14_perf.py`（懒加载、闪屏/无同步 chart、Cache-Control、启动器关键字）。
- SQLite 全套：**116 passed, 15 skipped**（含新增 test_v14_perf 3 个）。
- UI e2e 在预热完成后再跑语义搜索（避免首搜抢模型）。

## 交付
- `dist/PriceHub-Setup.exe` + `dist/PriceHub-source.zip` → 用户电脑 `Desktop\PriceHub\`（仅这两个文件）。

## 未做 / 后续
- TimescaleDB 仍因软件源缺失跳过。
- 官方 API 真机联调仍需用户密钥。
- 图标缓存若仍显示旧图标：注销或重启 explorer。
