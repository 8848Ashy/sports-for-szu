from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from .models import Plan, SHANGHAI


@dataclass
class Schedule:
    id: str
    name: str
    config: dict
    kind: str = 'weekly'
    enabled: bool = True
    fire_time: str = '12:30:00'
    start_date: str = ''
    end_date: str = ''
    weekdays: list = field(default_factory=lambda: [0, 2, 4])
    date_mode: str = 'offset'
    day_offset: int = 1
    target_date: str = ''
    duration_minutes: int = 0
    catchup_seconds: int = 180
    resume_on_restart: bool = True

    def validate(self):
        if not self.id or not self.name.strip() or self.kind not in ('once', 'weekly'):
            raise ValueError('请填写定时名称并选择单次或每周')
        if len(self.fire_time) == 5:
            self.fire_time += ':00'
        datetime.strptime(self.fire_time, '%H:%M:%S')
        datetime.strptime(self.start_date, '%Y-%m-%d')
        if self.end_date:
            datetime.strptime(self.end_date, '%Y-%m-%d')
            if self.end_date < self.start_date:
                raise ValueError('结束日不能早于生效日')
        if self.kind == 'weekly' and (not self.weekdays or any(type(d) is not int or d not in range(7) for d in self.weekdays)):
            raise ValueError('请选择每周生效日')
        if self.date_mode not in ('offset', 'fixed') or not 0 <= self.day_offset <= 7:
            raise ValueError('预约日期规则不合法')
        if self.date_mode == 'fixed':
            datetime.strptime(self.target_date, '%Y-%m-%d')
        if not 0 <= self.duration_minutes <= 1440 or not 1 <= self.catchup_seconds <= 300:
            raise ValueError('蹲退时长0–1440分钟，补执行窗口1–300秒')
        Plan(**self.config).validate()
        return self

    def fire_on(self, day):
        text = day.isoformat()
        if not self.enabled or text < self.start_date or (self.end_date and text > self.end_date):
            return None
        if self.kind == 'once' and text != self.start_date:
            return None
        if self.kind == 'weekly' and day.weekday() not in self.weekdays:
            return None
        return datetime.combine(day, datetime.strptime(self.fire_time, '%H:%M:%S').time(), SHANGHAI)

    def due(self, moment):
        fire = self.fire_on(moment.date())
        return fire if fire and 0 <= (moment - fire).total_seconds() < self.catchup_seconds else None

    def next_fire(self, moment):
        for offset in range(370):
            fire = self.fire_on(moment.date() + timedelta(days=offset))
            if fire and fire >= moment:
                return fire
        return None

    def booking_date(self, fire):
        return self.target_date if self.date_mode == 'fixed' else (fire.date() + timedelta(days=self.day_offset)).isoformat()

    def to_dict(self):
        return asdict(self)
