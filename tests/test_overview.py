import csv
import tempfile
import unittest
from pathlib import Path
from partner_monitor.overview import FIELDS,export_csv


class OverviewTests(unittest.TestCase):
    def test_exact_task_fields_missing_values_and_safe_text(self):
        row=dict(zip(FIELDS,['=Example','00000000001','','NOT_ASSESSED',85.7,'Pending assessment','','Review']))
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'latest.csv';export_csv([row],path)
            with path.open(encoding='utf-8-sig',newline='') as stream:
                reader=csv.DictReader(stream);saved=list(reader)
                self.assertEqual(reader.fieldnames,FIELDS)
            self.assertEqual(saved[0]['Registration number'],'00000000001')
            self.assertEqual(saved[0]['Reliability score'],'')
            self.assertEqual(saved[0]['New findings'],'')
            self.assertEqual(saved[0]['Company'],"'=Example")
