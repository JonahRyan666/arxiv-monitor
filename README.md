# 实验凝聚态每日文献日报

给做量子自旋液体、磁电耦合、多铁和阻挫磁体的实验同学用。每天从 arXiv（默认 `cond-mat.str-el` 与 `cond-mat.mtrl-sci`）和 JPSJ（Crossref）里挑**有样品、有测量**的论文，打分后合成**一条**飞书卡片推到群里。

纯理论、只做 DFT、只提模型、只做蒙特卡洛 / 张量网络 / DMRG、以及只有分子束外延薄膜的论文不会进卡片。室温附近的相变优先。

## 每天发生什么

工作流是 `.github/workflows/daily-report.yml`，北京时间 09:00 跑，也可以在 Actions 里手动跑。

1. 从 `state` 分支取出 `digest_state.json`（已发送的 arXiv ID / DOI、题名，以及上次成功时间）。
2. 检索上次成功之后的新论文，最多回看 3 天，用来盖住周末或漏跑。窗口向前多重叠 2 小时，避免时钟误差漏文；已经发过的不会再发。
3. 用关键词做廉价预筛，丢掉明显不相关、以及“只有理论、没有样品测量”的摘要。
4. 把关键词命中最多的若干篇（默认 16 篇）交给 SiliconFlow 做筛选。仍然只看摘要，不读全文，并按批请求。模型判断是不是实验、样品形态、生长方法、测了什么、居里-外斯温度、有序温度、阻挫指数 f、是否靠近室温，并写一句中性的物理亮点。这一步不翻译全文。
5. 选出当天要发的 5–10 篇之后，再用翻译模型（默认 `tencent/Hunyuan-MT-7B`，失败则用 `Qwen/Qwen3.5-9B`）把**题名和整段摘要**译成中文。未入选的论文不翻译。公式如 `$\mathbf{C}=(1,1,1)$` 按原文保留。
6. 发飞书卡片，并把当天的 `digest.json` 留成 Actions artifact。每篇包含编号、达标或备选和分数、英文题名链接、【中文题名】、短作者、一行样品/生长/温度/来源、【摘要翻译】、【亮点/筛选理由】。卡片超过约 30KB 时拆成多条：先一条总览，然后每条最多 5 篇，编号连续，讨论提示只在最后一条。
7. 发送成功后，把新的去重状态提交回 `state` 分支。这个分支不要合并进主分支。

达标条件比第一版松：实验论文只要有真实样品和相关物理就入选，生长方法没写清楚也不淘汰。分数大约 4 分及以上、并且看得到样品的，也会算达标。纯理论、只做 DFT / 模型 / 蒙特卡洛 / 张量网络 / DMRG、以及只有分子束外延薄膜的论文仍然直接丢掉。

数量：

- 达标论文按分数从高到低都发，最多 `MAX_PAPERS_PER_DAY` 篇（默认 10）。
- 达标不足 `MIN_PAPERS_PER_DAY` 篇（默认 5）时，先用模型打了分但没达标的论文补，再用还没送给模型的关键词候选补，补到 5 篇。
- 补位时如果下一篇和最后一篇分数接近或并列，可以多收 1 篇，当天最多 6 篇。达标本身超过 5 篇时，不受这 6 篇限制，最多仍是 10 篇。
- 补进来的都标成 **备选**。有模型分数就写上分数；只靠关键词进来的不写分数。
- 一篇候选都没有时，发一句 `No qualifying papers today.`

卡片里每篇有编号 `#1`、`#2`…… 末尾有一段讨论提示。群里可以直接回复，例如 `#2 追 / #4 细读 / #5 跳过`。

飞书自定义机器人的 webhook **只能发、不能收**。这个仓库不会、也收不到群里的回复。要自动读回复，需要另外做一个带事件订阅的飞书应用机器人，而不是现在这个 webhook。

## 配置

`config.yaml` 的 `arxiv_monitor` 段会被脚本读入。优先级是：

**环境变量 / GitHub 仓库变量 > `config.yaml` > 脚本内置默认值**

空的环境变量视为没设置，所以仓库变量留空时，以 `config.yaml` 为准。

| 配置 | 作用 | 默认 |
| --- | --- | --- |
| `categories` | arXiv 分类 | `cond-mat.str-el`、`cond-mat.mtrl-sci` |
| `keywords` | 标题+摘要预筛，`*` 是通配符 | 自旋液体、阻挫、kagome、多铁、磁电等 |
| `theory_markers` | 没有测量证据就丢掉 | DFT、first-principles、Monte Carlo、DMRG 等 |
| `experiment_markers` | 有样品或测量的证据 | flux、CVT、比热、中子、μSR 等 |
| `max_lookback_days` | 最长回看 | 3 天 |
| `max_papers_per_day` | 达标论文每天最多发几篇 | 10 |
| `min_papers_per_day` | 不足时用备选补到几篇 | 5 |
| `screen_pool` | 送给模型的预筛上限，其余可作未评分备选 | 16 |
| `models.screen` | 筛选用的指令模型 | `Qwen/Qwen3.6-27B` |
| `models.fallback` | 第一备用 | `Qwen/Qwen3.5-9B` |
| `models.legacy` | 第二备用（原来的翻译模型） | `tencent/Hunyuan-MT-7B` |
| `screen_batch_size` / `screen_retries` | 批量条数和 JSON 解析重试 | 4 / 2 |

对应的环境变量：

- `MAX_PAPERS_PER_DAY`、`MIN_PAPERS_PER_DAY`
- `ARXIV_CATEGORIES`（逗号分隔）、`ARXIV_MAX_LOOKBACK_DAYS`
- `SILICONFLOW_SCREEN_MODEL`、`SILICONFLOW_FALLBACK_MODEL`、`SILICONFLOW_MODEL`
- `SCREEN_BATCH_SIZE`、`SCREEN_RETRIES`
- `DRY_RUN=1`

在仓库的 Settings → Secrets and variables → Actions → Variables 里设置同名变量，工作流会把它传给脚本，并覆盖 `config.yaml`。

## 密钥

沿用原来的三个，名字不要改：

- `FEISHU_WEBHOOK_URL`
- `FEISHU_SECRET`（飞书机器人签名；没签名的机器人可以留空）
- `SILICONFLOW_API_KEY`

可选。两个都设置时，脚本会把当天的 digest JSON POST 出去，请求头是 `X-Signature: sha256=<对原始 body 做 HMAC-SHA256 的十六进制>`。缺任何一个就跳过，不报错：

- `RESULTS_WEBHOOK_URL`
- `RESULTS_WEBHOOK_SECRET`

日志里不会打印这些值。

## state 分支

`sent_papers.json` 在 `.gitignore` 里，以前的 artifact 也不会在下次运行时还原，所以同一篇会天天重复。现在改成：

- 状态文件是 `digest_state.json`，只放已发送的 arXiv ID（去掉 `v1`/`v2`）、DOI、题名和上次成功时间。
- 工作流有 `contents: write`。每次发送成功后，把这个文件提交到单独的 `state` 分支。
- 下次运行开始时再把该文件取回来。
- 已经发过的论文不会再发。arXiv 新版本和后来出现的 DOI 也会对上。
- 筛选接口临时失败的论文会记在 `pending` 里，下次按 ID 再试，最多三次。

不要把 `state` 合并进主分支，也不要给它加“必须走 Pull Request”的保护规则，否则 Actions 推不上去。第一次成功跑完，这个分支会自动出现。

## 手动跑和演练

Actions 页面选 **Daily arXiv Report** → **Run workflow**。勾上 dry run 时设置 `DRY_RUN=1`：照常检索和筛选，把飞书卡片 JSON 打到日志里，不推送，也不改 `state`。

本地：

```bash
pip install -r requirements.txt
DRY_RUN=1 python arxiv_daily_report.py
python -m unittest test_digest_logic.py test_jpsj_crossref.py
```

本地演练同样需要 `SILICONFLOW_API_KEY` 才会真正打分。没有达标论文时，日志里会看到 `No qualifying papers today.`

## 你需要确认的一次性设置

1. 仓库 Actions 允许这个工作流写内容。文件里已经写了 `permissions: contents: write`。如果组织把 `GITHUB_TOKEN` 锁成只读，要在仓库 Settings → Actions → General 里改成 Read and write，或者允许工作流自己声明写权限。
2. 三个原有密钥还在：`FEISHU_WEBHOOK_URL`、`FEISHU_SECRET`、`SILICONFLOW_API_KEY`。
3. 想改每天篇数或模型时，改 `config.yaml`，或者设置仓库变量 `MAX_PAPERS_PER_DAY`、`MIN_PAPERS_PER_DAY`、`SILICONFLOW_SCREEN_MODEL`、`SILICONFLOW_FALLBACK_MODEL`、`SILICONFLOW_MODEL`。如果之前把 `MIN_PAPERS_PER_DAY` 设成了 3，请改成 5 或删掉，否则会盖过配置文件。
4. 只有在你另外要收 JSON 时，才添加 `RESULTS_WEBHOOK_URL` 和 `RESULTS_WEBHOOK_SECRET`。
5. `state` 分支留给机器人，不要合并，也不要要求评审才能推送。
