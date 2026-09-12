import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from partner_monitor.inputs import read_companies
from partner_monitor.pipeline import run
from partner_monitor.ur import extract, snapshot


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.input = self.root / 'companies.csv'
        self.input.write_text('registration_number,name\n40000000001,Example\n40000000002,Missing\n', encoding='utf-8')
        self.source = self.root / 'register.csv'
        self.source.write_text('regcode;name;registered;terminated;type;type_text;address\n'
                               '40000000001;Example;2001-01-01;;SIA;Limited company;Rīga\n', encoding='utf-8')

    def test_replay_preserves_history_and_hash(self):
        first = run(self.input, self.root / 'data', '', self.source)
        second = run(self.input, self.root / 'data', '', self.source)
        self.assertNotEqual(first['run_id'], second['run_id'])
        self.assertEqual(first['snapshot_sha256'], second['snapshot_sha256'])
        self.assertEqual((first['found'], first['not_found']), (1, 1))
        with sqlite3.connect(self.root / 'data' / 'monitoring.db') as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM company_observations').fetchone()[0], 4)
            record = db.execute("SELECT official_json, name_matches FROM company_observations WHERE query_status='FOUND'").fetchone()
            self.assertEqual(json.loads(record[0])['registry_status'], 'REGISTERED')
            self.assertEqual(record[1], 1)

    def test_invalid_source_is_error_not_not_found(self):
        self.source.write_text('<html>unavailable</html>', encoding='utf-8')
        with self.assertRaises(ValueError):
            run(self.input, self.root / 'data', '', self.source)
        with sqlite3.connect(self.root / 'data' / 'monitoring.db') as db:
            self.assertEqual(db.execute('SELECT status FROM monitoring_runs').fetchone()[0], 'FAILED')
            self.assertEqual(db.execute('SELECT DISTINCT query_status FROM company_observations').fetchall(), [('ERROR',)])
            self.assertEqual(db.execute('SELECT COUNT(*) FROM source_snapshots').fetchone()[0], 1)

    def test_duplicate_input_rejected_before_run(self):
        self.input.write_text('registration_number\n40000000001\n40000000001\n', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'duplicate'):
            run(self.input, self.root / 'data', '', self.source)
        self.assertFalse((self.root / 'data').exists())

    def test_leading_zero_is_preserved(self):
        self.input.write_text('registration_number\n00000000001\n', encoding='utf-8-sig')
        self.assertEqual(read_companies(self.input)[0]['registration_number'], '00000000001')

    def test_invalid_number_and_formula_rejected(self):
        for number in ['123', '=10000000000+1', '4e10', '40000000001.0']:
            self.input.write_text('registration_number\n' + number, encoding='utf-8')
            with self.assertRaises(ValueError):
                read_companies(self.input)

    def test_duplicate_source_and_invalid_date_rejected(self):
        with self.source.open('a', encoding='utf-8') as stream:
            stream.write('40000000001;Other;2001-01-01;;SIA;Limited company;Rīga\n')
        with self.assertRaisesRegex(ValueError, 'Duplicate'):
            extract(self.source, {'40000000001'})
        self.source.write_text(self.source.read_text(encoding='utf-8').replace('2001-01-01', 'unknown'), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'date'):
            extract(self.source, {'40000000001'})

    def test_terminated_and_name_mismatch(self):
        self.source.write_text(self.source.read_text(encoding='utf-8').replace('Example;2001-01-01;', 'Renamed;2001-01-01;2020-01-01'), encoding='utf-8')
        run(self.input, self.root / 'data', '', self.source)
        with sqlite3.connect(self.root / 'data' / 'monitoring.db') as db:
            record = db.execute("SELECT official_json, name_matches FROM company_observations WHERE query_status='FOUND'").fetchone()
            self.assertEqual(json.loads(record[0])['registry_status'], 'TERMINATED')
            self.assertEqual(record[1], 0)

    def test_network_failure_redacted(self):
        import requests
        with patch('partner_monitor.ur.requests.get', side_effect=requests.ConnectionError('secret-token')):
            with patch('partner_monitor.ur.time.sleep'):
                with self.assertRaisesRegex(RuntimeError, '^UR download failed after 3 attempts$'):
                    snapshot(self.root / 'raw', 'https://example.com/register.csv')
        self.assertFalse((self.root / 'raw' / 'register.csv.part').exists())


if __name__ == '__main__':
    unittest.main()
