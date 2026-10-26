import asyncio

from th_platform import agent_core


class FakeContext:
    def __init__(self):
        self.context = {"seen": set(), "cited": [], "tool_trace": []}


def _detail_row(translation_official):
    return {
        "section_id": 12,
        "document_id": 3,
        "section_index": 4,
        "section_label": "19",
        "section_text": "The Data Controller shall request consent.",
        "language": "en",
        "title": "Personal Data Protection Act B.E. 2562 (2019)",
        "act_year": 2019,
        "document_type": "law",
        "status": "in_force",
        "effective_from": "2019-05-28",
        "source": "public-source",
        "source_url": "https://example.go.th/pdpa",
        "pdf_url": None,
        "source_authority": "Thai government agency",
        "translation_source": "Government-hosted English translation",
        "translation_official": translation_official,
    }


def test_citation_retains_translation_authority_flag():
    citation = agent_core._statute_citation(12, _detail_row(False))

    assert citation["translation_official"] is False
    assert citation["effective_from"] == "2019-05-28"
    assert agent_core._DOC_TYPE_ZH["law"] == "法律"
    assert agent_core._DOC_TYPE_ZH["royal_decree"] == "皇家法令"


def test_detail_labels_nonofficial_translation_without_claiming_machine_translation(monkeypatch):
    monkeypatch.setattr(agent_core.db, "query_one_statutes", lambda *_args: _detail_row(False))
    monkeypatch.setattr(agent_core.db, "query_all_statutes", lambda *_args: [])

    output = asyncio.run(agent_core._one_statute_detail(FakeContext(), 12))

    assert "非官方英译" in output
    assert "机器翻译" not in output
    assert "联邦法律" not in output
    assert "Section 19" in output
    assert "生效日：2019-05-28" in output


def test_detail_distinguishes_government_hosted_translation(monkeypatch):
    monkeypatch.setattr(agent_core.db, "query_one_statutes", lambda *_args: _detail_row(True))
    monkeypatch.setattr(agent_core.db, "query_all_statutes", lambda *_args: [])

    output = asyncio.run(agent_core._one_statute_detail(FakeContext(), 12))

    assert "官方机构提供或托管的英译" in output
    assert "以泰语官方文本为准" in output


def test_detail_labels_thai_source_as_official_original(monkeypatch):
    row = {
        **_detail_row(False),
        "language": "th",
        "section_text": "มาตรา ๑๙ ผู้ควบคุมข้อมูลส่วนบุคคล",
    }
    monkeypatch.setattr(agent_core.db, "query_one_statutes", lambda *_args: row)
    monkeypatch.setattr(agent_core.db, "query_all_statutes", lambda *_args: [])

    output = asyncio.run(agent_core._one_statute_detail(FakeContext(), 12))

    assert "OCS 官方泰文原文" in output
    assert "非官方英译" not in output
    assert "มาตรา 19" in output
