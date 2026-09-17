---
name: cnipa-cpquery
description: 从国家知识产权局专利审查信息查询系统(cpquery.cponline.cnipa.gov.cn)下载专利案卷文件——历次审查意见通知书(OA)、历次权利要求书、意见陈述书、无效宣告文件、无效/复审决定书、授权通知书等。当用户要求查/下载中国专利的审查档案、审查文件、OA通知书、无效决定、授权权利要求书,或提到cpquery、专利审查信息查询、CNIPA、申请号/专利号+审查历史时使用。该站有瑞数反爬:必须用本skill的"接管真实Chrome+页面内API"方案,常规自动化浏览器或裸HTTP请求会被拦截。
---

# CNIPA cpquery 专利审查档案下载

从专利审查信息查询系统批量下载案件档案,**每份文书合并为单个PDF**(PNG图片流无损嵌入A4,决定书等整份PDF直通)。全程走页面内API方案(无点击模拟),单份文档0.1-2秒。

## 总流程

```
launch(启动接管用Chrome) → login(APP扫码,profile存续则免) → pipeline 一键:
uv run --with DrissionPage --with img2pdf --with pypdf python <skill>/scripts/cpquery_pipeline.py <申请号>[,<申请号2>...] <输出根目录>
```

**默认全量下载**三个主菜单的全部文书(含说明书、著录变更、驳回通知等)——关键词白名单在多样案件类型下维护不过来且会漏,全量代价仅约1.3倍时间。需要缩小时用 `--only oa,claims,decision`(类型: 审查意见+驳回通知/权利要求书/无效相关决定)。

## 环境准备(每次会话一次)

1. **启动接管用Chrome**(不能用DrissionPage自启——带webdriver标记被站点防护识别成400空白页):
   ```bash
   python <skill>/scripts/cpquery_dl.py launch
   ```
   自动探测Chrome路径(优先级: `CPQUERY_CHROME`环境变量 > PATH > 常见安装位置),用独立profile `<skill>/scripts/_cpquery_profile/`,调试端口9333。依赖 DrissionPage(用 `uv run --with DrissionPage ...` 运行)。
2. **登录**: `python <skill>/scripts/cpquery_dl.py login [身份]` 导出二维码PNG,用户用**专利业务办理APP**扫码。身份可选 自然人/法人/代理机构(默认自然人),按实际账号类型选。登录态存于Chrome profile,不删则免登录。SSO后若出现"安全异常"页,直接重访cpquery主页即可。
3. 检索前先点掉首页"使用说明"弹框(pipeline的search已自动处理)。

## API方案

登录后在**详情页上下文内**用 `fetch` 直调系统API。站点防护对页面内XHR/fetch自动附加动态防护参数,所以**必须由浏览器里的页面发请求**——这就是不能脱离浏览器裸请求的原因,但页内fetch无任何点击、无懒加载等待,速度快且稳。`scripts/cpquery_pipeline.py` 已实现全部逻辑(含重试),理解机制详见 `references/api-endpoints.md`。

端点速查(全部POST JSON;认证头 `Authorization: Bearer <localStorage.ACCESS_TOKEN>` + `userType: <localStorage.USER_TYPE>`):

| 用途 | 端点 |
|---|---|
| 通知书/中间文件/申请文件清单 | `/api/view/gn/scxx/{tzs,zjwj,sqwj}` body `{zhuanlisqh, nodeId:'aj_gk_scxx_<sub>', anjianbh:''}` |
| 无效案号列表 | `/api/view/gn/scxx/wxwj` nodeId `aj_gk_scxx_wxwj` |
| 无效案卷三子类 | `/api/view/gn/scxx/wxwj/ajbh/{sqwj,zjwj,tzs}` body 加 `parentNodeId:<案号>`(已结案案件常为合法空列表,决定书见下) |
| 无效/复审决定列表 | `/api/view/gn/scjd/{wxxxwj,fsxxwj}` nodeId `aj_gk_scjd_wxxx`/`aj_gk_scjd_fsxx` |
| 决定书rid | `/api/view/gn/scjd/tzsInfo` body `{zhuanlisqh, nodeId:<案号>, anjianbh:''}` |
| 案卷页清单 | `/api/view/gn/fetch-file-infos` body `{zhuanlisqh, rid, ds, wenjiandm}` |
| 决定书页清单 | `/api/view/gn/fetch-scjd-file-infos` body 加 `anjianbh` |
| 页本体 | GET `/api/pcshoss/view/fetch-file?osslujing=..&wenjianhzm=..&timestamp=..&sign=..&isDN=..&ds=..&wenjiandm=..` |

清单条目的 `additionalData` 直接给 `{rid, ds, wenjiandm}`;页清单响应的 `ossLujingList` 每页自带 `{osslujing, timestamp, sign, isDN}` 签名,按上式拼URL并发取页。

### API方案已踩实的坑(pipeline已内置处理)

1. **fetch-file URL必须手动字符串拼接**——`URLSearchParams` 会把 `/` 编码成 `%2F`,导致部分页404(isDN=true首页最易触发)。
2. **清单的 `wenjianhzm` 不可信**——无效决定书清单写PNG实为PDF(`%PDF`魔数),按魔数分流:PNG流合并进PDF,整份PDF直通写盘。
3. **密集请求偶发被拦截**(响应变HTML/JS异常)——sleep 3秒重试即过,勿提高频率。
4. **空响应体是独立拦截变体**(响应200但body形如`'\r\n\r\n\r\n'`)——同参数隔时段重发即成功,**不是未公开**。pipeline按7秒重试处理。
5. **决定书单页偶发404**——重跑整份即过,pipeline已把重试上限提到5轮;仍缺页时对单份决定书再单独跑一次。

### 申请号格式(重要)

用户给的号码可能是 `ZL`前缀(授权公告号)、带点申请号、`CN`前缀申请号或**缺校验位的12位**形式。pipeline统一规范化:剥任意前导字母(ZL/CN等,申请号本体永以数字开头,枚举前缀会漏)+去点/末位X大写;**12位时自动补算校验位**(权重2,3,4,5,6,7,8,9,2,3,4,5加权求和mod 11,余10记X)。系统结果行只显示13位无点格式,输入原格式会因文本不匹配点不到结果链接。

### 检索偶发0结果

同一号码自动检索0结果、手动查却有1条——主页tab的弹框/输入残留所致。pipeline已内置`search_with_retry`:0结果时重新加载主页再查一次即恢复。若重试后仍0结果,才考虑号码错误或登录态失效。

### 每案件的固定动作

`run_case` 内置:规范化申请号(13位无点、末位X大写)→ 关旧detail标签(防串案件)→ search → **校验详情页正文含申请号**(详情页可能缓存上一案件的树数据,校验失败须重search)→ 逐菜单API下载。

**重要事实**: "2010-02-10前提交/发文的文件不提供"的限制只在页面点击路径存在——API路径下更早年份纸件申请的原始申请文件权利要求书也能正常下载。所以原申请文件一律用API方案取,不要因条目日期在2010年前就跳过。

### 无效文件菜单结构注意

"无效文件"下案号条目在数据层返回 `isLeaf:false`(树组件懒加载写法),但UI上多为**文件图标**——点击直接渲染该案的决定书,其下三子菜单(申请文件/中间文件/通知书)对已结案案件返回**合法空列表**(`code:200, data:[]`,非拦截非缺失);刚立案未公开的才是子菜单真有文件的形态。判别依据:先查"无效审查决定"列表,有该案号→决定书已取到,无效子菜单空≠缺档案。

## 输出规范

- 每份文书一个PDF: `<申请号>/<文件类型>_<日期>_<名称>_N页.pdf`(如 `通知书_2011-06-08  第一次审查意见通知书_3页.pdf`)
  - 若同一菜单返回多条相同显示名称的文书，按API返回顺序追加 `_2`、`_3` 等本地序号，避免不同案卷记录互相覆盖；该序号不是CNIPA原始名称的一部分
  - PNG图片流用 **img2pdf** 无损嵌入A4(210×297mm,不重编码);整份即PDF的文书(无效/复审决定书)直接写原文件
  - 运行须带 `--with img2pdf --with pypdf`(pypdf用于数直通PDF的真实页数——清单只算1条而决定书实为多页,PDF对象流已压缩不可正则计数)。img2pdf不可用时自动降级Pillow有损合并,仅异常路径
  - 重试致页数变化时自动清理旧`_N页.pdf`避免并存
- 校验: PDF头(`%PDF`)+pypdf页数与文件名后缀一致+首页A4尺寸,报告缺页
- 案件结束向用户报告: 成功清单、下载失败须重跑的条目、无无效程序的专利。**"下载失败"≠"未公开"**——空体/拦截失败往往是瞬时的,重跑即过
