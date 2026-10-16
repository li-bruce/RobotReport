import unittest
from app.score_parser import parse_score


class ScoreParserTests(unittest.TestCase):
    def test_requested_natural_variants(self):
        for text in ('周二双打，第十二场21：19', '周二双打，12场21-19',
                     '周二双打，12 21:19', '周二双打，十二21—19',
                     '周二双打,第12场21比19', '周二双打， 十二 场 21 / 19'):
            with self.subTest(text=text):
                self.assertEqual(parse_score(text), {'title': '周二双打', 'event_id': None, 'number': 12, 'scores': [[21, 19]]})

    def test_score_separators_and_multigame_delimiters(self):
        for separator in (':', '：', '-', '－', '—', '–', '比', '/'):
            with self.subTest(separator=separator):
                self.assertEqual(parse_score(f'第2场21{separator}19')['scores'], [[21, 19]])
        for separator in (',', '，', ';', '；'):
            with self.subTest(separator=separator):
                self.assertEqual(parse_score(f'双打赛，二场21:19{separator}18:21{separator}21:16')['scores'], [[21, 19], [18, 21], [21, 16]])

    def test_legacy_event_id_and_unnamed_forms(self):
        self.assertEqual(parse_score('赛事 15 第2场21:19'), {'title': None, 'event_id': 15, 'number': 2, 'scores': [[21, 19]]})
        self.assertEqual(parse_score('第2场21:19')['number'], 2)
        self.assertEqual(parse_score('2 21:19')['number'], 2)
        self.assertEqual(parse_score('十二21—19')['number'], 12)
        self.assertIsNone(parse_score('名单'))
        self.assertIsNone(parse_score('组织比赛 单打'))
        self.assertIsNone(parse_score('2026-10-13 18:00 报名'))
        self.assertIsNone(parse_score('2026-10-13 名单'))
        self.assertIsNone(parse_score(None))

    def test_chinese_ordinals(self):
        for text, expected in [('十', 10), ('十一', 11), ('两', 2), ('一百零二', 102),
                               ('一千〇一十', 1010), ('九千九百九十九', 9999), ('〇一二', 12), ('10000', 10000)]:
            with self.subTest(text=text):
                self.assertEqual(parse_score(f'某赛，第{text}场21:19')['number'], expected)

    def test_ambiguous_arabic_number_needs_explicit_boundary(self):
        for text in ('某赛，1221-19', '第1221:19', '赛事 2 1221-19'):
            with self.subTest(text=text), self.assertRaisesRegex(ValueError, '场次与比分之间'):
                parse_score(text)

    def test_number_ranges_and_invalid_chinese(self):
        for text in ('某赛，0场21:19', '第零场21:19', '10001场21:19', '一百千场21:19', '十二三场21:19', '赛事 0 第1场21:19'):
            with self.subTest(text=text), self.assertRaises(ValueError):
                parse_score(text)
        for text in ('第1场1000:0', '第1场21:9999', '第1场21:19，', '第1场21:19，垃圾', '第1场21:19:15'):
            with self.subTest(text=text), self.assertRaises(ValueError):
                parse_score(text)

    def test_no_event_binding_or_winner_rules_are_inferred(self):
        self.assertEqual(parse_score('还不存在的比赛，第3场20:20')['scores'], [[20, 20]])
        self.assertEqual(parse_score('第1场0:999')['scores'], [[0, 999]])
        with self.assertRaises(ValueError):
            parse_score('A' * 1001)

    def test_unnamed_multigame_is_not_misread_as_title(self):
        result = parse_score('第12场21:19,18:21,21:18')
        self.assertIsNone(result['title'])
        self.assertEqual(result['number'], 12)
        self.assertEqual(result['scores'], [[21, 19], [18, 21], [21, 18]])

    def test_default_iso_date_event_title_preserves_literal_and_score(self):
        for score in ('第1场21:19', '1场21－19', '1 21:19', '一21：19'):
            with self.subTest(score=score):
                self.assertEqual(parse_score('2026-10-12 积分赛，' + score), {
                    'title': '2026-10-12 积分赛', 'event_id': None, 'number': 1, 'scores': [[21, 19]]})
        self.assertEqual(parse_score('2026-10-12 积分赛，第2场21:19，18:21，21:16')['scores'],
                         [[21, 19], [18, 21], [21, 16]])
        for text in ('2026-10-12 报名', '2026-10-12 18:00 报名',
                     '2026-10-12 报名，甲，乙', '2026-10-12 积分赛，对战表'):
            with self.subTest(text=text):
                self.assertIsNone(parse_score(text))


if __name__ == '__main__':
    unittest.main()
