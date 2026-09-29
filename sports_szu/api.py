"""Fixed-origin API adapter. No redirects, raw response logging or external payments."""
import secrets
import re
from .catalog import SPORTS, booking_type, matches_venue, rank_room

ORIGIN = 'https://ehall.szu.edu.cn'
BASE = ORIGIN + '/qljfwapp/sys/lwSzuCgyy/'
INDEX = BASE + 'index.do'


class ApiError(Exception):
    pass


class LoginRequired(ApiError):
    pass


class Ambiguous(ApiError):
    """The write may have reached the server; do not replay it."""


class SchoolAPI:
    def __init__(self, cookies):
        import requests
        self.session = requests.Session()
        self.timeout = 10
        self.session.trust_env = False  # No ambient proxy/netrc credentials.
        self.session.headers.update({'Referer': INDEX, 'Origin': ORIGIN,
                                     'X-Requested-With': 'XMLHttpRequest', 'Accept': 'application/json'})
        for cookie in cookies:
            domain = cookie.get('domain', '').lstrip('.')
            if domain in ('ehall.szu.edu.cn', 'szu.edu.cn'):
                # Narrow replay scope to ehall even for parent-domain cookies.
                self.session.cookies.set(cookie['name'], cookie['value'], domain='ehall.szu.edu.cn',
                                         path=cookie.get('path', '/'), secure=True)

    def request(self, path, data=None, write=False):
        import requests
        if not path.endswith('.do') or '..' in path or ':' in path or path.startswith('/'):
            raise ValueError('接口地址不合法')
        try:
            response = self.session.post(BASE + path, data=data or {}, timeout=(5, self.timeout), allow_redirects=False)
            if response.status_code in (301, 302, 303, 307, 308, 401, 403):
                raise LoginRequired('登录失效，请重新登录学校网站')
            response.raise_for_status()
            if 'application/json' not in response.headers.get('Content-Type', '').lower():
                raise LoginRequired('网站没有返回JSON；请检查登录或接口是否变化')
            result = response.json()
            if not isinstance(result, dict):
                raise ValueError()
            return result
        except (requests.RequestException, ValueError) as exc:
            kind = Ambiguous if write else ApiError
            raise kind('网络或接口异常；写操作须核实结果，不自动重发') from exc

    def orders(self):
        rows = []
        for page in range(1, 101):
            result = self.request('modules/myBooking/myBookingInfo.do', {'pageSize': 50, 'pageNumber': page})
            table = result.get('datas', {}).get('myBookingInfo', {})
            batch = table.get('rows')
            if str(result.get('code')) != '0' or not isinstance(batch, list):
                raise LoginRequired('无法读取预约列表，请登录或检查网站变化')
            rows.extend(batch)
            total = table.get('totalSize')
            if not batch or (total is not None and len(rows) >= int(total)):
                return rows
        raise ApiError('订单列表未完整读取，暂停写操作')

    def balance(self):
        from .models import money
        result = self.request('modules/myBooking/getSelfMoney.do')
        try:
            if str(result.get('code')) != '0':
                raise ValueError()
            return money(result['datas']['getSelfMoney']['rows'][0]['ZJE'])
        except (KeyError, IndexError, ValueError, TypeError) as exc:
            raise ApiError('余额返回格式异常') from exc

    def rooms(self, plan, date, slot):
        start, end = slot.split('-')
        result = self.request('modules/sportVenue/getOpeningRoom.do', {
            'XMDM': SPORTS[plan.sport][1], 'YYRQ': date,
            'YYLX': booking_type(plan),
            'KSSJ': start, 'JSSJ': end, 'XQDM': plan.campus})
        rows = result.get('datas', {}).get('getOpeningRoom', {}).get('rows')
        if str(result.get('code')) != '0' or not isinstance(rows, list):
            raise ApiError('场地查询格式异常')
        candidates = []
        for row in rows:
            if row.get('disabled') in (True, 'true'):
                continue
            if not matches_venue(plan, str(row.get('CGBM_DISPLAY') or row.get('CDMC', ''))) or not row.get('CGBM') or not row.get('WID'):
                continue
            text = str(row.get('text', '')).strip()
            ratio = re.fullmatch(r'(\d+)/(\d+)', text)
            available = text == '可预约' or (ratio and 0 < int(ratio[1]) <= int(ratio[2]))
            if plan.sport == 'gym' and row.get('disabled') in (False, 'false'):
                available = True
            if not available:
                continue
            candidates.append(row)
        return sorted(candidates, key=lambda r: rank_room(plan, r))

    def book(self, plan, date, slot, room, account):
        start, end = slot.split('-')
        return self.request('sportVenue/insertVenueBookingInfo.do', {
            'DHID': '', 'YYRGH': account['username'], 'YYRXM': account['real_name'],
            'CYRS': '1' if plan.sport == 'gym' else '', 'CGDM': room['CGBM'], 'CDWID': room['WID'],
            'XMDM': SPORTS[plan.sport][1], 'XQWID': plan.campus,
            'KYYSJD': slot, 'YYRQ': date, 'YYLX': booking_type(plan),
            'YYKS': f'{date} {start}', 'YYJS': f'{date} {end}', 'PC_OR_PHONE': 'pc'}, write=True)

    def quote(self, wid):
        result = self.request('sportVenue/payBookingInfo.do', {'WID': wid})
        if result.get('success') is not True or any(type(result.get(k)) is not int or result[k] < 0
                for k in ('gxje', 'tuipiao', 'zuizong', 'tksyje')):
            raise ApiError('支付报价格式异常，未扣款')
        return result

    def token(self):
        token = secrets.token_hex(16)
        result = self.request('sportVenue/initUserToken.do', {'token': token})
        if result.get('success') is not True:
            raise ApiError('支付令牌登记失败')
        return token

    def pay(self, wid, quote, token):
        return self.request('sportVenue/setYyinfoToMoney.do', {
            'ZFJE': quote['gxje'], 'TPIAO': quote['tuipiao'], 'WID': wid, 'STATE': '1',
            'ACTULAMT': 0, 'ZHJE': quote['tuipiao'], 'TOKEN': token, 'ZFLX': 'syje',
            'tksyje': quote['tksyje']}, write=True)

    def cancel(self, wid):
        return self.request('sportVenue/cancelSelectBookingInfo.do', {'WID': wid}, write=True)
