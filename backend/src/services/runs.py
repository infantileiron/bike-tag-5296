import json
import logging

from sqlalchemy import select

from src.config.project import SPEC
from src.constants.enums import Decision, Role, RunStatus
from src.domain.analysis import analyze
from src.domain.ledger import build_ledger
from src.models.base import utc_now
from src.models.workflow import AnalysisRun, AuditEvent, Observation, Review
from src.services.errors import BusinessError
from src.services.ledger import current_ledger
from src.services.validation import validated

logger = logging.getLogger(__name__)


def create_run(db, request, actor):
    parameters = validated(SPEC['parameters'], request.parameters, defaults=True)
    sample_ids = list(db.scalars(select(Observation.id).order_by(Observation.id)))
    if not sample_ids:
        raise BusinessError('请先导入观测记录')
    # 发起时认定的这一版账整体固化；后续补送更正不会改动队列里的历史分析。
    ledger = current_ledger(db)
    item = AnalysisRun(title=request.title, owner=actor, parameters=parameters,
                       observation_ids=sample_ids, last_observation_id=sample_ids[-1],
                       sample_size=len(sample_ids), ledger_snapshot=ledger)
    db.add(item)
    db.flush()
    db.add(AuditEvent(actor=actor, action='run.created',
                      detail=f'分析 #{item.id}，固化 {item.sample_size} 条观测，'
                             f'暂缓街区 {len(ledger["excluded_districts"])} 个'))
    return item


def visible_runs(db, user, run_id=None):
    query = select(AnalysisRun, Review).outerjoin(Review, Review.run_id == AnalysisRun.id)
    if user['role'] == Role.VIEWER:
        query = query.where(Review.decision == Decision.APPROVED)
    if run_id is not None:
        pair = db.execute(query.where(AnalysisRun.id == run_id)).first()
        if pair is None:
            raise BusinessError('没有可查看的分析结果', 404)
        return pair
    return db.execute(query.order_by(AnalysisRun.id.desc()).limit(100)).all()


def submit_review(db, run_id, request, actor):
    row = db.get(AnalysisRun, run_id, with_for_update=True)
    if row is None:
        raise BusinessError('分析不存在', 404)
    if row.status != RunStatus.SUCCEEDED:
        raise BusinessError('分析算完后才能复核', 409)
    if db.scalar(select(Review).where(Review.run_id == run_id)):
        raise BusinessError('这份分析已完成复核。请发起新分析并重新复核。', 409)
    if row.owner == actor:
        raise BusinessError('分析须由另一位负责人复核', 409)
    review = Review(run_id=run_id, decision=request.decision, note=request.note, reviewer=actor)
    db.add(review)
    db.add(AuditEvent(actor=actor, action=f'review.{request.decision}', detail=f'分析 #{run_id}'))
    db.flush()
    return review


def _ledger_for_run(db, item):
    """优先用发起时的快照；历史分析没有快照时按当时的观测清单重建。"""
    if item.ledger_snapshot:
        return item.ledger_snapshot
    sample = list(db.scalars(select(Observation.payload).where(Observation.id.in_(item.observation_ids))
                             .order_by(Observation.id)))
    return build_ledger(sample)


def run_pending(db):
    item = db.scalar(select(AnalysisRun).where(AnalysisRun.status == RunStatus.QUEUED)
                     .order_by(AnalysisRun.id).limit(1).with_for_update(skip_locked=True))
    if item is None:
        return False
    try:
        ledger = _ledger_for_run(db, item)
        result = analyze(ledger['effective'], item.parameters, ledger=ledger)
        if not {'headline', 'summary', 'metrics', 'columns', 'rows'} <= set(result):
            raise RuntimeError('invalid result schema')
        json.dumps(result, allow_nan=False, ensure_ascii=False)
        item.result = result
        item.status = RunStatus.SUCCEEDED
    except ValueError as exc:
        item.error = str(exc)[:600] or '本次观测数据不足以完成计算'
        item.status = RunStatus.FAILED
    except Exception:
        logger.exception('Analysis %s raised unexpectedly', item.id)
        item.error = '分析计算失败，请检查记录和参数后重试'
        item.status = RunStatus.FAILED
    item.finished_at = utc_now()
    return True
