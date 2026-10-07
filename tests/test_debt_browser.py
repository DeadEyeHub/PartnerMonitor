import unittest
from datetime import date

from partner_monitor.debt_browser import parse_result, query_date

REG = '00000000004'
DAY = date(2026,9,9)
PREFIX = '2026.gada septembra 9.datumā nodokļu maksātājam "Example 00000000004" '
NO_DEBT = PREFIX+'nav VID administrēto nodokļu (nodevu) parāda, kas kopsummā pārsniedz 150 euro.'


class DebtBrowserTests(unittest.TestCase):
    def test_available_date_skips_two_workdays_and_weekends(self):
        self.assertEqual(query_date(date(2026,9,12),set()),DAY)
        self.assertEqual(query_date(date(2026,9,14),set()),DAY)

    def test_available_date_skips_holidays(self):
        self.assertEqual(query_date(date(2026,9,12),{date(2026,9,10)}),date(2026,9,8))

    def test_no_published_debt_is_not_zero(self):
        row = parse_result(NO_DEBT,REG,DAY)
        self.assertEqual(row['query_status'],'NO_PUBLISHED_DEBT_ABOVE_THRESHOLD')
        self.assertIsNone(row['published_debt_amount'])

    def test_published_amount_is_exact(self):
        row = parse_result(PREFIX+'ir VID administrēto nodokļu (nodevu) parāds 1 234,56 euro.',REG,DAY)
        self.assertEqual(row['published_debt_amount'],'1234.56')

    def test_wrong_company_date_and_unknown_wording_rejected(self):
        for text,reg,day in [(NO_DEBT,'40000000001',DAY),(NO_DEBT,REG,date(2026,9,8)),
                             (PREFIX+'sesijas noilgums',REG,DAY),('No data',REG,DAY)]:
            with self.subTest(text=text,reg=reg,day=day),self.assertRaises(ValueError):
                parse_result(text,reg,day)

    def test_current_debt_is_not_replaced_by_component_amounts(self):
        text=PREFIX+'ir VID administrēto nodokļu (nodevu) parāds 7302.15 euro apmērā, tai skaitā: parāda summa 0.00 euro; parāda summa 7302.15 euro.'
        self.assertEqual(parse_result(text,REG,DAY)['published_debt_amount'],'7302.15')

    def test_pdf_spacing_and_decimal_threshold(self):
        row = parse_result(NO_DEBT.replace('gada','gada\n').replace('150 euro','150.00 euro'),REG,DAY)
        self.assertIsNone(row['published_debt_amount'])
