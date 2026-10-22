"""Markdown evidence for the bounded development research grid."""

from collections import Counter
from decimal import Decimal

from .development_research import END, SLIPPAGES, THRESHOLDS, UNITS, Result


def _num(value: Decimal, digits: int = 4) -> str:
    return f"{value:.{digits}f}"


def _pct(value: Decimal) -> str:
    return _num(value * 100, 2) + "%"


def _month_summary(result: Result) -> list[str]:
    groups = {}
    for point in result.equity:
        groups.setdefault(point.day.strftime("%Y-%m"), []).append(point)
    lines = ["| 月份 | 交易日 | 月末权益/初始单位 | 月内最低权益/初始单位 | 月内最深回撤 |",
             "| --- | ---: | ---: | ---: | ---: |"]
    for month, points in groups.items():
        lines.append(f"| {month} | {len(points)} | {_num(points[-1].equity / UNITS)} | "
                     f"{_num(min(p.equity for p in points) / UNITS)} | "
                     f"{_pct(min(p.drawdown for p in points))} |")
    return lines


def _representative(result: Result) -> list[str]:
    lines = [f"### 门槛 {_num(result.threshold, 3)}；滑点 {_num(result.slippage, 2)}",
             "", f"真实历史模拟成交 {len(result.trades)} 笔；已完成 {result.completed} 轮；"
             f"期末未回补 {sum(x.remaining for x in result.legs) / UNITS:.2f}；"
             f"累计费用 {_num(result.fees, 2)} 研究尺度元"
             f"（佣金 {_num(result.commission, 2)}，印花税 {_num(result.stamp, 2)}）；"
             f"已实现 T 净额 {_num(result.realized, 2)}，实收含税分红 {_num(result.dividends, 2)}；"
             f"相对基准收益率差 {_pct(result.return_difference)}；"
             f"最大回撤 {_pct(result.max_drawdown)}。", "",
             "成交价为原始未复权历史开盘价叠加本档滑点；数量只写初始机动仓相对量。", "",
             "| 成交日 | 批次 | 方向 | 相对量 | 原始开盘 | 模拟成交价 | 费用（研究尺度元） |",
             "| --- | ---: | --- | ---: | ---: | ---: | ---: |"]
    for trade in result.trades:
        lines.append(f"| {trade['day']} | {trade['batch']} | {trade['side']} | "
                     f"{_num(trade['quantity'], 2)} | {_num(trade['open'], 2)} | "
                     f"{_num(trade['price'], 2)} | {_num(trade['fees'], 2)} |")
    if not result.trades:
        lines.append("| — | — | 零交易 | 0 | — | — | 0 |")
    lines += ["", "FIFO 回补账本；未回补腿盯市额只作备查，不算已实现 T：", "",
              "| 批次 | 卖出日 | 回补日 | 类别 | 已回补相对量 | 余量 | 已实现净额（研究尺度元） | 错过分红备查 | 未回补腿末日盯市额 |",
              "| ---: | --- | --- | --- | ---: | ---: | ---: | ---: | ---: |"]
    for leg in result.legs:
        category = "REBOUGHT" if not leg.remaining else ("INVALIDATION_REVIEW" if leg.review else "OPEN_UNREBOUGHT")
        lines.append(f"| {leg.batch} | {leg.day} | {leg.buy_day or '—'} | {category} | "
                     f"{(leg.sale.shares - leg.remaining) / UNITS:.2f} | {leg.remaining / UNITS:.2f} | "
                     f"{_num(leg.realized, 2)} | {_num(leg.missed_dividend, 2)} | "
                     f"{_num(Decimal(leg.remaining) * result.last_close, 2)} |")
    if not result.legs:
        lines.append("| — | — | — | 无卖出腿 | 0 | 0 | 0 | 0 | 0 |")
    lines += ["", "盯市列仅按末日原始收盘价乘以未回补量，不计入已实现做 T 净额。", "",
              "逐日权益序列摘要（每月逐日计算后列末值、最小值和最深回撤）：", ""]
    lines += _month_summary(result)
    lines += ["", f"全期 {len(result.equity)} 个逐日权益点；首日权益/单位 "
              f"{_num(result.equity[0].equity / UNITS)}，末日 "
              f"{_num(result.equity[-1].equity / UNITS)}；"
              f"最深回撤日 {min(result.equity, key=lambda p: p.drawdown).day}。", ""]
    transitions = [(p.day, e) for p in result.states for e in p.events
                   if e.startswith("state ") or "C released" in e or "D " in e]
    lines += ["状态迁移及 D/C 事件摘要：", "",
              "| 日期 | 事件 |", "| --- | --- |"]
    for day, event in transitions:
        lines.append(f"| {day} | {event} |")
    if not transitions:
        lines.append("| — | 无迁移 |")
    canceled = Counter(item["reason"] for item in result.cancellations)
    lines += ["", "未成交候选撤销原因：" + ("；".join(f"{k} {v} 笔" for k, v in sorted(canceled.items()))
                                      if canceled else "无") + "。", ""]
    return lines


def render_report(results: list[Result], choice: Decimal | None) -> str:
    if len(results) != len(THRESHOLDS) * len(SLIPPAGES):
        raise ValueError("incomplete fixed grid")
    if (any(p.day > END for result in results for p in result.equity + list(result.states))
            or any(item["day"] > END for result in results
                   for item in result.trades + result.cancellations)
            or any(leg.day > END or (leg.buy_day is not None and leg.buy_day > END)
                   for result in results for leg in result.legs)):
        raise ValueError("post-boundary output forbidden")
    lines = ["# Stage 2B-D 开发段研究报告", "",
             "**开发段研究结果，不构成策略有效性结论，样本外未验证。**", "",
             "范围：2023-10-09 至 2025-09-30；485 个开发段交易日，61 日预热。"
             "模式 `development_research`；第三、四批禁用；正式 `ready_for_stage2b=false`，"
             "正式门槛仍为 `null`。初始机动仓=1.0，每批=0.25；逐笔费用使用 10,000 研究单位的纯假设会计尺度，"
             "不是实际持仓。结果含未回补风险、开盘跳空和滑点，均为日线开盘成交近似。", "",
             "## 固定网格结果", "",
             "完成率按已回补量/累计卖出量；零卖出定义为 0。每股净额按已回补研究单位，"
             "零回补记 0。收益率差为策略期末权益收益率减同起点买入持有收益率，"
             "后者含公告含税现金分红且不再投资。", "",
             "| 门槛 元/股 | 滑点 元/边 | 卖/买笔数 | 完成轮次 | 完成率 | 已实现净额 研究元 | 每已回补单位净额 | 未回补相对量 | 累计费用 研究元 | 相对基准收益率差 | 最大回撤 | 实收分红 研究元 | 分红占初始权益 | 错过分红备查 |",
             "| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for result in results:
        sells = sum(t["side"] == "sell" for t in result.trades)
        buys = sum(t["side"] == "buy" for t in result.trades)
        per_unit = result.realized / result.rebought if result.rebought else Decimal(0)
        lines.append(f"| {_num(result.threshold, 3)} | {_num(result.slippage, 2)} | {sells}/{buys} | "
                     f"{result.completed} | {_pct(result.completion_rate)} | {_num(result.realized, 2)} | "
                     f"{_num(per_unit, 4)} | {(result.sold-result.rebought)/UNITS:.2f} | "
                     f"{_num(result.fees, 2)} | {_pct(result.return_difference)} | "
                     f"{_pct(result.max_drawdown)} | {_num(result.dividends, 2)} | "
                     f"{_pct(result.dividends / result.equity[0].benchmark_equity)} | "
                     f"{_num(sum((x.missed_dividend for x in result.legs), Decimal(0)), 2)} |")
    states = Counter(point.state for point in results[0].states)
    base = results[0]
    start_price = base.equity[0].benchmark_equity / UNITS
    benchmark_return = base.equity[-1].benchmark_equity / base.equity[0].benchmark_equity - 1
    benchmark_div_cash = base.equity[-1].benchmark_equity - base.last_close * UNITS
    first_review = next((point.day for point in results[0].states
                         if any("invalidation review required" in event for event in point.events)), None)
    candidate_count = sum(t["side"] == "sell" for t in results[0].trades) + sum(
        t["side"] == "sell" for t in results[0].cancellations)
    a_condition_days = sum(point.active.get("A", False) for point in results[0].states)
    lines += ["", f"Stage 1 归一化基准：首日原始收盘 {_num(start_price, 2)} 元/单位，"
              f"末日原始收盘 {_num(base.last_close, 2)} 元/单位，"
              f"累计含税前分红 {_num(benchmark_div_cash / UNITS, 3)} 元/单位；"
              f"基准收益 {_pct(benchmark_return)}，其中分红贡献 {_pct(benchmark_div_cash / base.equity[0].benchmark_equity)}。", "",
              "状态/零交易核对："
              + "、".join(f"{name} {states[name]} 日" for name in
                         ("WARMUP", "A", "B", "C", "D", "NO_ACTION"))
              + f"；A 条件本身成立 {a_condition_days} 日（含被高优先级遮蔽），"
              + f"其中第一批价位同时成立 {base.first_trigger_price_days} 日；卖出候选 {candidate_count} 笔。"
              + (f"{first_review} 触发失效审查；研究模式没有人工解除记录，D 持续暂停。" if first_review else "")
              + ("预热后没有同时满足 A 与第一批收盘触发价的信号，故所有档位零交易。"
                 if candidate_count == 0 and all(not r.trades for r in results) else ""), "",
              "## 按预登记规则选择", "",
              (f"建议门槛：**{_num(choice, 3)} 元/股**。三档均至少完成一轮、已实现净额非负且完成率不低于 50%；"
               "在合格档中按三档单位化已实现净额中位数、最低完成率、较低门槛依次排序。"
               if choice is not None else
               "**无建议门槛。** 没有候选同时满足三档均至少完成一轮、已实现净额非负、完成率至少 50% 的预登记条件；未放宽规则。"), "",
              "## 代表性候选的历史明细", ""]
    representatives = []
    if choice is not None:
        representatives.extend(r for r in results if r.threshold == choice)
    for threshold in (THRESHOLDS[0], THRESHOLDS[-1]):
        case = next(r for r in results if r.threshold == threshold and r.slippage == Decimal("0.01"))
        if case not in representatives:
            representatives.append(case)
    for result in representatives:
        lines += _representative(result)
    anomalies = sorted(set(a for r in results for a in r.anomalies))
    lines += ["## 完整性与限制", "",
              f"开发段内量比/均线分母异常 {len(anomalies)} 项；" + ("；".join(anomalies) if anomalies else "无") + "。",
              "所有成交、权益和状态日期均经程序断言不超过 2025-09-30。"
              "周资格只在完整周确认，B 日线门逐日重验，C 的 2.64 边界及 D 优先级按 R1 复审记录。"
              "未建模盘口流动性、部分成交概率或个人股息税；研究会计尺度的最低佣金可能影响门槛，"
              "不可推断真实持仓收益。", "",
              "**数据隔离操作偏差：**开发前的格式探查曾以 `json.load` 解析整份原始 JSON，"
              "虽未输出或计算封存段行情，仍不符合严格的禁止读取要求。正式研究入口随后改为逐字符载入，"
              "遇首个越界日期就在该行价格字段前停止；本报告的 21 组计算只使用开发段。"
              "本次偏差随研究结果一并提交审查。", ""]
    return "\n".join(lines)
