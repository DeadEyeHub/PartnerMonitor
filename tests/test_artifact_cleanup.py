import os
from pathlib import Path
import tempfile
import unittest
from partner_monitor.artifact_cleanup import cleanup
class CleanupTests(unittest.TestCase):
 def test_only_old_reproducible_artifacts_are_removed(self):
  with tempfile.TemporaryDirectory() as folder:
   root=Path(folder);(root/'assessments').mkdir();(root/'evidence').mkdir()
   keep=['latest.xlsx','report-old.html','assessments/old.json','evidence/old.pdf','recent.tmp.xlsx']
   for name in keep+['old.tmp.xlsx.inspect.ndjson']:
    p=root/name;p.write_text('fixture')
    if name!='recent.tmp.xlsx':os.utime(p,(1,1))
   self.assertEqual(len(cleanup(root)['candidates']),1)
   self.assertTrue((root/'old.tmp.xlsx.inspect.ndjson').exists())
   cleanup(root,apply=True)
   self.assertTrue(all((root/name).exists() for name in keep))
   self.assertFalse((root/'old.tmp.xlsx.inspect.ndjson').exists())
