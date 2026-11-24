import json
from sqlalchemy import func, select

from src.domain.analysis import seed
from src.migrations.online import ensure_schema
from src.models.base import Base, Session, engine
from src.models.workflow import AnalysisRun, Observation, Review, Schedule
from src.services.ledger import known_ledger_keys
from src.services.observations import clean_observation, content_key


def seed_data():
    Base.metadata.create_all(engine)
    ensure_schema(engine)
    rows = [clean_observation(row) for row in seed()]
    with Session.begin() as db:
        existing_keys = set(db.scalars(select(Observation.source_key)))
        existing_ledger_keys = known_ledger_keys(db, {(row['district'], row['batch']) for row in rows})
        for row in rows:
            key = content_key(row)
            # 认账键已存在时只补缺，不重复写入同版记录，避免种子把正常街区变成冲突街区。
            if key in existing_keys or (row['district'], row['batch']) in existing_ledger_keys:
                continue
            db.add(Observation(source_key=key, district=row['district'], batch=row['batch'],
                               revision=row['revision'], payload=row))
        db.flush()
        return {model.__tablename__: db.scalar(select(func.count(model.id)))
                for model in (Observation, AnalysisRun, Review, Schedule)}


if __name__ == '__main__':
    print(json.dumps(seed_data(), sort_keys=True))
