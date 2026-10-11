"""front matter (starters) and rendering: tables, images, raw HTML left out, footer, values."""

import io

import pytest
from PIL import Image

from mailerlm.campaigns import frontmatter, render, repository

FRAME_HTML = "<html><body>{{ body_html | safe }}<footer>{{ unsubscribe_line }} {{ sender_name }}, {{ postal_address }}</footer></body></html>"
FRAME_TEXT = (
    "{{ body_text }}\n--\n{{ unsubscribe_line }}\n{{ sender_name }}, {{ postal_address }}\n"
)

BODY = """Hi {{ first_name }}, about {{ book_title }}.

| A | B |
|---|---|
| 1 | 2 |

![cover](cover.png)

<script>alert(1)</script>

A [link](https://example.com/x) here.
"""


def png() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (800, 400), "navy").save(buffer, "PNG")
    return buffer.getvalue()


def email(body=BODY, **overrides):
    arguments = dict(
        subject="Hello {{ book_title }}",
        body=body,
        preview_text="",
        frame_html=FRAME_HTML,
        frame_text=FRAME_TEXT,
        variables={"book_title": "The Book"},
        sender_name="Ann",
        postal_address="1 Main St",
        reply_to="ann+book@example.com",
        resolve_image=lambda name: png() if name == "cover.png" else None,
        image_budget_bytes=100 * 1024,
    )
    arguments.update(overrides)
    return render.render(**arguments)


def test_starter_front_matter_is_parsed_and_validated():
    campaign = frontmatter.parse('---\nsubject: "S"\nlists: [a]\ntags: [t]\n---\nbody here')
    assert campaign.lists == ("a",) and campaign.tags == ("t",) and campaign.body == "body here"
    assert campaign.track_clicks is False and campaign.kind == "campaign"


@pytest.mark.parametrize(
    "text, message",
    [
        ("no front matter", "must start with"),
        ("---\nsubject: x\n", "not closed"),
        ("---\nlists: [a]\n---\nbody", "needs a 'subject'"),
        ("---\nsubject: x\nbogus: 1\n---\nb", "unknown front matter key 'bogus'"),
        ("---\nsubject: x\nkind: weird\n---\nb", "'kind' must be"),
        ("---\nsubject: x\ntrack_clicks: yes please\n---\nb", "true or false"),
    ],
)
def test_bad_front_matter_names_the_problem(text, message):
    with pytest.raises(frontmatter.CampaignFileError, match=message):
        frontmatter.parse(text)


def test_the_email_has_a_table_an_inline_image_a_footer_and_no_raw_html():
    result = email()
    assert result.subject == "Hello The Book"
    assert "<table" in result.html and "<th" in result.html
    assert 'src="cid:image1"' in result.html and result.images[0].content_id == "image1"
    assert "<script>" not in result.html and "&lt;script&gt;" in result.html
    assert "1 Main St" in result.html and "Reply UNSUBSCRIBE" in result.text
    assert "Hi there, about The Book." in result.text and "A | B" in result.text
    assert "link (https://example.com/x)" in result.text
    assert result.unsubscribe_mailto == "mailto:ann+book@example.com?subject=unsubscribe"


def test_missing_postal_address_and_empty_values_warn():
    result = email(postal_address="", variables={"book_title": ""})
    assert any("postal address" in w for w in result.warnings)
    assert any("'book_title' is empty" in w for w in result.warnings)


def test_a_web_image_or_an_unknown_image_is_rejected():
    with pytest.raises(render.RenderError, match="web address"):
        email("![x](https://example.com/a.png)")
    with pytest.raises(render.RenderError, match="not uploaded"):
        email("![x](gone.png)")


def test_an_unknown_value_is_an_error():
    with pytest.raises(render.RenderError, match="unknown value"):
        email("Hi {{ nope }}")


def test_a_broken_template_is_an_error_not_a_crash():
    with pytest.raises(render.RenderError, match="template"):
        email(frame_html="{{ body_html | safe }} {{ undefined_thing }}")


def test_missing_alt_text_warns():
    assert any("alt text" in w for w in email("![](cover.png)").warnings)


def test_a_template_must_keep_the_place_for_the_letter():
    import sqlite3

    connection = sqlite3.connect(":memory:")
    with pytest.raises(repository.CampaignsError, match="body_html"):
        repository.template_save(connection, "x", "<html></html>", "{{ body_text }}")
