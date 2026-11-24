# PriceHub

PriceHub 是一套竞品价格监控与控价系统，支持多平台采集、同款匹配与去重、价格告警、定价与买入建议，可在本地桌面或服务器运行。

监控采集指标时，请自行生成 `PRICEHUB_METRICS_TOKEN`，把令牌写入未纳入版本库的 `deploy/metrics_token`（可复制 `deploy/metrics_token.example` 后改成你自己的值）。

本地启动（默认 SQLite `data/pricehub.db`，不要改正在跑的生产库）：

    ./start_server.sh
    # 或：. .venv/bin/activate && uvicorn app.main:app --host 0.0.0.0 --port 8800

UI 在 http://127.0.0.1:8800/ ，API 文档 /docs，存活/就绪探针 `/healthz`、`/readyz`。

演示数据: python -m uvicorn tests.mock_private_api:app --port 8901 & python seed_demo.py
e2e: python -m pytest tests -v -s
AI 搜索: export DEEPSEEK_API_KEY=...（另支持 MOONSHOT_API_KEY / DASHSCOPE_API_KEY / PERPLEXITY_API_KEY / OPENAI_API_KEY，可用 <PROVIDER>_BASE_URL 覆盖地址）
扩展数据源: 在 app/sources.py 继承 DataSource 并 @register，或直接 POST /api/sources 用 rest/csv/webpage 配置接入。
定时监控: PRICEHUB_MONITOR_MIN（默认 60 分钟），价格变动≥5% 写入 /api/alerts。

常用环境变量：`PRICEHUB_DB`（SQLite 路径）、`PRICEHUB_NO_ST=1` / `PRICEHUB_NO_CLIP=1`（跳过语义/CLIP 模型，搜索回退 TF-IDF / phash）、`PRICEHUB_FAISS_DIR`（向量索引目录，默认 `{PRICEHUB_DB}.faiss/`，商品标题变了会自动重建）、`PRICEHUB_SCORE_WORKERS`（去重打分进程数，默认最多 4；服务里可设为 1 关闭并行）、`PRICEHUB_WARM_ST=0`（关闭启动后后台预热模型）。不要把密钥写进 `server.env` 或提交 `data/*.db`、`data/.secret`、`deploy/metrics_token`。

## 桌面窗口（pywebview）
- 内嵌服务模式：`python desktop.py`（自动起本地服务+窗口）；瘦客户端：`python desktop.py --url http://服务器:8800`
- Linux 需 Qt：`pip install pywebview PyQt6 PyQt6-WebEngine qtpy`（box 已装好），Windows 用系统 WebView2(EdgeChromium)，macOS 用 WKWebView，无需额外 GUI 依赖。
- 打包：在目标系统执行 `pip install pywebview pyinstaller && ./build_desktop.sh`（Windows 用同样的 pyinstaller 命令，产出 dist/PriceHub/PriceHub.exe；macOS 产出 dist/PriceHub.app，可加 --osx-bundle-identifier 并 codesign）。PyInstaller 不能交叉编译。
- box 上已产出 Linux 包：dist/PriceHub-linux-x64.tar.gz（瘦客户端，默认连 PRICEHUB_URL=http://127.0.0.1:8800）。
## 快捷键
Ctrl+K 命令面板 / Ctrl+F 查找 / Ctrl+N 新建数据源 / Ctrl+R 全部抓取 / Ctrl+E 导出CSV / Ctrl+W 关闭标签 / Ctrl+Tab 切换标签 / Ctrl+B 侧栏 / Ctrl+Alt+B 属性面板 / Ctrl+Shift+T 主题 / F5 刷新

## v18–v27 可靠性更新

- 工作区缓存/去重数据及索引分别隔离；FAISS/numpy 快照升级 `pricehub-faiss-v2`，单文件原子发布并检查校验和，旧缓存自动重建。
- 价格必须为有限非负数；显式时区时间归一到 UTC；历史回填不覆盖按事件时间计算的现价。
- 比价按配置参考汇率统一 CNY 排序（非实时汇率），未知币种单列；定价竞品换成目标商品币种。
- 采集 upsert、合并/撤销使用事务；撤销绑定工作区和 10 分钟有效期，不覆盖被复用 id 的新数据。
- 去重 fork 并行仅用于安全的单线程 dense CLI；Windows、服务多线程、稀疏回调均正确串行回退。
- 测试图片/证据可用 `PRICEHUB_IMAGE_DIR`、`PRICEHUB_EVIDENCE_DIR` 隔离；正常默认路径保持不变。

验证范围与未通过项详见 [十轮状态记录](docs/ITERATION_v18_v27_STATUS.md)。目前不是全量依赖测试全绿，也未做 Windows 真机或 PG 服务验证。
