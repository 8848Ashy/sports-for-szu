from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
import re

SHANGHAI = timezone(timedelta(hours=8), 'Asia/Shanghai')


def now():
    return datetime.now(SHANGHAI)


def money(value):
    from decimal import Decimal, InvalidOperation
    try:
        amount = Decimal(str(value)) * 100
        if not amount.is_finite() or amount < 0 or amount != amount.to_integral_value():
            raise ValueError('金额必须为非负数且最多两位小数')
        return int(amount)
    except InvalidOperation as exc:
        raise ValueError('金额格式错误') from exc


def court_codes(text):
    result = []
    for item in re.split(r'[,，\s]+', text.strip().upper()):
        if not item:
            continue
        match = re.fullmatch(r'([A-Z])(\d{1,2})(?:-(\d{1,2}))?', item)
        if not match:
            raise ValueError('场地格式示例：B4-6,C4-6')
        letter, first, last = match.groups()
        first, last = int(first), int(last or first)
        if not 1 <= first <= last <= 99:
            raise ValueError('场地范围不合法')
        result.extend(f'{letter}{i}' for i in range(first, last + 1))
    return result


def court_rank(name, preferred, avoided):
    match = re.search(r'([A-Z]\d{1,2})(?!\d)', name.upper())
    code = match.group(1) if match else ''
    good, bad = court_codes(preferred), court_codes(avoided)
    if code in good:
        return good.index(code)
    return len(good) + 1 + (bad.index(code) if code in bad else -1) + (100 if code in bad else 0)


@dataclass
class Plan:
    id: str
    name: str = '我的预约计划'
    enabled: bool = False
    weekdays: list = field(default_factory=lambda: [0, 2, 4])
    fire_time: str = '12:30'
    day_offset: int = 1
    sport: str = 'badminton'
    campus: str = '1'
    venue: str = '运动广场东馆羽毛球场'
    slots: list = field(default_factory=lambda: ['19:00-20:00'])
    preferred: str = 'B4-6,C4-6'
    avoided: str = 'D7-8,A7-8'
    auto_pay: bool = False
    max_cents: int = 3000
    cancel_minutes: int = 60
    valid_from: str = ''
    valid_until: str = ''

    def validate(self):
        if not self.id or not self.name.strip():
            raise ValueError('计划名称不能为空')
        if not self.weekdays or any(type(x) is not int or x not in range(7) for x in self.weekdays):
            raise ValueError('请选择抢场星期')
        datetime.strptime(self.fire_time, '%H:%M')
        if self.sport not in ('badminton', 'gym') or self.campus not in ('1', '2'):
            raise ValueError('项目或校区错误')
        if not 0 <= self.day_offset <= 7 or not 60 <= self.cancel_minutes <= 10080:
            raise ValueError('预约日偏移应为0–7天；自动取消至少提前60分钟')
        if not 0 < self.max_cents <= 100000 or not self.slots or len(set(self.slots)) != len(self.slots):
            raise ValueError('付款上限或时段不合法')
        if not self.venue.strip():
            raise ValueError('请填写准确场馆名称，避免跨馆预约')
        for slot in self.slots:
            if not re.fullmatch(r'\d{2}:\d{2}-\d{2}:\d{2}', slot):
                raise ValueError('时段格式应为19:00-20:00')
            start, end = [datetime.strptime(x, '%H:%M') for x in slot.split('-')]
            if start >= end:
                raise ValueError('结束时间必须晚于开始时间，不支持跨日时段')
        intervals = sorted(tuple(slot.split('-')) for slot in self.slots)
        if any(a[1] > b[0] for a, b in zip(intervals, intervals[1:])):
            raise ValueError('时段不能重叠')
        court_codes(self.preferred)
        court_codes(self.avoided)
        for date in (self.valid_from, self.valid_until):
            if date:
                datetime.strptime(date, '%Y-%m-%d')
        if self.valid_from and self.valid_until and self.valid_from > self.valid_until:
            raise ValueError('生效日期范围错误')
        return self

    def due(self, moment):
        day = moment.date().isoformat()
        if not self.enabled or moment.weekday() not in self.weekdays:
            return False
        if (self.valid_from and day < self.valid_from) or (self.valid_until and day > self.valid_until):
            return False
        fire = datetime.combine(moment.date(), datetime.strptime(self.fire_time, '%H:%M').time(), SHANGHAI)
        # Small catch-up window only; never unexpectedly book hours after a restart.
        return 0 <= (moment - fire).total_seconds() < 180

    def next_fire(self, moment):
        for offset in range(370):
            day = moment.date() + timedelta(days=offset)
            fire = datetime.combine(day, datetime.strptime(self.fire_time, '%H:%M').time(), SHANGHAI)
            if fire >= moment and self.due(fire):
                return fire
        return None

    def to_dict(self):
        return asdict(self)
