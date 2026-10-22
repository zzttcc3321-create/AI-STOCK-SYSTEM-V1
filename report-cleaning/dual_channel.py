#!/usr/bin/env python3
"""双通道分析调度器 v2.1 - nohup 模式
主通道：Codex + gpt-6.1-sol (High) via nohup后台
备通道：阿七直接分析（Codex失败/超时时）

解决 dev-box SSH 4.5分钟断线问题：
- 不再用单条阻塞SSH等Codex跑完
- 改用：nohup后台启动 → 轮询检查 → 取结果文件

v2.1（2026-10-20晚）：启动命令改写脚本文件再 scp 上去，根治 v2 的 ssh 命令行嵌套引号
解析错误（16:30 主通道死因：unexpected EOF）
"""
import json
import logging
import re
import subprocess
import os
import sys
import time
import uuid
from datetime import datetime

CODEX_MODEL = "gpt-6.1-sol"
CODEX_REASONING = "high"
SSH_CONFIG = "/home/hatch/.ssh/config"
SSH_KEY = "/home/hatch/.ssh/dev_box_key"
# Codex分析超时：10分钟
CODEX_TIMEOUT = 600
# 轮询间隔：30秒
POLL_INTERVAL = 30

# 报告清洗内置于调度器，部署时无需额外模块。
_LOG = logging.getLogger(__name__)
_MARK = "[REPORT_CLEAN_FALLBACK]"
_KEYS = (
    "workdir|model|provider|approval|sandbox|reasoning effort|"
    "reasoning summaries|session id"
)
_META = re.compile(rf"(?:{_KEYS}):[ \t]*\S.*", re.I)
_BANNER = re.compile(r"OpenAI Codex v[0-9][^\r\n]*")
_STDIN_NOTICE = "Reading additional input from stdin..."
_RULE = re.compile(r"-{3,}")
_TITLE = re.compile(r"(?:持仓晨报|午盘简报|持仓日报|持仓报告|股票日报)")
_DATE = re.compile(r"(?:日期[：:]\s*)?\d{4}(?:-\d{1,2}-\d{1,2}|年\d{1,2}月\d{1,2}日)(?:[ \t]+\d{1,2}:\d{2})?")
_USAGE = re.compile(r"tokens used[ \t]*:[ \t]*[0-9][0-9,]*", re.I)
_COUNT = re.compile(r"[0-9][0-9,]*")
_CLI_ERROR = re.compile(
    r"(?:(?:codex(?: exec)?[ \t]*:[ \t]*)?(?:error|fatal)[ \t]*:|"
    r"Traceback \(most recent call last\):)", re.I
)
_SECTIONS = ("一、持仓盈亏表", "二、技术面一句话", "三、今日动作指令", "四、风险提示")


def _label(line):
    """Normalize labels for recognition only; never use this to emit text."""
    value = line.strip()
    value = re.sub(r"^#{1,6}[ \t]+", "", value)
    if value.startswith("**"):
        close = value.find("**", 2)
        if close >= 2:
            value = (value[2:close] + value[close + 2:]).strip()
    return value


def _kind(line):
    value = _label(line)
    if len(value) <= 100 and _TITLE.search(value):
        # A title may have a date, separators, and a market prefix, but no prose.
        rest = _TITLE.sub("", value)
        rest = _DATE.sub("", rest)
        rest = re.sub(r"[（(]\d{1,2}:\d{2}[）)]", "", rest)
        rest = re.sub(r"A股|股票", "", rest)
        if not rest.strip(" \t|｜:：-—–()（）[]【】"):
            return "title"
    if _DATE.fullmatch(value):
        return "date"
    if value == _SECTIONS[0]:
        return "section"
    return None


def _outside_fences(lines):
    result = []
    fence = None
    for line in lines:
        value = line.lstrip(" \t")
        # Markdown tabs advance to the next four-column stop.
        indent = len(line[:len(line) - len(value)].expandtabs(4))
        match = re.match(r"(\x60{3,}|~{3,})(.*)$", value.rstrip("\r\n")) if indent <= 3 else None
        result.append(fence is None and match is None and indent <= 3)
        if match:
            mark, rest = match.groups()
            if fence is None:
                fence = (mark[0], len(mark))
            elif mark[0] == fence[0] and len(mark) >= fence[1] and not rest.strip():
                fence = None
    return result


def _nonblank(lines, start, end):
    while start < end and not lines[start].strip():
        start += 1
    return start


def _prefix(lines, outside):
    """Return report start and whether a proven CLI envelope was consumed."""
    end = len(lines)
    start = _nonblank(lines, 0, end)
    if start == end:
        return start, False
    cursor = start
    stdin_notice = outside[cursor] and lines[cursor].rstrip("\r\n") == _STDIN_NOTICE
    if stdin_notice:
        cursor = _nonblank(lines, cursor + 1, end)
        if cursor == end:
            raise ValueError("missing_banner")
    banner = outside[cursor] and bool(_BANNER.fullmatch(lines[cursor].strip()))
    if not banner:
        # Report metadata and bare role names cannot establish CLI provenance.
        return start, False
    fields = []
    cursor = _nonblank(lines, cursor + 1, end)
    if cursor == end or not outside[cursor] or not _RULE.fullmatch(lines[cursor].strip()):
        raise ValueError("invalid_banner")
    cursor += 1
    while cursor < end:
        value = lines[cursor].strip()
        if not value:
            cursor += 1
        elif outside[cursor] and _META.fullmatch(value):
            fields.append(value.split(":", 1)[0].lower())
            cursor += 1
        else:
            break
    if not {"model", "session id"}.issubset(fields):
        raise ValueError("incomplete_header")
    if cursor == end or not outside[cursor] or not _RULE.fullmatch(lines[cursor].strip()):
        raise ValueError("unclosed_header")
    start = _nonblank(lines, cursor + 1, end)
    role = lines[start].strip() if start < end and outside[start] else None
    if role in ("user", "codex"):
        roles = [i for i in range(start, end)
                 if outside[i] and lines[i].strip() in ("user", "codex")]
        codex_roles = [i for i in roles if lines[i].strip() == "codex"]
        if stdin_notice or len(codex_roles) > 1:
            # Search only role-bounded segments, never the echoed prompt. A
            # plausible title plus long chatter cannot establish a report.
            candidates = []
            for i, limit in zip(roles, roles[1:] + [end]):
                if lines[i].strip() != "codex":
                    continue
                report = _nonblank(lines, i + 1, limit)
                if report < limit and outside[report] and _kind(lines[report]) == "title":
                    anchor = lines[report].rstrip("\r\n")
                    stop = next((j for j in range(report + 1, limit)
                                 if outside[j] and lines[j].rstrip("\r\n") == anchor), limit)
                    if _valid_report(lines, outside, report, stop, require_template=True):
                        candidates.append((i, report))
            if len(candidates) != 1 or candidates[0][0] != roles[-1]:
                raise ValueError("ambiguous_transcript_roles")
            return candidates[0][1], True
    elif stdin_notice:
        raise ValueError("missing_transcript_role")
    if role == "user":
        candidates = []
        for i, limit in zip(roles, roles[1:] + [end]):
            if lines[i].strip() != "codex":
                continue
            report = _nonblank(lines, i + 1, limit)
            if report < limit and outside[report] and _kind(lines[report]):
                candidates.append(report)
        if len(candidates) != 1:
            raise ValueError("ambiguous_transcript_roles")
        return candidates[0], True
    if role == "codex":
        start = _nonblank(lines, start + 1, end)
    return start, True


def _usage_start(lines, outside, start, end):
    """Locate a possible usage suffix, without authorizing its removal."""
    original_end = end
    while end > start and not lines[end - 1].strip():
        end -= 1
    if end > start and outside[end - 1]:
        # CLI usage is unindented. Do not normalize whitespace to recognize it.
        value = lines[end - 1].rstrip("\r\n")
        if _USAGE.fullmatch(value):
            return end - 1
        if _COUNT.fullmatch(value):
            label = end - 2
            if label >= start and outside[label] and lines[label].rstrip("\r\n").lower() == "tokens used":
                return label
    return original_end


def _stream_boundary(lines, outside, start, end):
    """Require an EOF boundary or let exact-copy comparison prove the end.

    A lone template has no explicit terminator. Recognize its final risk list
    conservatively; unstructured continuations cannot authorize prefix deletion.
    Repeated anchors still need the existing whole-slice equality proof.
    """
    anchor = lines[start].rstrip("\r\n")
    repeats = 0
    risk = None
    for i in range(start, end):
        if not outside[i]:
            continue
        value = lines[i].strip()
        if value in ("user", "codex", "exec") or re.match(r"web search\s*:", value, re.I):
            raise ValueError("uncertain_report_end")
        if lines[i].rstrip("\r\n") == anchor:
            repeats += 1
        if _label(lines[i]).startswith(_SECTIONS[3]) and risk is None:
            risk = i
    if repeats > 1:
        # _clean must compare every complete block, including its entire tail.
        return
    if risk is None:
        raise ValueError("uncertain_report_end")
    items = 0
    for i in range(risk + 1, end):
        if not outside[i] or not lines[i].strip():
            continue
        if not re.fullmatch(r"(?:[-+*][ \t]+|[0-9]+[.)、][ \t]*)\S.*",
                            lines[i].strip()):
            raise ValueError("uncertain_report_end")
        items += 1
    if not items:
        raise ValueError("uncertain_report_end")


def _valid_report(lines, outside, start, end, require_template=False):
    if start >= end or not outside[start]:
        return False
    kind = _kind(lines[start])
    if kind is None:
        return False
    if any(outside[i] and lines[i].strip() in ("user", "codex")
           for i in range(start, end)):
        return False
    labels = [_label(lines[i]) for i in range(start, end) if outside[i]]
    sections = []
    for value in labels:
        for index, section in enumerate(_SECTIONS):
            if value.startswith(section):
                sections.append(index)
                break
    if sections == [0, 1, 2, 3]:
        return True
    if require_template:
        return False
    # A title is a strong anchor for short reports; dates alone are not.
    body_size = sum(not char.isspace() for i in range(start + 1, end)
                    if outside[i] for char in lines[i])
    return kind == "title" and body_size >= 20


def _confirmed_usage_copy(lines, outside, starts, envelope):
    """Return a report slice only when a role and an exact copy frame usage."""
    if not envelope or len(starts) != 2:
        return None
    start, repeat = starts
    role = start - 1
    while role >= 0 and not lines[role].strip():
        role -= 1
    if role < 0 or not outside[role] or lines[role].strip() != "codex":
        return None
    if _kind(lines[start]) != "title":
        return None
    usage = _usage_start(lines, outside, start, repeat)
    # The separator must be exactly one usage line or a label/count pair;
    # blank boundary lines cannot be trimmed to make copies compare equal.
    suffix = [line.rstrip("\r\n") for line in lines[usage:repeat]]
    if not (len(suffix) == 1 and _USAGE.fullmatch(suffix[0]) or
            len(suffix) == 2 and suffix[0].lower() == "tokens used" and
            _COUNT.fullmatch(suffix[1])):
        return None
    first = "".join(lines[start:usage])
    if first != "".join(lines[repeat:]):
        return None
    if not all(_valid_report(lines, outside, begin, end, require_template=True)
               for begin, end in ((start, usage), (repeat, len(lines)))):
        return None
    return first


def _clean(text):
    lines = text.splitlines(keepends=True)
    outside = _outside_fences(lines)
    start, envelope = _prefix(lines, outside)
    if start == len(lines) or not outside[start] or _kind(lines[start]) is None:
        raise ValueError("unrecognized_report_start")
    # Anchor equality is exact, including spaces and Markdown syntax.
    anchor = lines[start].rstrip("\r\n")
    starts = [i for i in range(start, len(lines))
              if outside[i] and lines[i].rstrip("\r\n") == anchor]
    if len(starts) > 64:
        raise ValueError("too_many_anchors")
    bounds = starts[1:] + [len(lines)]
    role = start - 1
    while role >= 0 and not lines[role].strip():
        role -= 1
    if (envelope and role >= 0 and outside[role] and lines[role].strip() == "codex"
            and _valid_report(lines, outside, start, bounds[0], require_template=True)):
        _stream_boundary(lines, outside, start, len(lines))
    chunks = ["".join(lines[begin:end]) for begin, end in zip(starts, bounds)]
    if any(chunk != chunks[0] for chunk in chunks[1:]):
        confirmed = _confirmed_usage_copy(lines, outside, starts, envelope)
        if confirmed is not None:
            return confirmed
        raise ValueError("nonidentical_anchored_blocks")
    if len(starts) == 1 and envelope and _usage_start(lines, outside, start, bounds[0]) != bounds[0]:
        # A header proves nothing about whether terminal telemetry is body text.
        raise ValueError("ambiguous_usage_footer")
    for begin, end in zip(starts, bounds):
        if not _valid_report(lines, outside, begin, end):
            raise ValueError("unrecognized_report_body")
    if len(starts) > 1:
        # No trimming: final EOLs and whitespace-only boundary lines are bytes too.
        return chunks[0] if envelope else "".join(lines[:bounds[0]])
    if not envelope:
        return text
    return "".join(lines[start:])


def clean_report(text: str) -> str:
    """Delete proven CLI prefixes/exact report copies; fail open on doubt.

    Unknown structures and internal errors return the original object and log
    REPORT_CLEAN_FALLBACK. No whitespace, number, or wording is rewritten.
    """
    try:
        return _clean(text)
    except Exception as exc:
        _LOG.warning("%s reason=%s", _MARK, type(exc).__name__ + ":" + str(exc))
        return text


def _recovery_error(text):
    """Recognize leading CLI diagnostics, independently of report length."""
    lines = text.splitlines(keepends=True)
    outside = _outside_fences(lines)
    try:
        start, _ = _prefix(lines, outside)
    except ValueError:
        start = _nonblank(lines, 0, len(lines))
    if start < len(lines) and outside[start] and _CLI_ERROR.match(lines[start].strip()):
        return "".join(lines[start:]).strip()[:300]
    return None


def ssh_run(cmd, timeout=20):
    """短SSH命令"""
    r = subprocess.run(
        ["ssh", "-F", SSH_CONFIG, "-i", SSH_KEY, "dev-box", cmd],
        capture_output=True, text=True, timeout=timeout
    )
    return r

def fetch_market_data():
    """抓取市场数据（必须用项目 venv 的 python，talib 装在 venv 里）"""
    venv_py = "/home/hatch/workspace/stock-system-v2/venv/bin/python"
    r = subprocess.run(
        [venv_py, "/home/hatch/workspace/stock-system-v2/fetch_tdx2.py"],
        capture_output=True, text=True, timeout=180
    )
    return json.loads(r.stdout)

def build_prompt(market_data, report_type):
    """构建分析prompt"""
    with open("/home/hatch/workspace/stock-system-v2/analysis_prompt.md") as f:
        template = f.read()

    titles = {"morning": "持仓晨报", "midday": "午盘简报", "evening": "持仓日报"}

    return f"""{template}

## 本次任务
生成{titles.get(report_type, '持仓报告')}（{datetime.now().strftime('%Y-%m-%d %H:%M')}）

## 持仓数据
{json.dumps(market_data, ensure_ascii=False, indent=2)}

请按模板的四步流程分析，输出中文报告。记住：指标解读+明确动作，两样都要。不要只给术语，不要只给动作不给理由。
"""

def try_codex_analysis(prompt):
    """nohup模式调用Codex"""
    job_id = uuid.uuid4().hex[:8]
    remote_prompt = f"/tmp/prompt_{job_id}.txt"
    remote_result = f"/tmp/result_{job_id}.txt"
    remote_done = f"/tmp/done_{job_id}.txt"

    # 1. 写prompt到本地临时文件
    import tempfile
    local_script = None
    with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as f:
        f.write(prompt)
        local_prompt = f.name

    try:
        # 2. SCP prompt到dev-box（短连接）
        subprocess.run(
            ["scp", "-F", SSH_CONFIG, "-i", SSH_KEY, local_prompt, f"dev-box:{remote_prompt}"],
            check=True, timeout=30, capture_output=True
        )

        # 3. 把 Codex 启动命令写成脚本文件传上去，再 nohup 跑脚本
        # v2.1 修复（2026-10-20 晚）：v2 把命令拼在 ssh 命令行里，\\' 转义导致
        # 远端 shell 报 unexpected EOF（16:30 主通道死因）；脚本文件零嵌套引号，根治
        remote_script = f"/tmp/run_codex_{job_id}.sh"
        with tempfile.NamedTemporaryFile(mode='w', suffix='.sh', delete=False) as f:
            f.write(
                "#!/bin/bash\n"
                "export PATH=/usr/local/node22/bin:$HOME/.npm-global/bin:$PATH\n"
                f"codex exec --model {CODEX_MODEL} "
                f"--config 'model_reasoning_effort=\"{CODEX_REASONING}\"' "
                f"--skip-git-repo-check \"$(cat {remote_prompt})\" "
                f"> {remote_result} 2>&1\n"
                f"echo done > {remote_done}\n"
            )
            local_script = f.name
        subprocess.run(
            ["scp", "-F", SSH_CONFIG, "-i", SSH_KEY, local_script, f"dev-box:{remote_script}"],
            check=True, timeout=30, capture_output=True
        )

        # 4. nohup后台启动Codex（短连接，立即返回；命令行里零引号）
        # Codex跑完后写 done 标记文件
        r = ssh_run(f"nohup bash {remote_script} > /dev/null 2>&1 & echo started")
        if "started" not in r.stdout:
            return {"success": False, "error": f"nohup启动失败: {r.stderr[:200]}"}

        # 5. 轮询等待完成
        start = time.time()
        while time.time() - start < CODEX_TIMEOUT:
            time.sleep(POLL_INTERVAL)
            r = ssh_run(f"test -f {remote_done} && echo yes || echo no")
            if r.stdout.strip() == "yes":
                break
        else:
            return {"success": False, "error": f"Codex超时（{CODEX_TIMEOUT}秒）"}

        # 6. 取结果文件（短连接）
        local_result = f"/tmp/codex_result_{job_id}.txt"
        subprocess.run(
            ["scp", "-F", SSH_CONFIG, "-i", SSH_KEY, f"dev-box:{remote_result}", local_result],
            check=True, timeout=30, capture_output=True
        )

        with open(local_result, encoding="utf-8", newline="") as f:
            raw_content = f.read()
        # 清洗失败时保留原文；警告写 stderr，不混入报告 JSON。
        content = clean_report(raw_content)

        # 清理
        os.unlink(local_result)
        ssh_run(f"rm -f {remote_prompt} {remote_result} {remote_done} {remote_script}")

        error = _recovery_error(content)
        if error:
            return {"success": False, "error": error}
        if content.strip():
            return {"success": True, "result": content}
        else:
            return {"success": False, "error": "Codex输出为空"}

    except subprocess.TimeoutExpired:
        return {"success": False, "error": "SSH/SCP超时"}
    except Exception as e:
        return {"success": False, "error": str(e)[:300]}
    finally:
        if os.path.exists(local_prompt):
            os.unlink(local_prompt)
        if local_script and os.path.exists(local_script):
            os.unlink(local_script)

def main():
    report_type = sys.argv[1] if len(sys.argv) > 1 else "morning"
    print(f"[{datetime.now()}] 双通道v2 (nohup模式) 开始{report_type}分析", file=sys.stderr)

    # 1. 抓取数据
    print("抓取市场数据...", file=sys.stderr)
    try:
        market_data = fetch_market_data()
    except Exception as e:
        print(json.dumps({"error": f"数据抓取失败: {e}"}))
        sys.exit(1)

    # 2. 构建prompt
    prompt = build_prompt(market_data, report_type)

    # 3. 尝试主通道（Codex nohup）
    print(f"主通道：Codex + {CODEX_MODEL} (nohup后台)...", file=sys.stderr)
    result = try_codex_analysis(prompt)

    if result.get("success"):
        print("主通道成功", file=sys.stderr)
        output = {
            "timestamp": datetime.now().isoformat(),
            "report_type": report_type,
            "channel": "codex",
            "model": CODEX_MODEL,
            "report": result["result"]
        }
    else:
        print(f"主通道失败（{result.get('error', 'unknown')}），切备通道", file=sys.stderr)
        output = {
            "timestamp": datetime.now().isoformat(),
            "report_type": report_type,
            "channel": "aqi_fallback",
            "model": "muse-spark",
            "report": None,
            "market_data": market_data,
            "fallback_reason": result.get("error", "unknown")
        }

    print(json.dumps(output, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
