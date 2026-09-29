"""Sport codes, venue filters and ranking ported from the user's userscript."""
import re
from .models import court_rank

SPORTS = {
    'badminton': ('羽毛球', '001'), 'gym': ('健身房（一楼重量型）', '007'),
    'volleyball': ('排球', '003'), 'tennis': ('网球', '004'),
    'basketball': ('篮球', '005'), 'table_tennis': ('乒乓球', '013'), 'billiards': ('桌球', '016'),
}
VENUES = {
    'badminton': {'1': ['运动广场东馆羽毛球场'], '2': ['至畅', '至快']},
    'gym': {'1': ['运动广场西馆一楼健身房'], '2': []},
    'volleyball': {'1': ['西馆排球场(包场)'], '2': ['风雨操场排球场']},
    'tennis': {'1': ['运动广场海边网球场', '北区网球场'], '2': ['北区体育场网球场', '南区室外网球场']},
    'basketball': {'1': ['运动广场天台篮球场', '运动广场东馆室内篮球场'], '2': ['风雨操场篮球场']},
    'table_tennis': {'1': ['北区乒乓球馆'], '2': ['体育馆乒乓球室']}, 'billiards': {'1': [], '2': []},
}
FILTERS = {
    '至畅': ['至畅'], '至快': ['至快'], '运动广场东馆羽毛球场': ['东馆', '运动广场'],
    '运动广场海边网球场': ['海边', '运动广场'], '北区网球场': ['北区'],
    '北区体育场网球场': ['北区体育场'], '南区室外网球场': ['南区室外'],
    '西馆排球场(包场)': ['西馆', '排球场'], '风雨操场排球场': ['风雨操场'],
    '运动广场天台篮球场': ['天台', '运动广场'], '运动广场东馆室内篮球场': ['东馆', '室内篮球场'],
    '风雨操场篮球场': ['风雨操场'], '北区乒乓球馆': ['北区'], '体育馆乒乓球室': ['体育馆'],
}


def booking_type(plan):
    return '2.0' if plan.sport == 'gym' or (plan.sport == 'basketball' and plan.campus == '1') else '1.0'


def matches_venue(plan, full_name):
    return plan.venue == '全部' or any(part in full_name for part in FILTERS.get(plan.venue, [plan.venue]))


def rank_room(plan, room):
    full_name, name = str(room.get('CGBM_DISPLAY', '')), str(room.get('CDMC', ''))
    venue, court = 2, 0
    if plan.sport == 'badminton' and plan.campus == '1':
        court = court_rank(name, plan.preferred, plan.avoided)
    if plan.sport == 'badminton' and plan.campus == '2':
        if '至畅' in full_name:
            venue = 0
            if '5号场' in name or '五号场' in name:
                court = -2
            elif '10号场' in name or '十号场' in name:
                court = -1
            elif re.search(r'(?<!\d)1号场|一号场|6号场|六号场', name):
                court = 2
        elif '至快' in full_name:
            venue = 1
    if plan.sport == 'tennis' and plan.campus == '1':
        venue = 0 if '海边' in full_name else (1 if '北区' in full_name else 2)
    return court, venue
