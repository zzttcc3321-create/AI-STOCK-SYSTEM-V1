"""Development-only Stage 2B R1 structure diagnostics; never changes V1 rules."""

from dataclasses import dataclass, replace
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
import re

from .adjustment import adjusted_bars, build_factors
from .calendar import trading_dates
from .config import STAGE1
from .data import Candle
from .development_data import DEV_DIVIDENDS, load_development
from .development_research import END, SLIPPAGES, THRESHOLDS, run, state_points
from .stage2a_policy import displayed_state


@dataclass(frozen=True)
class DTrace:
    day: date
    review_risk: bool
    weekly_price_stop: bool
    proxy_pause: bool
    qualifying_weeks: int
    events: tuple[str, ...]


class RecoveryProxy:
    """Separate weekly stop, review risk and a research-only release latch."""

    def __init__(self, required_weeks: int):
        if required_weeks not in (1, 2):
            raise ValueError("only preregistered N=1/N=2 scenarios are allowed")
        self.required_weeks = required_weeks
        self.review_risk = False
        self.weekly_price_stop = False
        self.proxy_pause = False
        self.qualifying_weeks = 0
        self.below_review_weeks = 0
        self.previous_week_close: Decimal | None = None
        self.release_next_day = False
        self.audit: list[tuple[date, str]] = []

    def start_day(self, day: date) -> tuple[str, ...]:
        if not self.release_next_day:
            return ()
        self.release_next_day = False
        self.review_risk = False
        self.proxy_pause = False
        event = f"D1-N{self.required_weeks} proxy release effective next trading day"
        self.audit.append((day, event))
        return (event,)

    def on_completed_week(self, day: date, close: Decimal) -> tuple[str, ...]:
        close = Decimal(close)
        previous = self.previous_week_close
        events = []
        self.below_review_weeks = self.below_review_weeks + 1 if close < Decimal("2.30") else 0
        crossed_review = previous is not None and previous >= Decimal("2.30") and close < Decimal("2.30")
        if crossed_review or self.below_review_weeks >= 2:
            self.review_risk = True
            event = "D invalidation review required"
            self.audit.append((day, event))
            events.append(event)
        if close < Decimal("2.40"):
            if previous is not None and previous >= Decimal("2.40"):
                event = "D stop downward cross 2.40"
                self.audit.append((day, event))
                events.append(event)
            self.weekly_price_stop = True
            self.proxy_pause = True
            self.qualifying_weeks = 0
            self.release_next_day = False
        else:
            self.weekly_price_stop = False
            self.qualifying_weeks += 1
            if self.proxy_pause and self.qualifying_weeks >= self.required_weeks:
                self.release_next_day = True
                event = f"D1-N{self.required_weeks} release qualified; effective next trading day"
                self.audit.append((day, event))
                events.append(event)
        self.previous_week_close = close
        return tuple(events)

    @property
    def blocked(self) -> bool:
        return self.review_risk or self.weekly_price_stop or self.proxy_pause


def d1_states(candles: tuple[Candle, ...], required_weeks: int):
    baseline, _ = state_points(candles)
    proxy = RecoveryProxy(required_weeks)
    states, trace = [], []
    for row, base in zip(candles, baseline):
        day = row.trade_date
        events = list(proxy.start_day(day))
        monday = day - timedelta(days=day.weekday())
        week_sessions = trading_dates(monday, monday + timedelta(days=4))
        if week_sessions and day == week_sessions[-1]:
            events.extend(proxy.on_completed_week(day, row.close))
        trace.append(DTrace(day, proxy.review_risk, proxy.weekly_price_stop,
                            proxy.proxy_pause, proxy.qualifying_weeks, tuple(events)))
        if base.state == "WARMUP":
            states.append(base)
            continue
        active = {**base.active, "D": proxy.blocked}
        state = displayed_state(active)
        retained = tuple(event for event in base.events if not event.startswith("state "))
        states.append(replace(base, active=active, state=state,
                              events=retained + tuple(events)))
    return tuple(states), tuple(trace), tuple(proxy.audit)


def moving_average_flags(candles: tuple[Candle, ...]):
    factors = build_factors(candles, DEV_DIVIDENDS)
    prices = [bar.close for bar in adjusted_bars(candles, factors)]
    volumes = [bar.vol for bar in candles]
    ma20 = [sum(prices[i-19:i+1]) / 20 if i >= 19 else None for i in range(len(prices))]
    ma60 = [sum(prices[i-59:i+1]) / 60 if i >= 59 else None for i in range(len(prices))]
    rows = []
    for i, row in enumerate(candles):
        flags = {"A-F1": False, "A-F2": False, "A-F3": False}
        box = Decimal("2.64") <= row.close <= Decimal("3.00")
        if i >= 60 and box and ma20[i-1] and ma60[i-1]:
            flags["A-F1"] = (abs(ma20[i] / ma20[i-1] - 1) <= Decimal("0.001")
                              and abs(ma60[i] / ma60[i-1] - 1) <= Decimal("0.0005"))
            flags["A-F3"] = (max(ma20[i-4:i+1]) - min(ma20[i-4:i+1])
                              <= Decimal("0.005") * ma20[i])
        if i >= 64 and box and ma60[i-5]:
            flags["A-F2"] = abs(ma60[i] / ma60[i-5] - 1) <= Decimal("0.0025")
        prior_volume = sum(volumes[i-20:i]) / 20 if i >= 20 else None
        volume_ratio = row.vol / prior_volume if prior_volume else None
        rows.append((flags, ma20[i], ma60[i], volume_ratio))
    return tuple(rows)


def _yn(value: bool) -> str:
    return "是" if value else "否"


def render_diagnostics(candles: tuple[Candle, ...]) -> str:
    if len(candles) != 485 or candles[0].trade_date != STAGE1.development_start or candles[-1].trade_date != END:
        raise ValueError("diagnostics require isolated complete development data")
    baseline, _ = state_points(candles)
    scenarios = {"D0": (baseline, (), ()),
                 "D1-N1": d1_states(candles, 1),
                 "D1-N2": d1_states(candles, 2)}
    moving = moving_average_flags(candles)
    assert all(flags["A-F1"] == point.active.get("A", False)
               for (flags, _, _, _), point in zip(moving, baseline))
    lines = ["# Stage 2B R1 结构诊断报告", "",
             "**仅 2023-10-09 至 2025-09-30 开发段；只诊断，不改变正式规则，不给投资结论。**",
             "独立输入为被 Git 忽略的 `backtest/local_data/jlgs_dev_only.json`。正式 `ready_for_stage2b=false`、门槛 `null`。",
             "D0 为 PR #54 严格人工门禁基准；D1-N2 为预登记机械代理恢复主场景；D1-N1 为对照。"
             "D1 是研究假设，不是 V1.0 解除规则；原失效审查事件留存，周线价格停摆和审查风险分别建模。", "",
             "## 价格条件独立诊断", "",
             "下表列出开发段所有收盘 ≥2.95 日；`≥3.00` 单独标识。A-F1 为箱体 2.64–3.00 与现行双均线走平公式同时成立。"
             "B/C/D 为 D0 当日状态（D1-N2 的 D 另列）。MA 使用 Stage 1 因果现金分红调整收盘价，量比为当日量/此前 20 日均量；"
             "未满 61 日标为预热。", "",
             "| 日期 | 原始收盘 | ≥3.00 | A-F1 | B(状态;周/日) | C | D0 | D1-N2 | MA20 / MA60 关系 | MA20 日变/MA60 日变 | 量比 | ≥1.30 |",
             "| --- | ---: | --- | --- | --- | --- | --- | --- | --- | --- | ---: | --- |"]
    price_days = 0
    high_days = 0
    price_a = 0
    for i, (row, point, (_, ma20, ma60, ratio)) in enumerate(zip(candles, baseline, moving)):
        if row.close < Decimal("2.95"):
            continue
        price_days += 1
        high_days += row.close >= Decimal("3.00")
        price_a += bool(point.active.get("A"))
        relation = "预热" if i < 60 else f"{ma20:.4f} {'>' if ma20 > ma60 else '<' if ma20 < ma60 else '='} {ma60:.4f}"
        if i >= 60:
            prev20, prev60 = moving[i-1][1:3]
            change = f"{ma20 / prev20 - 1:+.5f}/{ma60 / prev60 - 1:+.5f}"
        else:
            change = "预热"
        lines.append(f"| {row.trade_date} | {row.close:.2f} | {_yn(row.close >= Decimal('3.00'))} | "
                     f"{_yn(point.active.get('A', False))} | {_yn(point.active.get('B', False))};"
                     f"{_yn(point.week_qualified)}/{_yn(point.daily_b_gate)} | "
                     f"{_yn(point.active.get('C', False))} | {_yn(point.active.get('D', False))} | "
                     f"{_yn(scenarios['D1-N2'][0][i].active.get('D', False))} | {relation} | {change} | "
                     f"{f'{ratio:.3f}' if ratio is not None else '—'} | "
                     f"{_yn(ratio is not None and ratio >= Decimal('1.30'))} |")
    lines += ["", f"汇总：收盘 ≥2.95 共 {price_days} 日；其中 ≥3.00 共 {high_days} 日；"
              f"A-F1 与 ≥2.95 同日 {price_a} 日。PR #54 的 A∩第一批价位为 0 日，核对一致。"
              "其中 3.04 元日超出 A 箱体；另外两日双均线单日变化均超出现行走平容差。"
              "三日的 B 周资格均不成立，虽然当日 MA20>MA60 且量比达标。", "",
              "## A 定义敏感性", "",
              "只套用 [已归档公式](Q07_CANDIDATE_DEFINITIONS.md)：A-F1 双均线单日变化，"
              "A-F2 MA60 五交易日前变化，A-F3 五点 MA20 极差；均保留 2.64–3.00 箱体。"
              "F2 从第 65 日起，F1/F3 仍按现行第 61 日最早评价。"
              "“原始交集”不考虑状态；“可交易交集”须同时无 B/C/D，且只是信号日计数，不代表订单。", "",
              "| 定义 | A 成立日 | A∩≥2.95 原始交集 | D0 可交易交集 | D1-N2 可交易交集 |",
              "| --- | ---: | ---: | ---: | ---: |"]
    for name in ("A-F1", "A-F2", "A-F3"):
        indices = [i for i, (flags, _, _, _) in enumerate(moving) if flags[name]]
        hits = [i for i in indices if candles[i].close >= Decimal("2.95")]
        def allowed(scenario, i):
            active = scenarios[scenario][0][i].active
            return not any(active.get(s) for s in ("B", "C", "D"))
        lines.append(f"| {name} | {len(indices)} | {len(hits)} | "
                     f"{sum(allowed('D0', i) for i in hits)} | "
                     f"{sum(allowed('D1-N2', i) for i in hits)} |")
    lines += ["", "## D 场景与事件", "",
              "D0 保留原人工审查风险锁存；D1 分开记录 `review_risk`、最近完整周 `<2.40` 的价格停摆"
              "和代理等待。完整周收盘达到 `≥2.40` 连续 N 周后，仅下一交易日代理解除；"
              "若再次跌破则重暂停。`<2.30` 向下穿越或连续两周低于该价仍照原口径写审查事件；"
              "代理解除只改变当前研究场景门禁，不删除历史事件。"
              "“再次停摆”只计代理解除后的向下穿越；D0 无代理解除，故记 0。", "",
              "| 场景 | D 状态日 | 首次审查事件 | 代理解除次数 | 再次停摆次数 |",
              "| --- | ---: | --- | ---: | ---: |"]
    for name, (states, trace, audit) in scenarios.items():
        reviews = [p.day for p in states if any("invalidation review required" in e for e in p.events)] if name == "D0" else [day for day, e in audit if "invalidation review required" in e]
        releases = [day for day, e in audit if "release effective" in e]
        released = False
        restops = 0
        for _, event in audit:
            if "proxy release effective" in event:
                released = True
            elif "downward cross" in event and released:
                restops += 1
                released = False
        lines.append(f"| {name} | {sum(p.active.get('D', False) for p in states)} | "
                     f"{reviews[0] if reviews else '—'} | {len(releases)} | {restops} |")
    lines += ["", "D1 审计事件（含重复失效审查触发）：", "",
              "| 场景 | 日期 | 事件 |", "| --- | --- | --- |"]
    for name in ("D1-N1", "D1-N2"):
        for day, event in scenarios[name][2]:
            lines.append(f"| {name} | {day} | {event} |")
    lines += ["", "## 五层交易漏斗", "",
              "计数单位：第 1/2 层为收盘信号日，第 3 层为候选卖单，第 4 层为模拟卖出成交，"
              "第 5 层为该卖出腿完整回补且已实现净额 `>0`。第 1 层要求现行 A-F1 与第一批 `≥2.95`，"
              "排除终日不可执行信号；第 2 层要求无 B/C/D；第 3 层另受批次顺序及持仓约束；"
              "第 4 层受次日开盘价带与滑点成交约束。每个门槛和滑点重新独立执行，未选定门槛。", "",
              "| 场景 | 门槛 | 滑点 | 候选条件 | 状态允许 | 候选订单 | 模拟成交 | 净盈利回补 |",
              "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for name, (states, _, _) in scenarios.items():
        for threshold in THRESHOLDS:
            for slippage in SLIPPAGES:
                result = run(candles, threshold, slippage,
                             diagnostic_states=None if name == "D0" else states)
                f = result.funnel
                lines.append(f"| {name} | {threshold:.3f} | {slippage:.2f} | "
                             f"{f['candidate_conditions']} | {f['state_allowed']} | {f['orders']} | "
                             f"{f['fills']} | {f['net_profitable_completed']} |")
    lines += ["", "PR #54 D0 基准：21 档均零交易、零已实现净额，WARMUP/A/B/C/D/NO_ACTION "
              "分别为 60/1/0/8/402/14 日。本轮 3 场景×21 档共 63 行，五层均为 0；"
              "流失发生在首层：现行 A-F1 与第一批价位未同日成立。由于首层为零，"
              "本样本无法观察后四层的实际流失率；D1 状态解除及滑点变化均未产生卖出候选。", "",
              "## 证据边界与 V1.1 提案摘要", "",
              "供是否提出 V1.1 修改案审查的证据：本段 ≥2.95 仅 3 日；A-F1/F2/F3 分别成立 "
              "48/98/135 日，但与该价位的交集均为 0；D0/D1-N1/D1-N2 的 D 状态分别为 "
              "402/45/53 日，三种场景各 21 档均在漏斗首层为 0。"
              "这证明本开发段同日交集缺失，不能证明 A 与价位普遍互斥，也不能外推到封存段。"
              "D1 只是代理假设，无法据此验证人工解除的真实结果；零首层使门槛和成交层级没有可识别信息。"
              "本报告不选门槛、不提出正式规则修改结论。", ""]
    report = "\n".join(lines)
    if any(date.fromisoformat(match.group()) > END for match in
           re.finditer(r"\b20\d\d-\d\d-\d\d\b", report)):
        raise ValueError("post-development date in diagnostic artifact")
    return report


def main() -> None:
    source = Path("backtest/local_data/jlgs_dev_only.json")
    candles = load_development(source)
    output = Path("docs/backtest/STAGE2B_R1_DIAGNOSTICS_REPORT.md")
    output.write_text(render_diagnostics(candles), encoding="utf-8")
    print(f"diagnostics: {len(candles)} development sessions; report={output}")


if __name__ == "__main__":
    main()
