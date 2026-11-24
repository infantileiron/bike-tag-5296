"""入账、认账台账、分析快照和在线迁移的服务层集成测试。

用标准库 sqlite3 驱动的 SQLAlchemy 替身（tests/_sa_shim.py），在真实数据库
语义上验证任务规则；在装有真 SQLAlchemy 的环境可直接替换导入。

运行：python3 tests/test_ledger_services.py
"""

import sys
import unittest
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import _sa_shim as sqlalchemy  # noqa: E402

# 在导入应用模块前替换 sqlalchemy
sys.modules['sqlalchemy'] = sqlalchemy
orm = type(sys)('sqlalchemy.orm')
orm.Mapped = sqlalchemy.Mapped
orm.mapped_column = sqlalchemy.mapped_column
orm.DeclarativeBase = sqlalchemy.DeclarativeBase
orm.sessionmaker = sqlalchemy.sessionmaker
sys.modules['sqlalchemy.orm'] = orm
exc = type(sys)('sqlalchemy.exc')


class _IntegrityError(Exception):
    pass


exc.IntegrityError = _IntegrityError
sys.modules['sqlalchemy.exc'] = exc

from src.models import base as base_module  # noqa: E402


def reset_engine():
    fresh = sqlalchemy.create_engine('sqlite:///:memory:')
    base_module.engine = fresh
    base_module.Session = sqlalchemy.sessionmaker(fresh)
    return fresh


reset_engine()

from src.migrations.online import ensure_schema  # noqa: E402
from src.models.base import Base  # noqa: E402
import src.models.base as base  # noqa: E402
from src.models.workflow import AnalysisRun, Observation  # noqa: E402
from src.services.ledger import current_ledger, ledger_view  # noqa: E402
from src.services.observations import (  # noqa: E402
    add_observations,
    clean_observation,
    parse_csv,
)
from src.services.runs import create_run, run_pending  # noqa: E402


def row(district, batch, sample, missing, *, population=100, revision=None):
    data = dict(district=district, batch=batch, population=population,
                sample=sample, missing=missing)
    if revision is not None:
        data['revision'] = revision
    return clean_observation(data)


class ServiceLedgerTests(unittest.TestCase):
    def setUp(self):
        # 每个测试用全新内存库，避免共享引擎造成串扰
        self.engine = reset_engine()
        Base.metadata.create_all(self.engine)
        ensure_schema(self.engine)
        self.session_factory = base.Session

    def _add(self, rows, actor='analyst@demo.local'):
        with self.session_factory.begin() as db:
            return add_observations(db, rows, actor, 'observation.created')

    def test_retry_is_idempotent_and_creates_no_new_batch(self):
        first = row('甲街区', 'B1', 18, 3)
        items_one, _stats_one = self._add([first])
        self.assertEqual(len(items_one), 1)
        items_two, stats_two = self._add([dict(first)])
        self.assertEqual(items_two, [])
        self.assertEqual(stats_two['skipped'], 1)
        with self.session_factory() as db:
            count = db.scalar(sqlalchemy.select(sqlalchemy.func.count(Observation.id)))
        self.assertEqual(count, 1)

    def test_stale_revision_kept_for_audit_but_never_overwrites_newer(self):
        self._add([row('甲街区', 'B1', 20, 4, revision=2)])
        _items, stats = self._add([row('甲街区', 'B1', 18, 3, revision=1)])
        self.assertEqual(stats['stale'], 1)
        with self.session_factory() as db:
            ledger = current_ledger(db)
            count = db.scalar(sqlalchemy.select(sqlalchemy.func.count(Observation.id)))
        self.assertEqual(ledger['effective'][0]['sample'], 20)
        self.assertEqual(ledger['superseded_count'], 1)
        # 旧版记录仍在库中，保留审计痕迹
        self.assertEqual(count, 2)

    def test_correction_moves_sample_and_missing_under_original_key(self):
        self._add([row('甲街区', 'B1', 18, 3, revision=1)])
        self._add([row('甲街区', 'B1', 12, 1, revision=2)])
        with self.session_factory() as db:
            ledger = current_ledger(db)
        effective = ledger['effective'][0]
        self.assertEqual((effective['sample'], effective['missing']), (12, 1))
        self.assertEqual(effective['district'], '甲街区')
        self.assertEqual(effective['batch'], 'B1')

    def test_same_revision_conflict_quarantines_district_and_view_lists_it(self):
        self._add([
            row('甲街区', 'B1', 18, 3, revision=2),
            row('甲街区', 'B1', 18, 9, revision=2),
            row('乙街区', 'B2', 30, 3, population=200),
        ])
        with self.session_factory() as db:
            view = ledger_view(db)
        self.assertEqual(view['excluded_districts'], ['甲街区'])
        quarantined = next(item for item in view['districts'] if item['district'] == '甲街区')
        self.assertTrue(quarantined['excluded'])
        healthy = next(item for item in view['districts'] if item['district'] == '乙街区')
        self.assertFalse(healthy['excluded'])

    def test_higher_revision_clears_quarantine(self):
        self._add([
            row('甲街区', 'B1', 18, 3, revision=2),
            row('甲街区', 'B1', 18, 9, revision=2),
        ])
        self._add([row('甲街区', 'B1', 18, 5, revision=3)])
        with self.session_factory() as db:
            view = ledger_view(db)
        self.assertEqual(view['excluded_districts'], [])

    def test_queued_run_uses_snapshot_then_new_run_uses_corrected_ledger(self):
        class _Request:
            def __init__(self, title):
                self.title = title
                self.parameters = {}

        self._add([row('甲街区', 'B1', 20, 2)])
        with self.session_factory.begin() as db:
            old_run = create_run(db, _Request('第一次分析'), 'analyst@demo.local')
            old_run_id = old_run.id
            old_snapshot_sample = old_run.ledger_snapshot['effective'][0]['sample']

        # 更正入账：抽检/缺牌随新版改变
        self._add([row('甲街区', 'B1', 20, 8, revision=2)])

        # 队列里的旧分析仍按发起时的旧账算
        with self.session_factory.begin() as db:
            self.assertTrue(run_pending(db))
        with self.session_factory() as db:
            finished = db.get(AnalysisRun, old_run_id)
        old_missing = next(item for item in finished.result['rows']
                           if item['district'] == '甲街区')['missing']
        self.assertEqual(old_missing, 2)
        self.assertEqual(old_snapshot_sample, 20)

        # 新分析按新账重算
        with self.session_factory.begin() as db:
            new_run = create_run(db, _Request('更正后分析'), 'analyst@demo.local')
            new_run_id = new_run.id
        with self.session_factory.begin() as db:
            self.assertTrue(run_pending(db))
        with self.session_factory() as db:
            corrected = db.get(AnalysisRun, new_run_id)
        new_missing = next(item for item in corrected.result['rows']
                           if item['district'] == '甲街区')['missing']
        self.assertEqual(new_missing, 8)


class OnlineMigrationTests(unittest.TestCase):
    def test_legacy_rows_are_backfilled_and_indexed(self):
        migration_engine = reset_engine()
        connection = migration_engine.connect()
        # 模拟旧版表结构：只有 source_key/payload/created_at
        connection.execute('CREATE TABLE observations ('
                           'id INTEGER PRIMARY KEY, source_key TEXT UNIQUE, '
                           'payload TEXT, created_at TEXT)')
        connection.execute(
            "INSERT INTO observations (source_key, payload) VALUES "
            "('k1', '{\"district\": \"老街區\", \"batch\": \"L1\", \"population\": 100, "
            "\"sample\": 10, \"missing\": 2}')")
        connection.commit()
        connection.close()

        ensure_schema(migration_engine)

        session_factory = sqlalchemy.sessionmaker(migration_engine)
        with session_factory() as db:
            observation = db.scalars(sqlalchemy.select(Observation)).first()
        self.assertEqual(observation.district, '老街區')
        self.assertEqual(observation.batch, 'L1')
        self.assertEqual(observation.revision, 1)
        self.assertEqual(observation.payload['sample'], 10)

        with migration_engine.connect() as link:
            indexes = link.execute(
                "SELECT name FROM sqlite_master WHERE type='index'").fetchall()
        self.assertIn(('ix_observations_district_batch',), indexes)

        # 幂等：再跑一次不报错、不重复加列
        ensure_schema(migration_engine)


class CsvImportTests(unittest.TestCase):
    def test_new_template_carries_revision_and_blank_defaults_to_one(self):
        rows = parse_csv(
            'district,batch,revision,population,sample,missing\n'
            '甲,B1,,100,20,2\n甲,B1,2,100,20,8\n'.encode('utf-8'))
        self.assertEqual([row['revision'] for row in rows], [1, 2])

    def test_legacy_template_without_revision_column_still_imports(self):
        rows = parse_csv(
            'district,batch,population,sample,missing\n乙,B2,200,30,3\n'.encode('utf-8'))
        self.assertEqual(rows[0]['revision'], 1)
        self.assertEqual(rows[0]['batch'], 'B2')

    def test_end_to_end_csv_correction_flow(self):
        engine = reset_engine()
        Base.metadata.create_all(engine)
        ensure_schema(engine)
        factory = base.Session
        first = parse_csv(
            'district,batch,population,sample,missing\n甲,B1,100,20,2\n'.encode('utf-8'))
        correction = parse_csv(
            'district,batch,revision,population,sample,missing\n甲,B1,2,100,20,8\n'.encode('utf-8'))
        with factory.begin() as db:
            add_observations(db, first, 'analyst', 'observation.imported')
        with factory.begin() as db:
            _items, stats = add_observations(db, correction, 'analyst', 'observation.imported')
        self.assertEqual(stats['corrected'], 1)
        with factory() as db:
            ledger = current_ledger(db)
        self.assertEqual(ledger['effective'][0]['missing'], 8)
        self.assertEqual(ledger['superseded_count'], 1)


if __name__ == '__main__':
    unittest.main(verbosity=2)
