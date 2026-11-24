from collections import defaultdict
from math import ceil, sqrt
from statistics import NormalDist

from src.domain.ledger import build_ledger


COLUMNS = (
    ("district", "街区"), ("population", "桩位总数"), ("sample", "抽检数"), ("missing", "缺牌数"),
    ("rate", "估计缺牌率（%）"), ("estimate", "估计缺牌量"), ("lower", "区间下界"),
    ("upper", "区间上界"), ("purchase", "上界采购数量"), ("status", "备料状态"),
)


def validate(row):
    if not 0 <= row["missing"] <= row["sample"] <= row["population"] or row["sample"] < 1:
        raise ValueError("请核对 缺牌数 ≤ 抽检数 ≤ 桩位总数")


def estimate_total(population, sample, missing, z):
    rate = missing/sample
    estimate = population * rate
    variance = (population**2 * (1 - sample / population) * rate * (1-rate) / (sample-1)
                if 1 < sample < population else 0.0)
    width = z * sqrt(variance)
    return estimate, variance, max(float(missing), estimate-width), min(missing+population-sample, estimate+width)


def analyze(rows, parameters, *, ledger=None):
    """按发起分析时固化的认账台账计算。

    ``ledger`` 为 :func:`src.domain.ledger.build_ledger` 的产物；只传 ``rows`` 时
    会现场归并，保持纯函数便于直接调用。同版两套数的街区暂不出采购量，
    其余街区照常备料，总计和说明里标清缺了哪些街区。
    """

    if ledger is None:
        ledger = build_ledger(rows)
    confidence = parameters.get("confidence", 95)
    z = NormalDist().inv_cdf(0.5 + confidence/200)
    excluded = set(ledger["excluded_districts"])
    groups = defaultdict(list)
    seen = set()
    for row in ledger["effective"]:
        validate(row)
        key = row["district"], row["batch"]
        if key in seen:
            raise ValueError(f"重复抽查批次 {row['batch']}")
        seen.add(key)
        groups[row["district"]].append(row)
    # 整个街区都被隔离时没有生效行，仍要在明细里占一行并标明暂缓。
    for row in ledger.get("quarantined", []):
        groups[row["district"]]
    if not groups:
        raise ValueError("请先载入街区抽查批次")
    table, chart, warnings = [], [], []
    all_n = all_m = all_found = 0
    all_est = all_var = 0.0
    for district, batches in sorted(groups.items()):
        if district in excluded:
            population = None
            if batches:
                sizes = {row["population"] for row in batches}
                population = sizes.pop() if len(sizes) == 1 else None
            else:
                candidate = [row["population"] for row in ledger.get("quarantined", [])
                             if row["district"] == district]
                if candidate and len(set(candidate)) == 1:
                    population = candidate[0]
            table.append(dict(district=district, population=population, sample=None, missing=None,
                              rate=None, estimate=None, lower=None, upper=None, purchase=None,
                              status="同版数据冲突，暂不出采购量"))
            continue
        sizes = {row["population"] for row in batches}
        if len(sizes) != 1:
            raise ValueError(f"{district} 的桩位台账版本冲突")
        population = sizes.pop()
        sample = sum(row["sample"] for row in batches)
        missing = sum(row["missing"] for row in batches)
        if sample > population or (sample < population and sample < 2):
            raise ValueError(f"{district} 合并抽检数无效或不足两桩，请确认互斥抽样批次")
        estimate, variance, lower, upper = estimate_total(population, sample, missing, z)
        if sample < population and (missing < 5 or sample-missing < 5):
            warnings.append(district)
        all_n += population
        all_m += sample
        all_found += missing
        all_est += estimate
        all_var += variance
        chart.append((district, round(estimate, 2)))
        table.append(dict(district=district, population=population, sample=sample, missing=missing,
                          rate=round(100*missing/sample, 2), estimate=round(estimate, 2),
                          lower=round(lower, 2), upper=round(upper, 2), purchase=ceil(upper),
                          status="正常备料"))
    width = z * sqrt(all_var)
    lower = max(float(all_found), all_est - width)
    upper = min(all_found + all_n - all_m, all_est + width)
    table.append(dict(district="街区加权总计", population=all_n, sample=all_m, missing=all_found,
                      rate=round(100*all_est/all_n, 2) if all_n else None,
                      estimate=round(all_est, 2),
                      lower=round(lower, 2) if all_n else None,
                      upper=round(upper, 2) if all_n else None,
                      purchase=ceil(upper) if all_n else None,
                      status="受影响街区未计入" if excluded else "覆盖全部街区"))
    notes = []
    if warnings:
        notes.append("近似区间需补样：" + "、".join(warnings))
    if excluded:
        notes.append("以下街区同版出现两套数据，本版暂不出采购量：" + "、".join(sorted(excluded)))
    note = "。".join(notes)
    if all_n:
        budget = (f"各街区按实际桩位数加权，合计 {confidence:g}% 近似区间 {lower:.1f}–{upper:.1f} 枚。"
                  f"总体采购预算使用总方差，街区上界用于各班备料")
    else:
        budget = "全部街区均暂缓备料，本版不出采购量；待更正消除同版冲突后再重算"
    summary = budget + (f"。{note}。" if note else "。")
    headline = (f"估计需要补装 {all_est:.0f} 枚标牌" if not excluded
                else f"受影响街区暂缓备料，其余街区估计需要补装 {all_est:.0f} 枚标牌")
    return report(headline, summary,
                  [("桩位总体", all_n, "桩"), ("抽查覆盖", all_m, "桩"),
                   ("暂缓街区", len(excluded), "个"), ("合计上界备料", ceil(upper), "枚")],
                  COLUMNS, table, "各街区估计缺牌量", chart, "枚",
                  excluded_districts=sorted(excluded), conflicts=ledger["conflicts"])


def seed():
    plans = [
        ("木厂西街", 420, "MC", [(24, 5), (22, 3), (28, 5), (20, 4)]),
        ("砂桥支路", 260, "SJ", [(32, 2), (27, 1), (32, 3), (28, 6)]),
        ("石堤转角", 780, "SD", [(34, 10), (36, 8), (32, 8), (27, 7)]),
        ("榆畔社区", 460, "YP", [(34, 2), (46, 7), (34, 7), (31, 4)]),
    ]
    return [dict(district=district, batch=f"{prefix}-{index+1:02d}", population=population,
                 sample=sample, missing=missing)
            for district, population, prefix, items in plans for index, (sample, missing) in enumerate(items)]


def report(headline, summary, metrics, columns, rows, chart_label, chart_values, unit="",
           excluded_districts=None, conflicts=None):
    return {
        "headline": headline, "summary": summary,
        "metrics": [{"label": label, "value": value, "unit": suffix} for label, value, suffix in metrics],
        "columns": [{"key": key, "label": label} for key, label in columns],
        "rows": [{key: row.get(key) for key, _ in columns} for row in rows],
        "chart": {"label": chart_label, "unit": unit,
                  "points": [{"label": label, "value": value} for label, value in chart_values]},
        "excluded_districts": excluded_districts or [],
        "conflicts": conflicts or [],
    }
