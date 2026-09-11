# -*- coding: utf-8 -*-
"""cpquery 登录与检索 — API下载方案的环境准备脚本

cpquery_pipeline.py 依赖本模块(get_page/search/close_dialogs/BASE),
下载本身全部走API方案(见 cpquery_pipeline.py),本脚本只做环境准备。

用法:
  python cpquery_dl.py launch                  # 启动接管用Chrome并打开cpquery
  python cpquery_dl.py login [身份]            # 导出登录二维码(身份: 自然人/法人/代理机构)
  python cpquery_dl.py search <申请号>          # 检索并点进案件详情页

Chrome路径自动探测: 环境变量 CPQUERY_CHROME > PATH > 常见安装位置。

设计要点:
  1. 必须接管真实Chrome(手动启动+remote-debugging-port),DrissionPage原生
     启动会带 webdriver 标记,被站点防护识别 -> 页面400空白。
  2. 登录走统一认证: 选身份tab -> 点扫码按钮 -> 抓base64二维码 -> 用户用
     "专利业务办理"APP扫码。SSO偶发"安全异常"页,重访cpquery主页即可,
     登录态通常已种上;登录态存于Chrome profile,不删则免登录。
  3. 首页"使用说明"弹框会挡查询,必须先点"确定"关掉,否则查询0结果。
  4. 查询按钮: class含 q-btn--st 且文本含"查询"的button
     (不要用text:查询,会匹配到页面标题)。
  5. 结果行: span.hover_active 内是申请号链接,点它新开detail标签页。
"""
import sys, os, time, re, base64, subprocess, shutil

sys.stdout.reconfigure(encoding='utf-8')

from DrissionPage import ChromiumPage, ChromiumOptions

DEBUG_PORT = 9333
PROFILE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), '_cpquery_profile')
BASE = 'https://cpquery.cponline.cnipa.gov.cn'


def find_chrome():
    """Chrome可执行文件探测: CPQUERY_CHROME环境变量 > PATH > 常见安装位置"""
    exe = os.environ.get('CPQUERY_CHROME')
    if exe and os.path.exists(exe):
        return exe
    for name in ('chrome', 'google-chrome', 'google-chrome-stable'):
        p = shutil.which(name)
        if p:
            return p
    for c in (r'C:\Program Files\Google\Chrome\Application\chrome.exe',
              r'C:\Program Files (x86)\Google\Chrome\Application\chrome.exe',
              '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
              '/usr/bin/google-chrome', '/usr/bin/chromium',
              '/usr/bin/chromium-browser'):
        if os.path.exists(c):
            return c
    return None


def get_page():
    co = ChromiumOptions()
    co.set_address(f'127.0.0.1:{DEBUG_PORT}')
    return ChromiumPage(co)


def launch():
    exe = find_chrome()
    if not exe:
        print('未找到Chrome: 设环境变量 CPQUERY_CHROME 指向chrome可执行文件后重试')
        return
    subprocess.Popen([
        exe,
        f'--remote-debugging-port={DEBUG_PORT}',
        f'--user-data-dir={PROFILE_DIR}',
        '--no-first-run', '--no-default-browser-check', '--start-maximized',
        f'{BASE}/chinesepatent/index',
    ], close_fds=True)
    print('Chrome已启动, 等待页面...')
    time.sleep(8)
    p = get_page()
    print('标签页:', [t.url[:80] for t in p.get_tabs()])


def close_dialogs(tab):
    """关闭使用说明等弹框"""
    for btn_text in ('确 定', '确定'):
        for e in tab.eles(f'xpath://button[contains(., "{btn_text}")]'):
            try:
                e.click(by_js=True)
                time.sleep(0.5)
                break
            except Exception:
                pass


def qr_login(role='自然人'):
    p = get_page()
    tab = p.latest_tab
    tab.get(f'{BASE}/chinesepatent/index')
    time.sleep(6)
    url = tab.url
    if 'tysf' not in url:
        print('未跳到登录页,当前URL:', url)
        print('若已在详情系统内,无需登录')
        return
    label = role if role.endswith('登录') else f'{role}登录'
    hit = False
    for e in tab.eles('xpath://label[contains(@class,"title-item-btn")]'):
        if (e.text or '').strip() == label:
            e.click()
            time.sleep(1.5)
            hit = True
            break
    if not hit:
        labels = [(e.text or '').strip()
                  for e in tab.eles('xpath://label[contains(@class,"title-item-btn")]')]
        print(f'未找到身份tab "{label}", 可选: {labels}')
        return
    # 点扫码按钮
    for e in tab.eles('xpath://button[contains(@class,"qrImg")]'):
        try:
            e.click()
            time.sleep(2)
            break
        except Exception:
            continue
    # 抓base64二维码
    qr = tab.ele('xpath://img[contains(@src,"base64")]', timeout=5)
    if not qr:
        print('未找到二维码图片')
        return
    data = base64.b64decode(qr.attr('src').split(',', 1)[1])
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'qr_login.png')
    with open(out, 'wb') as f:
        f.write(data)
    print('二维码已保存:', out)
    print('请用专利业务办理APP扫码,完成后运行search继续')


def search(apply_no):
    p = get_page()
    tab = None
    for t in p.get_tabs():
        if 'chinesepatent' in t.url:
            tab = t
            break
    if tab is None:
        tab = p.latest_tab
        tab.get(f'{BASE}/chinesepatent/index')
        time.sleep(6)
    tab.get(f'{BASE}/chinesepatent/index')
    time.sleep(4)
    close_dialogs(tab)
    inp = tab.ele('xpath://input[@placeholder="例如: 2010101995057"]')
    inp.clear()
    inp.input(apply_no)
    time.sleep(0.5)
    btn = tab.ele('xpath://button[contains(@class,"q-btn--st") and contains(.,"查询")]')
    btn.click()
    time.sleep(4)
    body = tab.ele('tag:body').text
    m = re.search(r'共查询到\s*(\d+)\s*条', body)
    n = int(m.group(1)) if m else 0
    print(f'结果数: {n}')
    if n == 0:
        print('无结果: 检查申请号格式(13位,末位X大写,不带点) 或有弹框未关闭')
        return False
    link = tab.ele(f'xpath://span[contains(@class,"hover_active") and text()="{apply_no}"]')
    link.click()
    time.sleep(6)
    # 切到detail标签
    for t in p.get_tabs():
        if '/detail/' in t.url:
            print('详情页:', t.url[:90])
            return True
    print('未打开详情页')
    return False


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return
    cmd = sys.argv[1]
    if cmd == 'launch':
        launch()
    elif cmd == 'login':
        qr_login(sys.argv[2] if len(sys.argv) > 2 else '自然人')
    elif cmd == 'search':
        search(sys.argv[2])
    else:
        print(__doc__)


if __name__ == '__main__':
    main()
