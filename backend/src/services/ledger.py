"""数据库侧认账台账：把全部观测归并成当前认定的那一版。"""

from sqlalchemy import select

from src.domain.ledger import build_ledger, normalize_revision
from src.models.workflow import Observation


def current_ledger(db):
    rows = list(db.scalars(select(Observation.payload).order_by(Observation.id)))
    return build_ledger(rows)


def known_ledger_keys(db, keys):
    """返回库中已经出现过的认账键集合（用于种子幂等）。"""
    if not keys:
        return set()
    districts = {key[0] for key in keys}
    batches = {key[1] for key in keys}
    rows = db.execute(select(Observation.district, Observation.batch).where(
        Observation.district.in_(districts), Observation.batch.in_(batches))).all()
    return set(rows)


def ledger_view(db):
    ledger = current_ledger(db)
    by_district = {}
    for row in ledger['effective']:
        by_district.setdefault(row['district'], []).append(row)
    for row in ledger['quarantined']:
        by_district.setdefault(row['district'], []).append(row)
    districts = []
    for district in sorted(by_district):
        rows = sorted(by_district[district], key=lambda item: item['batch'])
        districts.append({
            'district': district,
            'excluded': district in ledger['excluded_districts'],
            'batches': [{
                'batch': row['batch'], 'revision': normalize_revision(row),
                'population': row['population'], 'sample': row['sample'],
                'missing': row['missing'],
            } for row in rows],
        })
    return {
        'districts': districts,
        'conflicts': ledger['conflicts'],
        'excluded_districts': ledger['excluded_districts'],
        'superseded_count': ledger['superseded_count'],
        'corrected_batches': [{'district': key['district'], 'batch': key['batch'],
                               'revision': key['revision']}
                              for key in ledger['corrected_keys']],
    }
