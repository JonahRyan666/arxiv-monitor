# 实验凝聚态每日文献日报

给做量子自旋液体、磁电耦合、多铁和阻挫磁体的实验同学用。每天从 arXiv（默认 `cond-mat.str-el` 与 `cond-mat.mtrl-sci`）和 JPSJ（Crossref）里挑**有样品、有测量**的论文，打分后合成**一条**飞书卡片推到群里。

纯理论、只做 DFT、只提模型、只做蒙特卡洛 / 张量网络 / DMRG、以及只有分子束外延薄膜的论文不会进卡片。室温附近的相变优先。

## 每天发生什么

工作流是 `.github/workflows/daily-report.yml`，北京时间 09:00 跑，也可以在 Actions 里手动跑。

1. 从 `state` 分支取出 `digest_state.json`（已发送的 arXiv ID / DOI、题名，以及上次成功时间）。
2. 检索上次成功之后的新论文，最多回看 3 天，用来盖住周末或漏跑。窗口向前多重叠 2 小时，避免时钟误差漏文；已经发过的不会再发。
3. 用关键词做廉价预筛，丢掉明显不相关、以及“只有理论、没有样品测量”的摘要。
4. 把剩下的交给 SiliconFlow 打分。模型要判断是不是实验、样品形态、生长方法、测了什么、居里-外斯温度、有序温度、阻挫指数 f、是否靠近室温，并写一句中文亮点和两三句中文摘要。
5. 按下面的数量规则选文，发**一张**飞书互动卡片，并把当天的 `digest.json` 留成 Actions artifact。
6. 发送成功后，把新的去重状态提交回 `state` 分支。这个分支不要合并进主分支。

筛选希望同时满足三条：

1. 有真实样品和测量（磁化率 / 比热 / 中子 / μSR / 介电或电极化）。
2. 有物理亮点：f 大约大于 10、无长程有序、剩余熵、比热 T² 或 T 线性、磁场可调极化，或者有序 / 铁电转变靠近室温。
3. 制备上做得到：助熔剂、CVT、固相或高温高压，前驱体不是剧毒，测量落在 MPMS / PPMS 常规范围。

数量不是固定篇数：

- 所有达标论文都发，最多 `MAX_PAPERS_PER_DAY` 篇（默认 10），按分数从高到低。
- 达标不足 `MIN_PAPERS_PER_DAY` 篇（默认 3）时，用分数次高、但不是纯理论的论文补到 3 篇，卡片里标成 **备选（未完全达标）**，并带上分数。
- 备选也不够时，有几篇发几篇。
- 一篇非理论候选都没有时，发一句 `No qualifying papers today.`

非实验论文和“只有 MBE 薄膜”的论文直接丢掉，不会拿来凑数。

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
| `max_papers_per_day` | 每天最多发几篇达标论文 | 10 |
| `min_papers_per_day` | 不足时用备选补到几篇 | 3 |
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
3. 想改每天篇数或模型时，改 `config.yaml`，或者设置仓库变量 `MAX_PAPERS_PER_DAY`、`MIN_PAPERS_PER_DAY`、`SILICONFLOW_SCREEN_MODEL`、`SILICONFLOW_FALLBACK_MODEL`、`SILICONFLOW_MODEL`。
4. 只有在你另外要收 JSON 时，才添加 `RESULTS_WEBHOOK_URL` 和 `RESULTS_WEBHOOK_SECRET`。
5. `state` 分支留给机器人，不要合并，也不要要求评审才能推送。
