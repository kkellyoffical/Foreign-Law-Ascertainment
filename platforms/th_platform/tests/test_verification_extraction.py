from th_platform.verification import extract_refs


def test_generic_this_act_phrase_is_not_a_statute_title():
    answer = (
        "The parties shall prepare an agreement to control the Data Processor's "
        "obligations for compliance with this Act."
    )
    refs = extract_refs(answer)
    assert refs["statutes"] == []


def test_real_no_year_act_title_is_still_extracted():
    refs = extract_refs("The Foreign Business Act applies to this transaction.")
    assert any(ref["act"] == "Foreign Business Act" for ref in refs["statutes"])
