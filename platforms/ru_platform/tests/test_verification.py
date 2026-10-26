from ru_platform.verification import (
    _doc_conds,
    _match_cite,
    extract_refs,
    verify_answer,
)


def test_chinese_labour_articles_are_not_assigned_to_a_later_constitution_mention():
    refs = extract_refs('依据俄罗斯劳动法典第81条，核对解除事由。劳动法典第278条、第288条、第336条另有规定。\n\n俄罗斯联邦宪法另行核对。')
    assert refs['const_articles'] == []
    assert {row['article'] for row in refs['statutes']} >= {'81', '278', '288', '336'}
    assert all('Labour Code' in row['act'] for row in refs['statutes'] if row.get('article'))


def test_english_and_official_russian_code_titles_match():
    assert _match_cite({'title_key': 'Labour Code'}, {'title': 'Трудовой кодекс Российской Федерации'})
    assert _match_cite({'act_number': '152-FZ'}, {'act_number': '152-ФЗ'})


def test_article_with_attached_following_title_overrides_earlier_constitution():
    refs = extract_refs('俄罗斯联邦宪法另有规定。另须核对 Article 312.8 of the Labour Code of the Russian Federation。')
    assert refs['const_articles'] == []
    assert refs['statutes'][0]['article'] == '312.8'
    assert 'Labour Code' in refs['statutes'][0]['act']


def test_actual_constitution_article_is_not_hidden_by_nearby_labour_title():
    refs = extract_refs('俄罗斯联邦宪法第37条规定劳动权。劳动法典另有解除事由。')
    assert refs['const_articles'][0]['article'] == '37'


def test_constitutional_court_is_not_a_constitution_citation():
    refs = extract_refs('俄罗斯刑法典第172.2条规制相关行为。\n\n'
                        '本库未覆盖俄罗斯联邦宪法法院裁判。Article 172.2 的司法适用仍需补证。')
    assert refs['const_articles'] == []
    assert any(row['article'] == '172.2' for row in refs['statutes'])


def test_extracts_russian_federal_law_and_article():
    refs = extract_refs(
        "Федеральный закон № 152-ФЗ «О персональных данных», "
        "Статья 5 устанавливает требования."
    )

    assert refs["statutes"] == [{
        "act": "Федеральный закон № 152-ФЗ «О персональных данных»",
        "title_key": "О персональных данных",
        "act_number": "152-ФЗ",
        "act_year": None,
        "article": "5",
        "display": (
            "Федеральный закон № 152-ФЗ «О персональных данных», Art. 5"
        ),
    }]


def test_official_number_is_the_first_database_anchor():
    conditions = _doc_conds({
        "act_number": "152-ФЗ",
        "title_key": "О персональных данных",
    })

    assert "act_number" in conditions[0][0]
    assert conditions[0][1] == ["152-ФЗ"]


def test_citation_matching_prefers_official_number():
    assert _match_cite(
        {"act_number": "152-ФЗ", "title_key": "О персональных данных"},
        {"act_number": "152-ФЗ", "title": "different rendering"},
    )
    assert not _match_cite(
        {"act_number": "152-ФЗ", "title_key": "О персональных данных"},
        {"act_number": "149-ФЗ", "title": "О персональных данных"},
    )


def test_specific_russian_case_number_is_flagged_without_case_database():
    answer = (
        "Определение Верховного Суда Российской Федерации "
        "от 14.09.2023 № 305-ЭС23-12345 подтверждает этот подход."
    )

    refs = extract_refs(answer)
    assert refs["case_cites"] == [
        "Определение Верховного Суда Российской Федерации "
        "от 14.09.2023 № 305-ЭС23-12345"
    ]
    result = verify_answer(answer, [])
    assert result["summary"]["unverified"] == 1
    assert "判例正文库尚未收录" in result["items"][0]["note"]
