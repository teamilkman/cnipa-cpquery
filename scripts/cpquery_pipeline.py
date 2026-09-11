# -*- coding: utf-8 -*-
"""cpquery 专利审查档案下载流水线 — API请求方案(页面内fetch并发,无点击)

在接管Chrome的详情页上下文内直接 fetch 系统API:瑞数防护由浏览器自身
的hook自动附加动态参数,无需任何点击/滚动。单份文档0.1-2秒。

用法:
  python cpquery_pipeline.py <申请号[,申请号2...]> <输出根目录>          # 全量下载
  python cpquery_pipeline.py <申请号> <根目录> --only oa,claims        # 按类型缩小
         可选类型: oa(审查意见/驳回通知) claims(权利要求书) decision(无效/决定书)

流程(每案件):
  1. search() 打开detail页(种下案件会话,之后全走API)
  2. 清单API拿各文件条目的 rid/ds/wenjiandm(见 MENU_EPS)
  3. 每条目: fetch-file-infos 拿签名页清单 -> 并发 fetch-file 取页
     (base64回传Python侧落盘)

输出: 每份文书合并为单个PDF `{out_base}_N页.pdf`
  - PNG图片流: img2pdf无损嵌入A4(210x297mm,不重编码)
  - 整份即PDF的文书(决定书等): 直通写盘
  运行依赖: DrissionPage + img2pdf + pypdf(数直通PDF真实页数)
  uv run --with DrissionPage --with img2pdf --with pypdf python cpquery_pipeline.py ...

依赖: 同目录 cpquery_dl.py 的 get_page/search/close_dialogs;
已通过 qr_login 登录的接管Chrome(9333端口)。
"""
import sys, os, time, json, base64, re
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding='utf-8')

from cpquery_dl import get_page, search, close_dialogs, BASE

# 页面内POST任意清单API。响应一律先text()再解析:瑞数偶发拦截密集请求
# 返回HTML,直接.json()会在JS里抛异常。
API_POST_JS = r"""
const api = arguments[0];
const bodyObj = JSON.parse(arguments[1]);
const token = localStorage.getItem('ACCESS_TOKEN');
const userType = localStorage.getItem('USER_TYPE') || 'USER_RZ_ZIRANREN';
return fetch(api, {
  method: 'POST',
  headers: {
    'Authorization': 'Bearer ' + token,
    'userType': userType,
    'Content-Type': 'application/json;charset=UTF-8'
  },
  body: JSON.stringify(bodyObj)
}).then(r => r.text());
"""

# 页面内下载一份文档:页清单API(endpoint由参数传入,案卷/决定书共用)
# -> 并发取全部页,base64打包回传。
# 坑1: fetch-file 的URL必须手动拼接——URLSearchParams会把 / 编码为%2F,
#      导致部分页(尤其isDN=true首页)404。
# 坑2: 清单声明的 wenjianhzm 不可信(无效决定书清单写PNG实为PDF),
#      按魔数分流: PNG流合并进PDF, 整份PDF直通。
FETCH_DOC_JS = r"""
const run = async () => {
const infosEp = arguments[0];
const body = JSON.parse(arguments[1]);
const token = localStorage.getItem('ACCESS_TOKEN');
const userType = localStorage.getItem('USER_TYPE') || 'USER_RZ_ZIRANREN';
const headers = {'Authorization': 'Bearer ' + token, 'userType': userType, 'Content-Type': 'application/json;charset=UTF-8'};
const text = await fetch(infosEp, {method: 'POST', headers: headers, body: JSON.stringify(body)}).then(r => r.text());
let infos;
try { infos = JSON.parse(text); } catch (e) { return JSON.stringify({_raw: text.slice(0, 300), empty: !text.trim()}); }
if (infos.code !== 200) return JSON.stringify(infos);
const list = infos.data.ossLujingList || [];
const hzm = infos.data.wenjianhzm || 'PNG';
const ds = infos.data.ds;
const wjdm = infos.data.wenjiandm;
const b64 = (buf) => {
  const bytes = new Uint8Array(buf);
  let bin = '';
  const chunk = 0x8000;
  for (let i = 0; i < bytes.length; i += chunk) {
    bin += String.fromCharCode.apply(null, bytes.subarray(i, i + chunk));
  }
  return btoa(bin);
};
const results = await Promise.all(list.map(async (it) => {
  const url = '/api/pcshoss/view/fetch-file?osslujing=' + it.osslujing
    + '&wenjianhzm=' + hzm + '&timestamp=' + it.timestamp + '&sign=' + it.sign
    + '&isDN=' + it.isDN + '&ds=' + ds + '&wenjiandm=' + wjdm;
  const resp = await fetch(url);
  if (!resp.ok) return {page: it.osslujing, err: resp.status};
  const buf = await resp.arrayBuffer();
  return {page: it.osslujing, data: b64(buf)};
}));
return JSON.stringify(results);
};
return run();
"""

MENU_EPS = {
    'tzs': '/api/view/gn/scxx/tzs',
    'zjwj': '/api/view/gn/scxx/zjwj',
    'sqwj': '/api/view/gn/scxx/sqwj',
    'wxwj': '/api/view/gn/scxx/wxwj',
    'wxscjd': '/api/view/gn/scjd/wxxxwj',
    'fsscjd': '/api/view/gn/scjd/fsxxwj',
}

# 可选筛选(--only):按名称关键词缩小抓取范围。默认全量——案件类型多样
# (驳回/复审/分案各有不同文书),关键词白名单维护不过来且会漏(如驳回通知书),
# 而全量的代价只是1.3-1.5倍时间。全量最坏结果是多了没用的文件,
# 筛选最坏结果是档案不完整且用户不知道。
ONLY_PATTERNS = {
    'oa': ('审查意见通知书', '驳回'),
    'claims': ('权利要求书',),
    'decision': ('无效', '决定'),
}


def api_post(tab, endpoint, body):
    r = tab.run_js(API_POST_JS, endpoint, json.dumps(body))
    if r is None:
        return None
    try:
        return json.loads(r)
    except Exception:
        return {'_raw': str(r)[:300]}


def menu_api(tab, endpoint, body, tries=3):
    """清单API带重试。拦截/异常响应(HTML、非200)重试,与download_doc_fast
    同纪律: sleep 3即过。合法空列表(code=200且data=[])不重试——
    无无效程序的专利菜单本来就可能为空。静默吞掉错误会让整类文书缺失
    (实测: sqwj被拦截时若无重试,'申请文件0条'整个类别被跳过)。"""
    for attempt in range(tries):
        r = api_post(tab, endpoint, body)
        if isinstance(r, dict) and r.get('code') == 200:
            return r
        print(f'     !! 清单API异常(第{attempt+1}次): {str(r)[:100]}')
        time.sleep(3)
    return None  # 重试用尽:调用方按"清单获取失败"处理,勿当空列表


def _pillow_pdf_fallback(png_blobs):
    """img2pdf不可用/alpha通道PNG时的兜底合并(有损JPEG,仅异常路径)"""
    import io
    from PIL import Image
    imgs = []
    for raw in png_blobs:
        im = Image.open(io.BytesIO(raw))
        if im.mode in ('RGBA', 'LA', 'P'):
            bg = Image.new('RGB', im.size, (255, 255, 255))
            im = im.convert('RGBA')
            bg.paste(im, mask=im.split()[-1])
            imgs.append(bg)
        else:
            imgs.append(im.convert('RGB'))
    buf = io.BytesIO()
    imgs[0].save(buf, 'PDF', save_all=True, append_images=imgs[1:], resolution=150)
    return buf.getvalue()


def save_pages(pages, out_base):
    """pages: [{page,data}|{page,err}] -> 合并为单个PDF `{out_base}_N页.pdf`。
    返回保存的页名列表(空列表=无可存页,不产生文件)。
    - PNG图片流:img2pdf无损嵌入(A4适配,不重编码)
    - 整份即PDF的文书(决定书等):直接写
    """
    import glob as _glob
    png_blobs, pdf_blob, saved = [], None, []
    for pg in pages:
        if 'err' in pg:
            print(f'     !! 页错误 {pg["page"].split("/")[-1]}: {pg["err"]}')
            continue
        raw = base64.b64decode(pg['data'])
        if raw[:4] == b'%PDF':
            pdf_blob = raw
        else:
            png_blobs.append(raw)
        saved.append(os.path.splitext(pg['page'].split('/')[-1])[0])
    if not saved:
        return []
    os.makedirs(os.path.dirname(out_base) or '.', exist_ok=True)
    # 重试后页数可能变化:先清同base旧PDF,避免_3页/_4页并存
    for old in _glob.glob(out_base + '_*页.pdf'):
        try:
            os.remove(old)
        except OSError:
            pass
    if pdf_blob and not png_blobs:
        data = pdf_blob
        # 直通PDF的页数须取其内部真实页数(清单只算1条,决定书实为多页);
        # 对象流已压缩,正则数不出,用pypdf解析。缺pypdf时退回条目数。
        try:
            import io
            from pypdf import PdfReader
            n = len(PdfReader(io.BytesIO(pdf_blob)).pages)
        except Exception:
            n = len(saved)
    else:
        n = len(saved)
    out_pdf = f'{out_base}_{n}页.pdf'
    if pdf_blob and not png_blobs:
        pass  # data已在上面直通
    else:
        try:
            import img2pdf
            layout = img2pdf.get_layout_fun((img2pdf.mm_to_pt(210), img2pdf.mm_to_pt(297)))
            data = img2pdf.convert(png_blobs, layout_fun=layout)
        except Exception:
            data = _pillow_pdf_fallback(png_blobs)
        if pdf_blob:  # PNG与PDF混排(系统未出现过):另存防丢数据
            with open(out_base + '_原文档.pdf', 'wb') as f:
                f.write(pdf_blob)
    with open(out_pdf, 'wb') as f:
        f.write(data)
    return saved


def download_doc_fast(tab, pat, rid, ds, wjdm, out_base, anjianbh=''):
    """请求方案下载一份文档,合并为 `{out_base}_N页.pdf`;异常/缺页自动重试"""
    infos_ep = '/api/view/gn/fetch-scjd-file-infos' if anjianbh else '/api/view/gn/fetch-file-infos'
    body = {'zhuanlisqh': pat, 'rid': rid, 'ds': ds, 'wenjiandm': wjdm}
    if anjianbh:
        body['anjianbh'] = anjianbh
    for attempt in range(5):
        try:
            r = tab.run_js(FETCH_DOC_JS, infos_ep, json.dumps(body))
        except Exception as ex:
            print(f'     !! JS异常(瑞数拦截?): {str(ex)[:120]}')
            time.sleep(3)
            continue
        if r is None:
            print('     !! 无响应')
            time.sleep(3)
            continue
        try:
            pages = json.loads(r)
        except Exception:
            print(f'     !! 非JSON响应: {str(r)[:200]}')
            time.sleep(3)
            continue
        if isinstance(pages, dict):
            # {_raw: html} 或 {code: xxx} 类错误对象。
            # 空体响应(empty:true,形如'\r\n\r\n\r\n')与HTML拦截不同:实测同参数
            # 隔段时间重发即可成功,属瞬时拦截——多等几秒。
            if pages.get('empty'):
                print('     !! 空响应体(瞬时拦截),7秒后重试')
                time.sleep(7)
            else:
                print(f'     !! 接口异常响应: {str(pages)[:150]}')
                time.sleep(3)
            continue
        errs = [pg for pg in pages if 'err' in pg]
        if not errs or attempt == 4:
            saved = save_pages(pages, out_base)
            if errs:
                print(f'     !! 仍缺页: {[pg["page"].split("/")[-1] for pg in errs]}')
            return saved
        time.sleep(2)  # 有错误页:签名可能过期,重取清单重试
    print('     !! 重试用尽仍失败——瞬时拦截未消,档案不完整,须重跑本案件此文档')
    return []


def safe(s):
    return re.sub(r'[\\/:*?"<>|]+', '_', s).strip()


def compute_check_digit(num12):
    """中国申请号校验位:12位本体按权重2-9,2-5加权求和 mod 11(10→X)。"""
    s = sum(int(d) * w for d, w in zip(num12, [2, 3, 4, 5, 6, 7, 8, 9, 2, 3, 4, 5]))
    r = s % 11
    return 'X' if r == 10 else str(r)


def normalize_pat_no(pat_raw):
    """规范化为13位无点格式:去字母前缀(CN/ZL等)/点号;12位时补算校验位。
    用户常给授权公告号(ZL前缀)、带点申请号、带CN前缀申请号、
    或缺校验位的12位形式这类输入。
    剥任意前导字母而非枚举CN/ZL——申请号本体永以数字开头,枚举会漏新前缀;
    按位数精确匹配则漏CN+12位(缺校验位)形式。"""
    pat = pat_raw.strip().upper().replace('.', '')
    pat = re.sub(r'^[A-Z]+', '', pat)
    if re.fullmatch(r'\d{12}', pat):
        pat += compute_check_digit(pat)
    return pat


def search_with_retry(pat, tries=2):
    """search失败(0结果)时刷新主页重试一次。
    已实测: 主页tab的弹框/输入残留会导致偶发0结果(同号码手动查正常),
    刷新主页面后重查即恢复。"""
    for i in range(tries):
        if search(pat):
            return True
        print(f'  检索0结果(第{i+1}次), 刷新主页重试...')
        p = get_page()
        tab = None
        for t in p.get_tabs():
            if 'chinesepatent' in t.url:
                tab = t
                break
        if tab is not None:
            tab.get(f'{BASE}/chinesepatent/index')
            time.sleep(6)
            close_dialogs(tab)
    return False


def run_case(pat_raw, root, only_keys=None):
    # 统一规范化为13位无点格式:去CN前缀/点号;12位缺校验位时自动补算
    pat = normalize_pat_no(pat_raw)
    print(f'========== {pat} ==========')
    p = get_page()
    # 先清掉所有旧detail标签:防止search新开的页与旧案件页混淆
    for t in [t for t in p.get_tabs() if '/detail/' in t.url]:
        try:
            t.close()
        except Exception:
            pass
    time.sleep(1)
    if not search_with_retry(pat):
        print('检索失败: 检查申请号或登录态')
        return
    tabs = [t for t in p.get_tabs() if '/detail/' in t.url]
    if not tabs:
        print('详情页未打开')
        return
    tab = tabs[-1]
    tab.refresh()
    time.sleep(6)
    close_dialogs(tab)

    # 校验详情页确实是本案件(防止缓存到上案件的树数据)
    shown = (tab.run_js(
        'return document.body.innerText.includes(arguments[0]) ? arguments[0] : "";',
        pat) or '')
    if not shown:
        print(f'!! 申请号校验失败: 页面不含 {pat}')
        return
    print('申请号校验通过')

    case_dir = os.path.join(root, pat)
    os.makedirs(case_dir, exist_ok=True)
    dl_log = []

    def dl_all(items, prefix, flt=None):
        for it in items:
            ad = it.get('additionalData') or {}
            name = (it.get('name') or '').strip()
            if flt and not flt(name):
                continue
            rid, ds, wjdm = ad.get('rid'), ad.get('ds'), ad.get('wenjiandm')
            if not (rid and ds and wjdm):
                continue  # 无rid的父节点(如无效案号)跳过
            out_base = os.path.join(case_dir, safe(f'{prefix}_{name}'))
            t0 = time.time()
            saved = download_doc_fast(tab, pat, rid, ds, wjdm, out_base)
            if saved:
                print(f'  [{prefix}] {name}: {len(saved)}页 ({time.time()-t0:.1f}s)')
                dl_log.append((prefix, name, len(saved)))
            else:
                print(f'  [{prefix}] {name}: !! 下载失败(重试用尽)——档案不完整,须重跑')

    # 通知书/中间文件/申请文件(默认全量;--only时按关键词组筛选)
    only_pats = None
    if only_keys:
        only_pats = [p for k in only_keys for p in ONLY_PATTERNS.get(k, ())]

    def name_flt(name):
        if not only_pats:
            return True
        return any(p in name for p in only_pats)

    for key, prefix in [('tzs', '通知书'), ('zjwj', '中间文件'), ('sqwj', '申请文件')]:
        r = menu_api(tab, MENU_EPS[key],
                     {'zhuanlisqh': pat, 'nodeId': f'aj_gk_scxx_{key}', 'anjianbh': ''})
        if r is None:
            print(f'!! {prefix}清单获取失败(重试后仍被拦截),该类未下载——档案不完整,须重跑本案件')
            continue
        items = r.get('data') or []
        print(f'{prefix} {len(items)}条')
        dl_all(items, prefix, name_flt)

    # 无效程序案卷:wxwj列案号 -> 每案号下三个子菜单
    wx = menu_api(tab, MENU_EPS['wxwj'],
                  {'zhuanlisqh': pat, 'nodeId': 'aj_gk_scxx_wxwj', 'anjianbh': ''})
    if wx is None:
        print('!! 无效文件清单获取失败,该类未下载——档案不完整,须重跑本案件')
        wx = {'data': []}
    for case in wx.get('data') or []:
        anjian = case.get('nodeId')
        print(f'  无效案件: {anjian}')
        for sub in ['sqwj', 'zjwj', 'tzs']:
            r = menu_api(tab, f'/api/view/gn/scxx/wxwj/ajbh/{sub}',
                         {'zhuanlisqh': pat, 'nodeId': f'aj_gk_scxx_wxwj_{sub}',
                         'anjianbh': '', 'parentNodeId': anjian})
            items = (r.get('data') if r else None) or []
            if r is None:
                print(f'    {anjian}/{sub}: 清单获取失败,跳过')
            else:
                print(f'    {anjian}/{sub}: {len(items)}条')
            dl_all(items, f'无效文件_{anjian}_{sub}')

    # 无效审查决定:列表给案号 -> tzsInfo换rid -> 决定书专用页清单接口
    for key, prefix in [('wxscjd', '无效审查决定'), ('fsscjd', '复审审查决定')]:
        r = menu_api(tab, MENU_EPS[key],
                     {'zhuanlisqh': pat, 'nodeId': 'aj_gk_scjd_wxxx' if key == 'wxscjd' else 'aj_gk_scjd_fsxx',
                      'anjianbh': ''})
        if r is None:
            print(f'!! {prefix}清单获取失败,该类未下载——档案不完整,须重跑本案件')
            continue
        items = r.get('data') or []
        print(f'{prefix} {len(items)}条')
        for it in items:
            anjian = it.get('nodeId')
            name = (it.get('name') or anjian).strip()
            info = menu_api(tab, '/api/view/gn/scjd/tzsInfo',
                            {'zhuanlisqh': pat, 'nodeId': anjian, 'anjianbh': ''})
            d = (info.get('data') if info else None) or {}
            d = d if isinstance(d, dict) else {}
            if not d.get('rid'):
                print(f'  [{prefix}] {name}: 无rid,跳过')
                continue
            out_base = os.path.join(case_dir, safe(f'{prefix}_{name}'))
            saved = download_doc_fast(tab, pat, d['rid'], d.get('ds', 'SCJD_TZS'),
                                      d.get('wenjiandm', '100000'), out_base,
                                      anjianbh=anjian)
            if saved:
                print(f'  [{prefix}] {name}: {len(saved)}页')
                dl_log.append((prefix, name, len(saved)))
            else:
                print(f'  [{prefix}] {name}: !! 下载失败(重试用尽)——决定书是否未公开须人工确认')

    print(f'---- {pat} 完成: {len(dl_log)}份 ----')


def main():
    argv = [a for a in sys.argv[1:] if not a.startswith('--')]
    if len(argv) < 2:
        print(__doc__)
        print('用法: cpquery_pipeline.py <申请号,...> <输出根目录> [--only oa,claims,decision]')
        return
    only_keys = None
    for a in sys.argv[1:]:
        if a.startswith('--only'):
            val = a.split('=', 1)[1] if '=' in a else sys.argv[sys.argv.index(a) + 1]
            only_keys = [k.strip() for k in val.split(',') if k.strip()]
            break
    root = argv[-1]
    os.makedirs(root, exist_ok=True)
    for pat in argv[0].split(','):
        try:
            run_case(pat, root, only_keys)
        except Exception as ex:
            print(f'!! {pat} 异常: {ex}')


if __name__ == '__main__':
    main()
