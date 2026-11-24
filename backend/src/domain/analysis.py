from collections import defaultdict
from math import ceil, sqrt
from statistics import NormalDist


COLUMNS = (
    ("district", "街区"), ("population", "桩位总数"), ("sample", "抽检数"), ("missing", "缺牌数"),
    ("rate", "估计缺牌率（%）"), ("estimate", "估计缺牌量"), ("lower", "区间下界"),
    ("upper", "区间上界"), ("purchase", "上界采购数量"),
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


def analyze(rows, parameters):
    confidence = parameters.get("confidence",95)
    z = NormalDist().inv_cdf(0.5 + confidence/200)
    groups = defaultdict(list)
    seen = set()
    for row in rows:
        validate(row)
        key = row["district"], row["batch"]
        if key in seen:
            raise ValueError(f"重复抽查批次 {row['batch']}")
        seen.add(key)
        groups[row["district"]].append(row)
    if not groups:
        raise ValueError("请先载入街区抽查批次")
    table, chart, warnings = [], [], []
    all_n = all_m = all_found = 0
    all_est = all_var = 0.0
    for district, batches in sorted(groups.items()):
        sizes = {row["population"] for row in batches}
        if len(sizes) != 1:
            raise ValueError(f"{district} 的桩位台账版本冲突")
        population = sizes.pop()
        sample = sum(row["sample"] for row in batches)
        missing = sum(row["missing"] for row in batches)
        if sample > population or (sample < population and sample < 2):
            raise ValueError(f"{district} 合并抽检数无效或不足两桩，请确认互斥抽样批次")
        estimate, variance, lower, upper = estimate_total(population,sample,missing,z)
        if sample < population and (missing < 5 or sample-missing < 5):
            warnings.append(district)
        all_n += population
        all_m += sample
        all_found += missing
        all_est += estimate
        all_var += variance
        chart.append((district,round(estimate,2)))
        table.append(dict(district=district,population=population,sample=sample,missing=missing,
                          rate=round(100*missing/sample,2),estimate=round(estimate,2),
                          lower=round(lower,2),upper=round(upper,2),purchase=ceil(upper)))
    width = z * sqrt(all_var)
    lower = max(float(all_found), all_est - width)
    upper = min(all_found + all_n - all_m, all_est + width)
    table.append(dict(district="街区加权总计",population=all_n,sample=all_m,missing=all_found,
                      rate=round(100*all_est/all_n,2),estimate=round(all_est,2),
                      lower=round(lower,2),upper=round(upper,2),purchase=ceil(upper)))
    note = "。近似区间需补样：" + "、".join(warnings) if warnings else ""
    return report(f"估计需要补装 {all_est:.0f} 枚标牌",
                  f"各街区按实际桩位数加权，合计 {confidence:g}% 近似区间 {lower:.1f}–{upper:.1f} 枚。总体采购预算使用总方差，街区上界用于各班备料{note}。",
                  [("桩位总体",all_n,"桩"),("抽查覆盖",all_m,"桩"),("合计上界备料",ceil(upper),"枚")],
                  COLUMNS,table,"各街区估计缺牌量",chart,"枚")


def seed():
    plans = [
        ("木厂西街", 420, "MC", [(24,5),(22,3),(28,5),(20,4)]),
        ("砂桥支路", 260, "SJ", [(32,2),(27,1),(32,3),(28,6)]),
        ("石堤转角", 780, "SD", [(34,10),(36,8),(32,8),(27,7)]),
        ("榆畔社区", 460, "YP", [(34,2),(46,7),(34,7),(31,4)]),
    ]
    return [dict(district=district,batch=f"{prefix}-{index+1:02d}",population=population,sample=sample,missing=missing)
            for district,population,prefix,items in plans for index,(sample,missing) in enumerate(items)]


def report(headline, summary, metrics, columns, rows, chart_label, chart_values, unit=""):
    return {
        "headline": headline, "summary": summary,
        "metrics": [{"label": label, "value": value, "unit": suffix} for label, value, suffix in metrics],
        "columns": [{"key": key, "label": label} for key, label in columns],
        "rows": [{key: row.get(key) for key, _ in columns} for row in rows],
        "chart": {"label": chart_label, "unit": unit,
                  "points": [{"label": label, "value": value} for label, value in chart_values]},
    }
