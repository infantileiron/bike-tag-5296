"""认账台账与分析算法的规则测试（仅用标准库）。

运行：python3 tests/test_ledger_rules.py
"""

import sys
import unittest
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from src.domain.ledger import build_ledger  # noqa: E402
from src.domain.analysis import analyze  # noqa: E402


def row(district, batch, sample, missing, *, population=100, revision=1):
    return dict(district=district, batch=batch, population=population,
                sample=sample, missing=missing, revision=revision)


class LedgerRules(unittest.TestCase):
    def test_correction_replaces_old_version_even_when_old_arrives_later(self):
        # v2 先到，v1 乱序后到；当前账只认 v2。
        ledger = build_ledger([
            row('甲街区', 'B1', 20, 4, revision=2),
            row('甲街区', 'B1', 18, 3, revision=1),
        ])
        self.assertEqual(len(ledger['effective']), 1)
        effective = ledger['effective'][0]
        self.assertEqual(effective['sample'], 20)
        self.assertEqual(effective['missing'], 4)
        self.assertEqual(effective['revision'], 2)
        self.assertEqual(ledger['superseded_count'], 1)
        self.assertEqual(ledger['excluded_districts'], [])

    def test_missing_revision_defaults_to_one_and_still_loses_to_newer(self):
        first = row('甲街区', 'B1', 18, 3)
        first.pop('revision')
        ledger = build_ledger([first, row('甲街区', 'B1', 20, 4, revision=2)])
        self.assertEqual(ledger['effective'][0]['sample'], 20)

    def test_moved_sample_and_missing_follow_correction_to_original_key(self):
        # 调去别处后，原街区批次的新版整体替换抽检数和缺牌数，不做累加。
        ledger = build_ledger([
            row('甲街区', 'B1', 18, 3, revision=1),
            row('甲街区', 'B1', 12, 1, revision=2),
        ])
        effective = ledger['effective'][0]
        self.assertEqual((effective['sample'], effective['missing']), (12, 1))

    def test_same_revision_two_datasets_quarantines_district_only(self):
        ledger = build_ledger([
            row('甲街区', 'B1', 18, 3, revision=2),
            row('甲街区', 'B1', 18, 9, revision=2),
            row('乙街区', 'B2', 20, 2),
        ])
        self.assertEqual(ledger['excluded_districts'], ['甲街区'])
        self.assertEqual(ledger['conflicts'][0]['versions'], 2)
        # 甲街区不出现在生效记录里，乙街区照常。
        self.assertEqual([item['district'] for item in ledger['effective']], ['乙街区'])

    def test_identical_redelivery_is_idempotent_and_creates_no_extra_batch(self):
        duplicate = row('甲街区', 'B1', 18, 3)
        ledger = build_ledger([duplicate, dict(duplicate)])
        self.assertEqual(len(ledger['effective']), 1)
        self.assertEqual(ledger['conflicts'], [])

    def test_higher_revision_resolves_conflict_and_reinstates_district(self):
        ledger = build_ledger([
            row('甲街区', 'B1', 18, 3, revision=2),
            row('甲街区', 'B1', 18, 9, revision=2),
            row('甲街区', 'B1', 18, 5, revision=3),
        ])
        self.assertEqual(ledger['excluded_districts'], [])
        self.assertEqual(ledger['effective'][0]['missing'], 5)
        self.assertEqual(ledger['superseded_count'], 2)

    def test_cross_district_transfer_keeps_original_key_and_new_key_separate(self):
        # 原批次在甲街区更正；调到乙街区的新批次独立认账。
        ledger = build_ledger([
            row('甲街区', 'B1', 18, 3, revision=1),
            row('甲街区', 'B1', 10, 1, revision=2),
            row('乙街区', 'B9', 8, 2),
        ])
        keys = sorted((item['district'], item['batch']) for item in ledger['effective'])
        self.assertEqual(keys, [('乙街区', 'B9'), ('甲街区', 'B1')])
        first = next(item for item in ledger['effective'] if item['district'] == '甲街区')
        self.assertEqual(first['sample'], 10)


class AnalysisWithLedger(unittest.TestCase):
    PARAMETERS = {'confidence': 95}

    def test_healthy_districts_still_produce_purchase_numbers(self):
        ledger = build_ledger([row('甲街区', 'B1', 20, 4), row('乙街区', 'B2', 30, 3, population=200)])
        result = analyze(ledger['effective'], self.PARAMETERS, ledger=ledger)
        detail = [item for item in result['rows'] if item['district'] == '甲街区'][0]
        self.assertIsNotNone(detail['purchase'])
        self.assertEqual(detail['status'], '正常备料')
        self.assertEqual(result['excluded_districts'], [])

    def test_quarantined_district_has_no_purchase_but_total_lists_others(self):
        ledger = build_ledger([
            row('甲街区', 'B1', 18, 3, revision=2),
            row('甲街区', 'B1', 18, 9, revision=2),
            row('乙街区', 'B2', 30, 3, population=200),
        ])
        result = analyze(ledger['effective'], self.PARAMETERS, ledger=ledger)
        by_district = {item['district']: item for item in result['rows']}
        self.assertIsNone(by_district['甲街区']['purchase'])
        self.assertIn('暂不出采购量', by_district['甲街区']['status'])
        self.assertIsNotNone(by_district['乙街区']['purchase'])
        total = by_district['街区加权总计']
        self.assertIsNotNone(total['purchase'])
        self.assertIn('甲街区', result['summary'])
        self.assertEqual(result['excluded_districts'], ['甲街区'])

    def test_all_districts_quarantined_yields_no_budget_not_a_crash(self):
        ledger = build_ledger([
            row('甲街区', 'B1', 18, 3, revision=2),
            row('甲街区', 'B1', 18, 9, revision=2),
        ])
        result = analyze(ledger['effective'], self.PARAMETERS, ledger=ledger)
        total = next(item for item in result['rows'] if item['district'] == '街区加权总计')
        self.assertIsNone(total['purchase'])
        self.assertIn('暂缓备料', result['summary'])

    def test_correction_changes_estimate_between_two_ledger_versions(self):
        before = build_ledger([row('甲街区', 'B1', 20, 2)])
        after = build_ledger([row('甲街区', 'B1', 20, 2), row('甲街区', 'B1', 20, 8, revision=2)])
        old_result = analyze(before['effective'], self.PARAMETERS, ledger=before)
        new_result = analyze(after['effective'], self.PARAMETERS, ledger=after)
        old_estimate = next(item for item in old_result['rows'] if item['district'] == '甲街区')['estimate']
        new_estimate = next(item for item in new_result['rows'] if item['district'] == '甲街区')['estimate']
        self.assertGreater(new_estimate, old_estimate)

    def test_result_is_json_safe_with_null_cells(self):
        import json
        ledger = build_ledger([
            row('甲街区', 'B1', 18, 3, revision=2),
            row('甲街区', 'B1', 18, 9, revision=2),
        ])
        result = analyze(ledger['effective'], self.PARAMETERS, ledger=ledger)
        json.dumps(result, ensure_ascii=False, allow_nan=False)


if __name__ == '__main__':
    unittest.main(verbosity=2)
