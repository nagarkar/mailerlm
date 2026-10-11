"""parsing: the shapes the owner pastes, with no database."""

from mailerlm.contacts import mask_email, parsing


def test_the_shapes_a_person_pastes():
    text = (
        "Rahul.Mehendale@Example.com\n"
        "Nelly: nellyv@example.com\n"
        "Zhenhua Zhang <zz@example.com>\n"
        "Gurjant Kalsi (Kalsi): gk@example.com\n"
        "Mukesh Agrawal: mq@example.com.\n"
        "https://www.linkedin.com/in/some-person-123?lipi=urn%3Ali\n"
    )
    found, rejected = parsing.parse(text)
    assert [c.email for c in found] == [
        "rahul.mehendale@example.com",
        "nellyv@example.com",
        "zz@example.com",
        "gk@example.com",
        "mq@example.com",
        None,
    ]
    assert [c.name for c in found][1:5] == [
        "Nelly",
        "Zhenhua Zhang",
        "Gurjant Kalsi (Kalsi)",
        "Mukesh Agrawal",
    ]
    assert found[5].linkedin_url == "https://www.linkedin.com/in/some-person-123"
    assert rejected == []


def test_a_line_with_nothing_usable_is_rejected_by_number_and_not_echoed():
    found, rejected = parsing.parse("hello there\nada@example.com\n\nnonsense")
    assert len(found) == 1 and rejected == [1, 4]


def test_a_csv_with_an_email_header_carries_name_and_tags():
    found, rejected = parsing.parse("email,name,tags\nada@example.com,Ada,early;beta\n,Nobody,\n")
    assert found[0].tags == ("early", "beta") and found[0].name == "Ada" and rejected == [3]


def test_masking_hides_the_address():
    assert mask_email("ada@example.com") == "a***@e***.com"
    assert mask_email(None) == "(no email)"
