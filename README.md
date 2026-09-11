# cnipa-cpquery — CNIPA 专利审查档案批量下载

从国家知识产权局**专利审查信息查询系统**(cpquery.cponline.cnipa.gov.cn)下载完整专利案卷,每份文书合并为单个 PDF:

- 历次审查意见通知书(OA)、驳回通知
- 原始申请文件(权利要求书、说明书、附图)与授权版本权利要求书
- 意见陈述书、补正书、著录变更等中间文件
- 无效宣告案卷文件、无效/复审决定书
- 授权通知书等

本仓库是一个 **Claude Code Skill**:在 Claude Code 里说"下载 <申请号> 的审查档案"即自动执行;也可脱离 Claude Code 直接跑脚本。

## 工作原理:接管真实 Chrome + 页面内 API

该站部署瑞数反爬:裸 HTTP 请求与常规自动化浏览器(带 webdriver 标记)会被拦截。本方案启动真实 Chrome 并经调试端口(9333)接管,登录后在**页面上下文内** `fetch` 直调系统 API——站点防护会为页面内请求自动附加动态参数,因此请求必须由页面发出;但全程无点击模拟、无懒加载等待,单份文档 0.1–2 秒,且不受页面端"2010-02-10 前文件不提供"的限制。

## 环境要求

| 依赖 | 说明 |
|---|---|
| Python 3.10+ / [uv](https://docs.astral.sh/uv/) | 用 `uv run --with …` 免装依赖 |
| Chrome | 路径自动探测;找不到时设 `CPQUERY_CHROME` 环境变量指向其可执行文件 |
| CNIPA 账号 | 用**专利业务办理 APP** 扫码登录 |

## 安装(Claude Code Skill)

```bash
git clone https://github.com/teamilkman/cnipa-cpquery.git ~/.claude/skills/cnipa-cpquery
```

Windows 路径:`C:\Users\<用户>\.claude\skills\cnipa-cpquery`。也可克隆到项目的 `.claude/skills/` 下,仅对该项目生效。

## 使用

### 方式一:Claude Code 对话(推荐)

安装后直接说,例如:

> 帮我下载 <申请号> 的审查档案到 D:\patents

Claude 按内置流程执行:启动 Chrome → 给你看二维码等扫码 → 下载 → 汇报成功/须重跑清单。细节见 [SKILL.md](SKILL.md)。

### 方式二:直接跑脚本

```bash
cd ~/.claude/skills/cnipa-cpquery/scripts

# 1) 启动接管用 Chrome(9333 端口,独立 profile)
uv run --with DrissionPage python cpquery_dl.py launch

# 2) 登录:生成 scripts/qr_login.png,用专利业务办理 APP 扫码
#    身份可选:自然人 / 法人 / 代理机构(默认自然人)
uv run --with DrissionPage python cpquery_dl.py login 自然人

# 3) 下载,申请号逗号分隔;登录态存于 profile,之后免扫码
uv run --with DrissionPage --with img2pdf --with pypdf \
  python cpquery_pipeline.py <申请号1>,<申请号2> ~/patents
```

申请号格式随意:`ZL` 前缀(授权公告号)、带点、带 `CN`、12 位缺校验位均可,自动规范化并补算校验位(权重 2–9,2–5 加权 mod 11)。

默认全量下载三个主菜单;加 `--only oa,claims,decision` 只取 OA 通知书+权利要求书+无效/决定类。

### 输出

```
<申请号>/
├── 申请文件_<日期>  权利要求书_6页.pdf
├── 通知书_<日期>  第一次审查意见通知书_3页.pdf
├── 中间文件_<日期>  意见陈述书_2页.pdf
└── 无效审查决定_<4W案号>_21页.pdf
```

- PNG 图片流经 img2pdf 无损嵌入 A4(不重编码);本身是 PDF 的决定书直通写盘,页数用 pypdf 解析(清单条目数不可信)
- 校验:PDF 头 + pypdf 页数与文件名后缀一致 + A4 尺寸;缺页会明确报告

## 已内置处理的坑(详见 [references/api-endpoints.md](references/api-endpoints.md))

1. fetch-file URL 须手动拼接——`URLSearchParams` 会把 `/` 编码成 `%2F` 导致 404
2. 清单声明的 `wenjianhzm` 不可信:写 PNG 实为 PDF,按魔数分流
3. 密集请求偶发拦截(响应变 HTML)与**空响应体**变体——固定 sleep 重试即过;下载失败 ≠ 未公开
4. 检索偶发 0 结果——刷新主页重查一次即恢复
5. 无效案件"4W"案号条目数据层 `isLeaf:false` 但实为文件节点;已结案案件其子菜单合法为空,决定书走"无效审查决定"流程取

## 说明

- 请使用本人账号、合法合规地查询,控制请求频率
- 登录态存于 `scripts/_cpquery_profile/`(已 gitignore),保留则免重复扫码,删除该目录即退出登录
