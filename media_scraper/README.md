# MediaScraper

> 家庭影院媒体刮削与重命名工具  
> Home Theater Media Scraper & Renamer

自动识别视频文件 → 在线刮削元数据 → 智能重命名 → 生成 Kodi/Jellyfin 兼容的 NFO + 封面。

**当前版本：`1.0.8.1`**

克隆后按下面的「配置」填好 API Key、代理和输出目录即可运行。

---

## 功能特性

- **多源刮削**：JavDB、JavDatabase、JavLibrary、JavBus、R18.dev、多家片商、FC2、DMM/FANZA、JavInfo、TMDB 等，按优先级自动降级
- **预览与执行分离**：扫描预览只查询并生成计划；执行刮削才写入并整理
- **FC2 本机登录**：调用本机 Edge/Chrome，只保存 Cookie，不保存密码
- **智能解析**：番号（含 Caribbeancom `091926-001`）、年份、分辨率、剧集
- **中日文优先**：同一番号下完整中/日文标题优先于英文；封面与演员仍跟高分主源
- **封面**：WebP 转 JPEG，保存为 `番号-poster.jpg`；优先大图，占位图可被真封面替换
- **并行**：Semaphore 默认 10 路并发
- **模板引擎**：电影 / 番号 / 剧集模板可配
- **元数据**：NFO (Kodi) + JSON + 封面 + 单片子文件夹
- **GUI**：PySide6 三栏工作台，总进度 + 逐行进度/状态，实时日志，设置页
- **回滚**：rollback JSON
- **代理**：HTTP / HTTPS / SOCKS5，GUI 连通性测试
- **明暗双主题**：亮色（青绿强调，默认）/ 暗色（紫色强调），右上角即时切换并持久化
- **应用图标**：青绿圆角「播放 + 放大镜」；窗口 / 任务栏 / EXE 共用，并随 EXE 打包

---

## 界面与品牌资源（随程序打包）

| 资源 | 路径 | 说明 |
|------|------|------|
| 亮色主题 | `assets/light.qss` | 白底卡片 + 青色强调 `#14B8A6`，**默认主题** |
| 暗色主题 | `assets/dark.qss` | 黑底 `#0C0B12` + 紫色强调 `#8B5CF6` |
| 应用图标 | `assets/app.ico` | 多尺寸 ICO（16–256） |
| 图标源图 | `assets/app_icon_source.png` | 256×256 权威 PNG；`python make_icon.py` 可再生成 ICO |

**主题切换**：工作台右上角「主题」→ 浅色 / 暗色，写入 `config.yaml` 的 `ui.theme`。

**图标样式**：圆角方块青绿渐变底 + 白色「播放三角叠放大镜」。主题正文在 `assets/light.qss` 与 `assets/dark.qss`，图标可由 `python make_icon.py` 从 `assets/app_icon_source.png` 重新生成。

`build_exe.spec` 打包清单（必须）：

```text
config.example.yaml
assets/app.ico
assets/dark.qss
assets/light.qss
```

EXE 内只带模板。首次运行会在 EXE 旁生成 `config.yaml`，这份真配置不会打进安装包。

---

## 安装

```bash
cd media_scraper
pip install -r requirements.txt
```

需要 Python 3.10+。

---

## 配置

程序读写的是旁边的 `config.yaml`（源码运行时在 `media_scraper/config.yaml`，打包后在 EXE 旁）。这个文件已在 `.gitignore` 里，里面可以放 Key、代理和本机目录。

仓库里只有模板 `config.example.yaml` 和 `.env.example`。

### 第一次运行

```bash
cd media_scraper
copy config.example.yaml config.yaml
```

已有 `config.yaml` 时不要覆盖，程序会继续用你原来的配置。没有这个文件时，首次启动也会自动从模板复制一份。

然后任选一种方式填密钥：

1. 直接编辑 `config.yaml`
2. `copy .env.example .env`，把 Key 写在 `.env` 里。启动时非空环境变量会覆盖 YAML 里的对应字段，其他选项保持不变
3. 打开 GUI：**设置 → 刮削源 / 刮削设置 / 重命名 / TMDB**，保存后写回 `config.yaml`

### API Key

TMDB 在 [themoviedb.org/settings/api](https://www.themoviedb.org/settings/api) 申请。填好后把该源的 `enabled` 改为 `true`：

```yaml
- name: tmdb
  enabled: true
  api_key: ""          # 或在 .env 里写 TMDB_API_KEY
  language: zh-CN
  region: CN
```

DMM 使用 `api_id` / `affiliate_id`（环境变量 `DMM_API_ID`、`DMM_AFFILIATE_ID`）。JavInfo 使用 `api_key`（环境变量 `JAVINFO_API_KEY`）。不需要这些源时保持 `enabled: false` 即可，番号源不依赖它们。

### 代理

默认关闭。需要时在 `config.yaml` 里打开，端口按你自己的客户端填写。Clash 常见是 `7890`，不是固定值。

```yaml
http:
  proxy:
    enabled: true
    type: http            # http / https / socks5
    host: 127.0.0.1
    port: 7890
    username: ""          # 没有认证就留空
    password: ""
    test_url: https://www.gstatic.com/generate_204
```

对应环境变量：`MEDIASCRAPER_PROXY_ENABLED`、`MEDIASCRAPER_PROXY_TYPE`、`MEDIASCRAPER_PROXY_HOST`、`MEDIASCRAPER_PROXY_PORT`、`MEDIASCRAPER_PROXY_USERNAME`、`MEDIASCRAPER_PROXY_PASSWORD`。

也可以在 **设置 → 刮削源 → 网络代理** 里填写并点「测试代理连通性」。

### 输出目录

默认 `rename.output_mode: in_place`，整理结果写在视频所在目录。要改到别的文件夹：

```yaml
rename:
  output_mode: custom_dir
  custom_output_dir: "D:/Media/output"    # 写成你自己的目录
```

或设置环境变量 `MEDIASCRAPER_OUTPUT_DIR`，启动时会把 `output_mode` 切到 `custom_dir`。GUI 里也可以勾选「输出到指定目录」。

模板、并发和元数据的其余字段见 `config.example.yaml`。

---

## 使用方法

### CLI

```bash
python main.py scan /path/to/videos
python main.py scrape /path/to/videos --apply
python main.py scrape /path/to/videos --apply -o /path/to/output
python main.py config
python main.py providers
python main.py rollback rollback_20240101_120000.json
python main.py version
python main.py gui
```

### GUI

```bash
python main.py gui
# 或双击打包后的 MediaScraper.exe
```

1. 左侧「工作台」：选择文件夹 / 视频  
2. 右侧勾选本次刮削源；可选「输出到指定目录」  
3. **扫描预览**：只查询与出计划，不写文件、不移动  
4. **执行刮削**：写 NFO / JSON / 封面并归档到单片文件夹  
5. 观察 KPI、总进度、表格逐行进度与状态  

含 FC2 番号且勾选 FC2 时，会先完成本机浏览器登录（已有 Cookie 则提示「FC2登录已完成」）。

---

## 打包成 Windows EXE

在 `media_scraper` 目录：

```bash
pip install pyinstaller
python make_icon.py          # 可选：从 app_icon_source.png 刷新 app.ico
pyinstaller build_exe.spec --noconfirm --distpath ..
```

产物：项目根目录 `MediaScraper.exe`（onefile + windowed，无黑窗）。  
主题 QSS、图标和 `config.example.yaml` 打进 EXE。首次运行在 EXE 旁生成 `config.yaml`、`logs/`。不要把已有的 `config.yaml` 打进包。

| 项目 | 处理 |
|------|------|
| 只读资源（配置模板、QSS、ico） | `_MEIPASS` |
| 可写配置 / 日志 / fc2_session | EXE 旁；目录不可写则 `%USERPROFILE%\MediaScraper` |

---

## 刮削源

| Provider | 类型 | 说明 |
|----------|------|------|
| JavDB / JavDatabase / JavLibrary / JavBus | 番号 HTML | 常用免费源 |
| R18.dev、Jav321、LibreDMM、AVSOX 等 | 番号 | 设置中启用 |
| FC2 / 片商源 | 专用 | 默认关闭；FC2 可本机登录 |
| DMM/FANZA、JavInfo | API | 需凭证 |
| TMDB | 通用电影 | 跳过番号类 |
| Mock | 测试 | 默认关 |

JavDB 搜索一般无需登录；个别详情跳到「登入」页时程序会丢弃该标题，改用搜索列表片名。

---

## 文件命名模板

| 变量 | 说明 |
|------|------|
| `{title}` | 标题（中日文优先） |
| `{year}` / `{code}` / `{resolution}` | 年份 / 番号 / 分辨率 |
| `{season}` / `{episode}` | 季集 |
| `{source}` / `{rating}` | 来源 / 评分 |

番号模板 `{code} {title}`：标题已以番号开头时只保留一次。Caribbeancom 日期番号同样走番号模板。

---

## 输出结构

```
output_dir/
├── ABC-123 中文标题/
│   ├── ABC-123 中文标题.mp4
│   ├── movie.nfo
│   ├── metadata.json
│   └── ABC-123-poster.jpg
└── scrape_report_*.csv
```

---

## 测试

```bash
python -m pytest tests/ -q
```

覆盖解析、瀑布标题、预览不写文件、归档幂等、代理、FC2、封面 WebP、Caribbeancom 等。

---

## 常见问题

### 代理怎么配？

**设置 → 刮削源 → 网络代理**：启用 → 类型/主机/端口 →「测试代理连通性」→ 保存。  
未开代理时扫描/执行会弹窗确认。代理仅用于合法网络请求，不得用于绕过验证码、付费墙或 DRM。

### FC2 为什么弹浏览器？

启用 FC2 且任务含 FC2 番号时确认登录。Cookie 写在 EXE 旁 `fc2_session.json`，不存密码。

### 文件名变成「番号 登入」？

登录墙标题已被拒绝。请用当前版本重新刮削；旧文件夹不会自动改名。

### 如何回滚？

```bash
python main.py rollback rollback_*.json
```

---

## 项目结构

```
media_scraper/
├── main.py / app_gui.py / version.py / version_info.txt
├── make_icon.py / build_exe.spec
├── config.example.yaml / .env.example
├── config.yaml            # 本地生成，不要提交
├── README.md
├── assets/
│   ├── app.ico / app_icon_source.png / app_icon_preview.png
│   ├── dark.qss / light.qss
├── cli/  core/  providers/  metadata/  rename/  gui/  tests/
```

---

## 版本说明（摘要）

| 版本 | 要点 |
|------|------|
| 1.0.8.1 | 当前；修复 JavMenu「猜你喜欢」软 404 被当成标题；FC2/FC2-PPV 番号对齐；FC2 番号优先官方源 |
| 1.0.8.0 | 执行时刮完一组就归档一组，中断后已开始的一组做完，会话记录标明已归档和未改动 |
| 1.0.7.0 | 无番号的中日文标题按文件名本地归档，去掉单独的 `_000`，写入只含片名的 movie.nfo |
| 1.0.6.0 | 同一影片的上下/数字/字母分段合并到一个文件夹，文件名在番号后标注（Part N） |
| 1.0.5.0 | 三栏 GUI + 紫/青双主题 + 播放放大镜图标随 EXE 打包 |
| 1.0.2.0 | Caribbeancom 相对封面补全绝对地址 |
| 1.0.1.0 | Caribbeancom 日期番号保留在文件名 |
| 1.0.0.0 | 英文标题最低优先级；WebP→JPEG |

---

## License

MIT License

## 致谢

- [JavDB](https://javdb.com) / [JavBus](https://javbus.com) — 番号元数据  
- [TMDB](https://www.themoviedb.org) — 通用电影元数据  
- [Kodi](https://kodi.wiki/view/NFO_files) — NFO 格式规范  
