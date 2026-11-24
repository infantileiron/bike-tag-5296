"""对已建库做幂等小迁移。

项目用 ``Base.metadata.create_all`` 首次建表；补送更正需要在历史库上补
认账键列、版次列和分析快照列。这里用反射检查列是否存在，缺什么补什么，
PostgreSQL 与本地测试用的 SQLite 都能跑，可重复执行。
"""

import json

from sqlalchemy import inspect, text

from src.domain.ledger import DEFAULT_REVISION

_COLUMNS = {
    'observations': {
        'district': "VARCHAR(300) NOT NULL DEFAULT ''",
        'batch': "VARCHAR(300) NOT NULL DEFAULT ''",
        'revision': f'INTEGER NOT NULL DEFAULT {DEFAULT_REVISION}',
    },
    'analysis_runs': {
        'ledger_snapshot': 'JSON',
    },
}


def ensure_schema(engine):
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    with engine.begin() as connection:
        for table, columns in _COLUMNS.items():
            if table not in existing_tables:
                continue
            present = {column['name'] for column in inspector.get_columns(table)}
            for name, ddl in columns.items():
                if name not in present:
                    connection.execute(text(f'ALTER TABLE {table} ADD COLUMN {name} {ddl}'))
        if 'observations' in existing_tables:
            connection.execute(text(
                'CREATE INDEX IF NOT EXISTS ix_observations_district_batch '
                'ON observations (district, batch)'))
            # 历史观测没有版次字段，按第 1 版认账；街区/批次从 payload 回填。
            rows = connection.execute(
                text('SELECT id, payload FROM observations WHERE district = :blank OR batch = :blank'),
                {'blank': ''}).all()
            for observation_id, payload in rows:
                data = payload if isinstance(payload, dict) else json.loads(payload)
                connection.execute(
                    text('UPDATE observations SET district = :district, batch = :batch, '
                         f'revision = :revision WHERE id = :id'),
                    {'district': data.get('district', ''), 'batch': data.get('batch', ''),
                     'revision': DEFAULT_REVISION, 'id': observation_id})
