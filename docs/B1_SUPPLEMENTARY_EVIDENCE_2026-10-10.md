# B1 补充证据：原始探针输出 + SKILL 字段定义（2026-10-10）

## 一、原始探针输出文件
- 文件：`workspace/user/files/b1_probe_output_20261010_171358.json`
- 采集时间：2026-10-10 17:13:58（Windows 实机，只读）
- 探针股票：601518.SH（吉林高速）
- 关键实测值：
  - `Unit`: "100"（get_stock_info）
  - `MinPrice`: "0.01"
  - `XsFlag`: "2"
  - `Name`: "吉林高速"
  - `ErrorId`: "0"（两接口均成功）

## 二、SKILL 字段定义（tdx-tq-local-SKILL.md）

### get_market_snapshot 返回字段（第 560-595 行）
| 字段 | 类型 | 说明 |
|------|------|------|
| Volume | str | 总手 |
| NowVol | str | 现手 |
| Amount | str | 总成交金额 |
| Buyp | List[str] | 五个买价 |
| Buyv | List[str] | 对应的五个买盘量 |
| Sellp | List[str] | 五个卖价 |
| Sellv | List[str] | 对应的五个卖盘量 |

### get_stock_info 返回字段（第 597 行起）
| 字段 | 类型 | 说明 |
|------|------|------|
| Unit | str | 交易单位 |
| MinPrice | str | 最小价格变动 |
| XsFlag | str | 价格小数位数 |

## 三、单位认定分析（供总指挥核定）

### Volume → 手
- SKILL 明确标注 Volume 为"总手"（第 574 行）
- 实机 Unit="100" 确认 1 手=100 股
- 认定：[核]（SKILL 文档 + 实机互证）

### Buyv/Sellv → 手
- SKILL 标注为"对应的五个买盘量"/"对应的五个卖盘量"
- 未明确写"手"字样，但与 Volume 同属成交量口径
- 认定：[估]（需总指挥确认是否可与 Volume 同级）

### 价格类 → 元
- SKILL 未标注币种
- MinPrice="0.01"、XsFlag="2" 佐证为元（2 位小数）
- 认定：[估]

### Amount → 万元
- SKILL 仅标注"总成交金额"，未标注单位
- 实算：121746手×100股×2.79元=3396.71万元 ≈ 上报 3397.11
- 认定：[估]
