"""Fail closed on the frozen V1.1 research configuration and V1.0 blobs."""

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RULES = ROOT / "backtest/rules_v1_1_research.json"
PREREG = ROOT / "docs/backtest/V1_1_B_PREREGISTRATION.md"
INPUT_HASH = "10ba156037d0ce2cec0c1b8489c78e38185e2e7de37d34a168e1693d508ac5b1"
BASE_BLOBS = {
    "backtest/rules_v1.json": "021422a216d7cc72934c848c3e06d19be9f0dc40",
    "backtest/stage2a_policy.py": "61b44ea948522868028f8787467bf9d63f0353f2",
    "backtest/development_research.py": "5b15452feff65c3feaa286083c855530a958ea9d",
}


def git_blob(path):
    data = path.read_bytes()
    return hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()


def validate(path=RULES, *, check_prereg=True):
    rules = json.loads(Path(path).read_text(encoding="utf-8"))
    if (rules["version"] != "jlgs-v1.1-b-development-research-preregistered" or
            rules["approval"] != "qi_six_A_commander_PASS_WITH_CHANGE" or
            rules["ready_for_stage2b"] is not False or rules["research_only"] is not True):
        raise ValueError("V1.1 research rules drift")
    if (rules["source"] != "backtest/local_data/jlgs_dev_only.json" or
            rules["source_sha256"] != INPUT_HASH or rules["sessions"] != 485 or
            (rules["first_date"], rules["last_date"]) != ("2023-10-09", "2025-09-30") or
            rules["formal_minimum_net_threshold"] is not None):
        raise ValueError("source or formal threshold drift")
    if (rules["units"] != 10000 or rules["batch_units"] != 2500 or
            rules["max_unrebought_fraction"] != "0.50" or
            rules["box"] != ["2.64", "3.00"] or
            rules["sell_triggers"] != ["2.95", "3.00", None, None] or
            rules["sell_open_band"] != ["2.64", "3.00"] or
            rules["regular_buy_bands"] != [["2.76", "2.82"], ["2.62", "2.68"]] or
            rules["risk_execution_permission"] != "offline_simulation_only_formal_use_manual_qi"):
        raise ValueError("fixed policy drift")
    if rules["main"] != {"daily_breakout": "3.00", "risk_wait_sessions": 5,
                          "regular_net_per_share": "0.010", "d_state": "D0"}:
        raise ValueError("main rule drift")
    if rules["single_factor"] != {
            "breakout_295": {"daily_breakout": "2.95"},
            "wait_10": {"risk_wait_sessions": 10},
            "net_000": {"regular_net_per_share": "0.000"},
            "net_020": {"regular_net_per_share": "0.020"}}:
        raise ValueError("sensitivity drift")
    if rules["slippages"] != ["0", "0.01", "0.02"] or rules["d1_research_only"] != ["D1-N1", "D1-N2"]:
        raise ValueError("scenario drift")
    if check_prereg and not PREREG.is_file():
        raise ValueError("preregistration missing")
    blobs = {name: git_blob(ROOT / name) for name in BASE_BLOBS}
    if blobs != BASE_BLOBS:
        raise ValueError(f"V1.0 baseline blob drift: {blobs}")
    return rules, blobs


if __name__ == "__main__":
    rules, blobs = validate()
    print("V1.1 static validation PASS; ready_for_stage2b=false; V1.0 blobs=" + json.dumps(blobs, sort_keys=True))
