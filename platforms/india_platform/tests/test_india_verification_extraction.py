from india_platform.verification import extract_refs


def test_section_prefers_preceding_act_over_later_act_in_same_sentence():
    refs = extract_refs(
        "Code of Civil Procedure §44A applies, while the Limitation Act governs timing."
    )

    assert refs["statutes"][0]["display"] == "Code of Civil Procedure §44A"


def test_section_can_bind_to_explicit_following_act_name():
    refs = extract_refs("Section 13 of the Code of Civil Procedure limits recognition.")

    assert refs["statutes"][0]["display"] == "Code of Civil Procedure §13"


def test_section_does_not_cross_sentence_boundary_to_another_act():
    refs = extract_refs(
        "Code of Civil Procedure §44A applies.\nLimitation Act §3 governs limitation."
    )

    displays = {ref["display"] for ref in refs["statutes"]}
    assert "Code of Civil Procedure §44A" in displays
    assert "Limitation Act §3" in displays
