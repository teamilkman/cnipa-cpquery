# cpquery API 端点速查

## API 端点(实测,全部POST JSON除非注明)

认证: `Authorization: Bearer <localStorage.ACCESS_TOKEN>` + `userType: <localStorage.USER_TYPE>`(如`USER_RZ_ZIRANREN`)。token 有效期内可反复用;过期表现为清单接口返回HTML(瑞数)或401,重新扫码登录。

### 清单类

| 用途 | 端点 | POST体 |
|---|---|---|
| 通知书清单 | `/api/view/gn/scxx/tzs` | `{zhuanlisqh, nodeId:'aj_gk_scxx_tzs', anjianbh:''}` |
| 中间文件清单 | `/api/view/gn/scxx/zjwj` | nodeId `aj_gk_scxx_zjwj` |
| 申请文件清单 | `/api/view/gn/scxx/sqwj` | nodeId `aj_gk_scxx_sqwj` |
| 无效案号列表 | `/api/view/gn/scxx/wxwj` | nodeId `aj_gk_scxx_wxwj` |
| 无效案卷子类 | `/api/view/gn/scxx/wxwj/ajbh/{sqwj,zjwj,tzs}` | `{zhuanlisqh, nodeId:'aj_gk_scxx_wxwj_<sub>', anjianbh:'', parentNodeId:'<无效案号>'}` |
| 无效决定列表 | `/api/view/gn/scjd/wxxxwj` | nodeId `aj_gk_scjd_wxxx` |
| 复审决定列表 | `/api/view/gn/scjd/fsxxwj` | nodeId `aj_gk_scjd_fsxx` |
| 决定书rid | `/api/view/gn/scjd/tzsInfo` | `{zhuanlisqh, nodeId:'<案号>', anjianbh:''}` → data `{rid, ds:'SCJD_TZS', wenjiandm:'100000'}` |

清单响应: `{code:200, data:[{nodeId, name, additionalData:{rid, ds, wenjiandm, tijiaorq}, url, isLeaf}]}`。`ds` 取值: `TZS`通知书 `ZJWJ`中间文件 `SQWJ`申请文件;name格式 `YYYY-MM-DD  文件名`(两空格)。

**wxwj结构注意**: 案号条目返回`isLeaf:false`+`url:/api/view/gn/scxx/wxwj/ajbh`,看似目录,但UI上多为**文件图标**——点击直接渲染该案的决定书,其下子类(ajbh三子菜单)对多数已结案案件返回空列表(`code:200,data:[]`,合法非拦截)。即:决定书通过"无效审查决定"流程(tzsInfo+fetch-scjd-file-infos)已取,wxwj案号条目本身不提供额外文件。刚立案未公开的才是三子菜单真有文件的形态。判别依据:先查wxscjd(无效审查决定列表),有该案号→决定书已下;wxwj三子菜单空≠缺档案。

### 文件页类

| 用途 | 端点 | POST体 |
|---|---|---|
| 案卷页清单 | `/api/view/gn/fetch-file-infos` | `{zhuanlisqh, rid, ds, wenjiandm}` |
| 决定书页清单 | `/api/view/gn/fetch-scjd-file-infos` | 加 `anjianbh:'<案号>'` |

页清单响应: `{code:200, data:{ossLujingList:[{osslujing,timestamp,sign,isDN}], wenjianhzm, ds, wenjiandm}}`。

页本体 GET: `/api/pcshoss/view/fetch-file?osslujing=<路径>&wenjianhzm=<hzm>&timestamp=<ts>&sign=<每页独立签名>&isDN=<bool>&ds=<ds>&wenjiandm=<dm>`

**已踩实的坑:**
1. URL必须手动拼接。`URLSearchParams`会把`osslujing`里的`/`编码成`%2F`→部分页404(尤其isDN=true首页)。
2. `wenjianhzm`不可信:决定书清单写PNG,本体实为`%PDF`。按魔数分流:PNG流→img2pdf合并为A4单PDF,`%PDF`→直通写盘(页数用pypdf数,清单条目数不可信)。
3. `osslujing`尾段是页名:通知书6位(`000001.PNG`),中间文件可能4位(`0001.PNG`)。
4. **空响应体独立变体**:清单/页清单接口偶发返回HTTP 200但body仅`'\r\n\r\n\r\n'`——区别于HTML拦截。瞬时性质:同参数隔时段重发即成功。不可据此断言"未公开/无权限";pipeline按7秒重试处理。

### 请求纪律

- URL上的`hHp4Kgam=...`是瑞数动态参数,页面内fetch自动附加;**脱离页面重放必被nginx 400拦截**——这就是必须接管真实Chrome的原因。
- 密集请求偶发拦截(响应HTML/JS抛异常):sleep 3s重试即过。单案件串行取文档,文档内页并发是安全的。
- `localStorage`关键键: `ACCESS_TOKEN` `USER_TYPE` `USER_NAME`。

## DOM 定位速查(登录/检索用)

| 目标 | 定位 |
|---|---|
| 登录页身份tab | `label.title-item-btn` ×3(自然人/法人/代理机构) |
| 扫码切换按钮 | `button.qrImg` |
| 二维码 | `img[src^="data:image/png;base64,"]` |
| 申请号输入框 | `input[placeholder="例如: 2010101995057"]`(placeholder为站点自带文本) |
| 查询按钮 | `button` class含`q-btn--st`且文本含"查询" |
| 结果行申请号链接 | `span.hover_active` text=申请号 |
| 弹框确定按钮 | `button` text含"确 定"(中间空格) |

## DrissionPage 要点

- 接管: `ChromiumOptions().set_address('127.0.0.1:9333')` + `ChromiumPage(co)`
- `tab.run_js(js, arg)`: 函数体格式取值须`return`;JS里的`arguments[0]`接arg;async要包成 `const run = async () => {...}; return run();`(顶层await报错)
- 详情页多开标签会串案件:每次search前关掉全部旧`/detail/`标签,取最后一个;`get_tabs()`引用的标签关闭后再用会抛PageDisconnectedError
- Windows终端GBK乱码: `sys.stdout.reconfigure(encoding='utf-8')`
