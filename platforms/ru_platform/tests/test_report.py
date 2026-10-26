from platforms.ru_platform import report


def test_report_citation_groups_exclude_unavailable_case_layer():
    groups = report._dedupe_citations([
        {
            "citations": [
                {"type": "statute", "section_id": 11, "title": "Federal Law"},
                {"type": "case", "case_id": 22, "case_name": "Invented Case"},
                {"type": "treaty", "chunk_id": 33, "title": "Treaty"},
            ]
        }
    ])

    assert set(groups) == {"statute", "official"}
    assert [item["section_id"] for item in groups["statute"]] == [11]
    assert [item["chunk_id"] for item in groups["official"]] == [33]
