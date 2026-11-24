import csv
import hashlib
import io
import json

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from src.config.project import SPEC
from src.domain import analysis
from src.domain.ledger import DEFAULT_REVISION, ledger_key, normalize_revision
from src.models.workflow import AuditEvent, Observation
from src.services.errors import BusinessError
from src.services.validation import validated


MAX_OBSERVATIONS = 5000


def clean_observation(raw):
    row = validated(SPEC['fields'], raw)
    row['revision'] = normalize_revision(row)
    try:
        if hasattr(analysis, 'validate'):
            analysis.validate(row)
    except ValueError as exc:
        raise BusinessError(str(exc)) from exc
    return row


def content_key(row):
    return 'data:' + hashlib.sha256(json.dumps(row, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def parse_csv(raw):
    if len(raw) > 512 * 1024:
        raise BusinessError('CSV 文件上限 512 KB', 413)
    try:
        reader = csv.DictReader(io.StringIO(raw.decode('utf-8-sig')), strict=True)
        header = reader.fieldnames or []
        known = {item['key'] for item in SPEC['fields']}
        required = {item['key'] for item in SPEC['fields'] if item.get('required', True)}
        if len(set(header)) != len(header) or not required.issubset(header) or not set(header).issubset(known):
            raise BusinessError('CSV 表头与导入模板不匹配')
        rows = []
        for row in reader:
            try:
                if None in row or any(value is None for value in row.values()):
                    raise BusinessError('列数与表头不匹配')
                rows.append(clean_observation(row))
            except BusinessError as exc:
                raise BusinessError(f'CSV 第 {reader.line_num} 行，{exc}') from exc
            if len(rows) > 500:
                raise BusinessError('单次导入上限 500 条')
        if not rows:
            raise BusinessError('CSV 里没有观测记录')
        return rows
    except (UnicodeDecodeError, csv.Error) as exc:
        raise BusinessError('请上传 UTF-8 编码的有效 CSV') from exc


def add_observations(db, rows, actor, action):
    """按认账键（街区+批次）和版次入账。

    返回 ``(items, stats)``：

    * 内容完全一致的记录视为重试，直接跳过，不新增批次；
    * 认账键已有更高版次时，迟到的旧版只留存审计，不顶掉新版；
    * 更高版次作为补送更正入账，抽检数/缺牌数随新版整体认到原街区批次；
    * 同版两套不同数据都留存，由认账台账把该街区标为暂缓备料。
    """

    deduped = {}
    for row in rows:
        deduped[content_key(row)] = row
    existing = set(db.scalars(select(Observation.source_key).where(Observation.source_key.in_(deduped))))
    stats = {'added': 0, 'corrected': 0, 'stale': 0, 'skipped': 0}
    items = []
    for key, row in deduped.items():
        if key in existing:
            stats['skipped'] += 1
            continue
        district, batch = ledger_key(row)
        history = list(db.scalars(select(Observation).where(
            Observation.district == district, Observation.batch == batch)))
        highest = max((normalize_revision(item.payload) for item in history), default=0)
        if normalize_revision(row) < highest:
            stats['stale'] += 1
        elif any(normalize_revision(item.payload) == normalize_revision(row) for item in history):
            stats['corrected'] += 1
        elif highest:
            stats['corrected'] += 1
        else:
            stats['added'] += 1
        items.append(Observation(source_key=key, district=district, batch=batch,
                                 revision=normalize_revision(row), payload=row))
    if db.scalar(select(func.count(Observation.id))) + len(items) > MAX_OBSERVATIONS:
        raise BusinessError('当前项目最多保留 5000 条观测记录', 409)
    db.add_all(items)
    if items:
        detail = (f"新增 {stats['added']} 条，补送更正 {stats['corrected']} 条，"
                  f"旧版留存 {stats['stale']} 条，重试跳过 {stats['skipped']} 条")
        db.add(AuditEvent(actor=actor, action=action, detail=detail))
    try:
        db.flush()
    except IntegrityError as exc:
        raise BusinessError('有相同记录刚刚写入，请刷新后重试', 409) from exc
    return items, stats
