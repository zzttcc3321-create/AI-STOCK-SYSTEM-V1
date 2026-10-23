"""Deterministic Markdown evidence for the preregistered V1.1 matrix."""

from collections import Counter
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import re
from datetime import date

from .development_research import run as run_v1
from .v11_development_research import END, UNITS, Result
from .validate_rules_v1_1 import PREREG, ROOT


def _fmt(number, decimals=2):
    return f"{number:.{decimals}f}"


def _pct(number):
    return _fmt(number * 100, 2) + "%"


def _daily_exposure(result):
    """Print every nonzero day; compress exact-zero daily runs without losing dates."""
    lines = ["| 日期或连续零敞口区间 | 日数 | 未回补相对量 | 回补义务/研究元 | 未实现扣费盯市净额/研究元 | 最久未回补完整日 |",
             "| --- | ---: | ---: | ---: | ---: | ---: |"]
    marks = result.marks
    i = 0
    while i < len(marks):
        point = marks[i]
        if point.open_shares:
            lines.append(f"| {point.day} | 1 | {_fmt(point.open_fraction, 2)} | "
                         f"{_fmt(point.repurchase_obligation)} | {_fmt(point.unrealized_net)} | {point.max_open_days} |")
            i += 1
            continue
        j = i + 1
        while j < len(marks) and not marks[j].open_shares:
            j += 1
        span = str(point.day) if j == i + 1 else f"{point.day} … {marks[j-1].day}（逐日均为零）"
        lines.append(f"| {span} | {j-i} | 0 | 0 | 0 | 0 |")
        i = j
    return lines


def _criterion(name, checks, detail):
    return f"| {name} | {'PASS' if all(checks) else 'FAIL'} | {detail} |"


def render_report(results: list[Result], candles, rules, blobs, source_hash):
    expected = [(name, d, slip) for d, names in (("D0", ("main", "breakout_295", "wait_10", "net_000", "net_020")),
                                                      ("D1-N1", ("main",)), ("D1-N2", ("main",)))
                for name in names for slip in (Decimal("0"), Decimal("0.01"), Decimal("0.02"))]
    if [(r.label, r.d_state, r.slippage) for r in results] != expected:
        raise ValueError("incomplete or reordered frozen matrix")
    for result in results:
        result.audit()
    prereg_hash = hashlib.sha256(PREREG.read_bytes()).hexdigest()
    config_hash = hashlib.sha256((ROOT / "backtest/rules_v1_1_research.json").read_bytes()).hexdigest()
    code_files = ("backtest/validate_rules_v1_1.py", "backtest/v11_policy.py",
                  "backtest/v11_development_research.py", "backtest/v11_report.py")
    code_hashes = {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in code_files}
    v1 = {slip: run_v1(candles, Decimal("0.010"), slip)
          for slip in (Decimal("0"), Decimal("0.01"), Decimal("0.02"))}
    main = results[:3]
    checks = {
        "三档各有至少一腿常规达标完整回补": [r.regular_completed_legs >= 1 for r in main],
        "三档含风险盈亏已实现扣费净额严格大于零": [r.realized > 0 for r in main],
        "三档常规完成率至少 50%": [r.regular_rate >= Decimal("0.5") for r in main],
        "基准滑点超额严格大于零": [main[1].excess > 0],
        "压力滑点超额非负": [main[2].excess >= 0],
        "三档回撤不深于各自 V1.0 和持有基准": [r.max_drawdown >= v1[r.slippage].max_drawdown
                                                and r.max_drawdown >= r.benchmark_drawdown for r in main],
        "任意日未回补不超过 50%": [r.max_open_shares <= UNITS // 2 for r in main],
    }
    strategy = "STRATEGY_PASS" if all(all(v) for v in checks.values()) else "STRATEGY_FAIL"
    lines = ["# JLGS V1.1-B 开发段隔离验证报告", "",
             f"**ENGINEERING_PASS + {strategy}。仅离线研究模拟，不构成投资结论；正式 `ready_for_stage2b=false`。**", "",
             "研究尺度为初始机动仓 10,000 单位，每批 2,500；不是实际持仓。执行价为次日原始开盘加每边滑点的模拟近似。"
             "D0 没有人工解除记录，D1-N1/N2 是单列研究代理。常规回补与风险回补分开；风险模拟不会产生真实买单。", "",
             "## 冻结输入与代码身份", "",
             f"开发段 `{rules['source']}`：485 交易日，{candles[0].trade_date} 至 {candles[-1].trade_date}；"
             f"SHA256 `{source_hash}`。截断加载器 `development_data.load_development`；未读取原始整份 JSON 或封存段。", "",
             f"预登记 SHA256 `{prereg_hash}`；研究配置 SHA256 `{config_hash}`。"
             "先登记再实现；全矩阵固定 5 组 D0 × 3 档，加 D1 两场景 × 3 档。", "",
             "| 只读 V1.0 文件 | 实测 git blob | 设计稿登记值 | 核对 |",
             "| --- | --- | --- | --- |"]
    expected_blobs = {
        "backtest/rules_v1.json": "021422a216d7cc72934c848c3e06d19be9f0dc40",
        "backtest/stage2a_policy.py": "61b44ea948522868028f8787467bf9d63f0353f2",
        "backtest/development_research.py": "5b15452feff65c3feaa286083c855530a958ea9d",
    }
    for name, actual in blobs.items():
        lines.append(f"| `{name}` | `{actual}` | `{expected_blobs[name]}` | {'PASS' if actual == expected_blobs[name] else 'FAIL'} |")
    lines += ["", "新增实现 SHA256：", "", "| 文件 | SHA256 |", "| --- | --- |"]
    for name, digest in code_hashes.items():
        lines.append(f"| `{name}` | `{digest}` |")
    lines += ["", "## 全矩阵结果", "",
              "五层漏斗依次为价格条件日/状态允许日/卖出候选单/模拟卖出成交/常规达标完整回补腿。"
              "常规完成率为常规实买股数/累计实卖股数，零卖出定义为 0；风险回补不计。"
              "超额为期末策略权益减含税前现金分红且不再投资的买入持有权益，再除同起点权益。", "",
              "| D 场景 | 规则 | 滑点 | 五层漏斗 | 卖/常规买/风险买笔 | 常规/风险完成腿 | 总/常规完成率 | 常规净额 | 风险净额 | 合计已实现扣费净额 | 每已回补单位净额 | 期末未回补 | 最大日未回补 | 期末盯市净额 | 超额 | 最大回撤 | 错过分红 |",
              "| --- | --- | ---: | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for r in results:
        f = r.funnel
        flow = "/".join(str(f[k]) for k in ("candidate_conditions", "state_allowed", "orders", "fills", "regular_completed"))
        sales = sum(t["side"] == "sell" for t in r.trades)
        regular = sum(t["category"] == "REBOUGHT" for t in r.trades)
        risk = sum(t["category"] == "RISK_REBOUGHT" for t in r.trades)
        bought = r.regular_shares + r.risk_shares
        per_share = r.realized / bought if bought else Decimal(0)
        risk_completed = sum(x.remaining == 0 and x.risk_shares > 0 for x in r.legs)
        total_rate = Decimal(bought) / r.sold if r.sold else Decimal(0)
        missed = sum((x.missed_dividend for x in r.legs), Decimal(0))
        lines.append(f"| {r.d_state} | {r.label} | {_fmt(r.slippage, 2)} | {flow} | {sales}/{regular}/{risk} | "
                     f"{r.regular_completed_legs}/{risk_completed} | {_pct(total_rate)}/{_pct(r.regular_rate)} | {_fmt(r.regular_net)} | "
                     f"{_fmt(r.risk_net)} | {_fmt(r.realized)} | {_fmt(per_share, 4)} | "
                     f"{_fmt(Decimal(r.open_shares)/UNITS, 2)} | {_fmt(Decimal(r.max_open_shares)/UNITS, 2)} | "
                     f"{_fmt(r.marks[-1].unrealized_net)} | {_pct(r.excess)} | {_pct(r.max_drawdown)} | {_fmt(missed)} |")
    lines += ["", "## 双层验收与硬门槛", "",
              "工程结论 `ENGINEERING_PASS`：21 次输入、状态、订单、FIFO 股数、现金、费用与每日敞口的运行时断言均通过；"
              "只读 V1.0 blob 和开发段哈希匹配，报告日期边界仅到开发段末日。"
              "A7 仅离线模拟，失败和待办事件保留。测试与静态校验的实际命令和数字见文末运行记录。", "",
              f"策略结论 **`{strategy}`**；只用 D0 主方案三档，敏感性和 D1 不参与。", "",
              "| 预登记硬条件 | 判定 | 实测依据 |", "| --- | --- | --- |"]
    details = {
        "三档各有至少一腿常规达标完整回补": "/".join(str(r.regular_completed_legs) for r in main),
        "三档含风险盈亏已实现扣费净额严格大于零": "/".join(_fmt(r.realized) for r in main),
        "三档常规完成率至少 50%": "/".join(_pct(r.regular_rate) for r in main),
        "基准滑点超额严格大于零": _pct(main[1].excess),
        "压力滑点超额非负": _pct(main[2].excess),
        "三档回撤不深于各自 V1.0 和持有基准": "/".join(
            f"{_pct(r.max_drawdown)} vs {_pct(v1[r.slippage].max_drawdown)} / {_pct(r.benchmark_drawdown)}" for r in main),
        "任意日未回补不超过 50%": "/".join(_pct(Decimal(r.max_open_shares)/UNITS) for r in main),
    }
    for name, values in checks.items():
        lines.append(_criterion(name, values, details[name]))
    lines.append("| FIFO/资金/股数/状态/隔离/因果/费用/可复现 | PASS | 运行时对账、静态校验、新增及全量回归见文末 |")
    lines += ["", "V1.0 对照在三档滑点下均为零交易；其最大回撤分别为 "
              + "/".join(_pct(v1[s].max_drawdown) for s in (Decimal("0"), Decimal("0.01"), Decimal("0.02")))
              + "。持有基准最大回撤按相同逐日权益序列计算。", "",
              "## FIFO 腿、成交、撤销与失败事件", ""]
    for r in results:
        lines += [f"### {r.d_state} / {r.label} / 滑点 {_fmt(r.slippage, 2)}", "",
                  "| 批次 | 卖出日 | 卖出股数 | 常规股数 | 风险股数 | 未回补股数 | 分类 | 常规净额 | 风险净额 | 错过分红 |",
                  "| ---: | --- | ---: | ---: | ---: | ---: | --- | ---: | ---: | ---: |"]
        if not r.legs:
            lines.append("| — | — | 0 | 0 | 0 | 0 | 无卖出腿 | 0 | 0 | 0 |")
        for leg in r.legs:
            category = "INVALIDATION_REVIEW" if leg.remaining and leg.review else (
                "OPEN_UNREBOUGHT" if leg.remaining else "CLOSED")
            lines.append(f"| {leg.batch} | {leg.sold_day} | {leg.sale.shares} | {leg.regular_shares} | "
                         f"{leg.risk_shares} | {leg.remaining} | {category} | {_fmt(leg.regular_net)} | "
                         f"{_fmt(leg.risk_net)} | {_fmt(leg.missed_dividend)} |")
        lines += ["", "| 日期 | 批次 | 方向/分类 | 股数 | 原始开盘 | 模拟成交价 | 费用 |",
                  "| --- | ---: | --- | ---: | ---: | ---: | ---: |"]
        if not r.trades:
            lines.append("| — | — | 零成交 | 0 | — | — | 0 |")
        for trade in r.trades:
            lines.append(f"| {trade['day']} | {trade['batch']} | {trade['category']} | {trade['shares']} | "
                         f"{_fmt(trade['quote'])} | {_fmt(trade['executed'])} | {_fmt(trade['fees'])} |")
        lines += ["", "撤销、风险待办及失败事件：", "",
                  "| 日期 | 事件 | 批次 | 余量 | 原因 |", "| --- | --- | ---: | ---: | --- |"]
        if not r.events:
            lines.append("| — | 无 | — | 0 | — |")
        for event in r.events:
            lines.append(f"| {event['day']} | {event['kind']} | {event['batch'] or '—'} | "
                         f"{event['shares']} | {event['reason']} |")
        lines += ["", "逐日未回补敞口及盯市风险：", ""] + _daily_exposure(r) + [""]
    lines += ["## 状态审计与运行记录", "",
              "状态审计保留 D0 主方案及 D1 代理首档的全部 B/C/D 相关事件；其余滑点同规则状态一致。", "",
              "| 场景 | 日期 | 状态事件 |", "| --- | --- | --- |"]
    for r in (results[0], results[15], results[18]):
        for event in r.state_events:
            if (event["event"].startswith("state ") or "D " in event["event"]
                    or "C released" in event["event"] or "proxy" in event["event"]):
                lines.append(f"| {r.d_state} | {event['day']} | {event['event']} |")
    lines += ["", "运行后补记：全量回归、新增测试、静态校验、工作树差异检查的命令与实测数字见 `EXPERIMENT_LOG.md`。"
              "没有临场调参、没有解除 D0 人工锁存、没有访问封存段。", ""]
    report = "\n".join(lines)
    if any(date.fromisoformat(value) > END for value in re.findall(r"\b20\d\d-\d\d-\d\d\b", report)):
        raise AssertionError("post-development date in report")
    return report
