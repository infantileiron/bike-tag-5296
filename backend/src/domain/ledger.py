"""认账台账：按（街区、批次）归并各版观测。

规则（对应巡查队补送更正流程）：

* 认账键是「街区 + 抽样批次」。抽检数、缺牌数等都挂在该键的某一版上，
  样本调到别处时，新版记录写在原街区批次下，数字随新版整体带走。
* 版次 ``revision`` 越大越新；乱序到达的旧版只留存审计，不顶掉新版。
* 同一认账键的同一版出现两套不同数据时，整个街区列入 ``excluded_districts``，
  本版暂不出采购量，等更高版次更正后自动恢复。

返回结构全部可 JSON 序列化，便于整体固化进分析快照。
"""

import json
from collections import defaultdict

DEFAULT_REVISION = 1


def normalize_revision(row):
    revision = row.get('revision')
    if isinstance(revision, bool):
        return DEFAULT_REVISION
    if isinstance(revision, int) and revision >= 1:
        return revision
    return DEFAULT_REVISION


def ledger_key(row):
    return row['district'], row['batch']


def row_fingerprint(row):
    return json.dumps(row, sort_keys=True, ensure_ascii=False)


def build_ledger(rows):
    """把原始观测归并成当前认定的那一版账。

    返回：

    * ``effective``：每个认账键当前生效的一条记录（含标准化后的 ``revision``）。
    * ``quarantined``：同版两套数的原始记录，留作报表标注，不参与估计。
    * ``conflicts``：同版两套数的认账键清单，含街区、批次、版次和数据套数。
    * ``excluded_districts``：受冲突影响、暂不出采购量的街区（去重排序）。
    * ``revisions``：每个认账键目前见到的最高版次（字符串键，可 JSON 化）。
    * ``superseded_count``：被新版顶掉、仅留存审计的旧版记录数。
    * ``corrected_keys``：发生过补送更正（最高版次 > 1）的认账键。
    """

    grouped = defaultdict(list)
    for row in rows:
        grouped[ledger_key(row)].append(row)

    effective = []
    quarantined = []
    conflicts = []
    revisions = {}
    corrected_keys = []
    superseded_count = 0

    for district, batch in sorted(grouped):
        entries = grouped[(district, batch)]
        latest = max(normalize_revision(row) for row in entries)
        revisions[f'{district}{batch}'] = latest
        if latest > 1:
            corrected_keys.append({'district': district, 'batch': batch, 'revision': latest})
        current = [row for row in entries if normalize_revision(row) == latest]
        superseded_count += len(entries) - len(current)
        distinct = {row_fingerprint(row): row for row in current}
        if len(distinct) > 1:
            conflicts.append({
                'district': district,
                'batch': batch,
                'revision': latest,
                'versions': len(distinct),
            })
            # 同版两套数都留存，供报表标注；不进入生效账，不参与估计。
            quarantined.extend(dict(row, revision=latest) for row in distinct.values())
        else:
            chosen = dict(current[0])
            chosen['revision'] = latest
            effective.append(chosen)

    return {
        'effective': effective,
        'quarantined': quarantined,
        'conflicts': conflicts,
        'excluded_districts': sorted({item['district'] for item in conflicts}),
        'revisions': revisions,
        'superseded_count': superseded_count,
        'corrected_keys': corrected_keys,
    }
