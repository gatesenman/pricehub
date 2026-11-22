# PriceHub

PriceHub 是一套竞品价格监控与控价系统，支持多平台采集、同款匹配与去重、价格告警、定价与买入建议，可在本地桌面或服务器运行。

监控采集指标时，请自行生成 `PRICEHUB_METRICS_TOKEN`，把令牌写入未纳入版本库的 `deploy/metrics_token`（可复制 `deploy/metrics_token.example` 后改成你自己的值）。

启动: cd /workspace/pricehub && . .venv/bin/activate && uvicorn app.main:app --port 8800  （UI: http://127.0.0.1:8800/ ，API 文档 /docs）
演示数据: python -m uvicorn tests.mock_private_api:app --port 8901 & python seed_demo.py
e2e: python -m pytest tests -v -s
AI 搜索: export DEEPSEEK_API_KEY=...（另支持 MOONSHOT_API_KEY / DASHSCOPE_API_KEY / PERPLEXITY_API_KEY / OPENAI_API_KEY，可用 <PROVIDER>_BASE_URL 覆盖地址）
扩展数据源: 在 app/sources.py 继承 DataSource 并 @register，或直接 POST /api/sources 用 rest/csv/webpage 配置接入。
定时监控: PRICEHUB_MONITOR_MIN（默认 60 分钟），价格变动≥5% 写入 /api/alerts。

## 桌面窗口（pywebview）
- 内嵌服务模式：`python desktop.py`（自动起本地服务+窗口）；瘦客户端：`python desktop.py --url http://服务器:8800`
- Linux 需 Qt：`pip install pywebview PyQt6 PyQt6-WebEngine qtpy`（box 已装好），Windows 用系统 WebView2(EdgeChromium)，macOS 用 WKWebView，无需额外 GUI 依赖。
- 打包：在目标系统执行 `pip install pywebview pyinstaller && ./build_desktop.sh`（Windows 用同样的 pyinstaller 命令，产出 dist/PriceHub/PriceHub.exe；macOS 产出 dist/PriceHub.app，可加 --osx-bundle-identifier 并 codesign）。PyInstaller 不能交叉编译。
- box 上已产出 Linux 包：dist/PriceHub-linux-x64.tar.gz（瘦客户端，默认连 PRICEHUB_URL=http://127.0.0.1:8800）。
## 快捷键
Ctrl+K 命令面板 / Ctrl+F 查找 / Ctrl+N 新建数据源 / Ctrl+R 全部抓取 / Ctrl+E 导出CSV / Ctrl+W 关闭标签 / Ctrl+Tab 切换标签 / Ctrl+B 侧栏 / Ctrl+Alt+B 属性面板 / Ctrl+Shift+T 主题 / F5 刷新
