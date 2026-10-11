"""front matter and rendering: tables, images, raw HTML left out, footer, placeholders."""

import pytest
from PIL import Image

from mailerlm.campaigns import frontmatter, render

FILE = """---
subject: "Hello {{ book_title }}"
lists: [book-interest]
tags: [early-access]
---
Hi {{ first_name }},

| A | B |
|---|---|
| 1 | 2 |

![cover](cover.png)

<script>alert(1)</script>

A [link](https://example.com/x) here.
"""


def parts(tmp_path, text=FILE, **overrides):
    Image.new("RGB", (800, 400), "navy").save(tmp_path / "cover.png")
    campaign = frontmatter.parse(text)
    arguments = dict(
        campaign_directory=tmp_path,
        book_title="The Book",
        sender_name="Ann",
        postal_address="1 Main St",
        reply_to="ann+book@example.com",
        image_budget_bytes=100 * 1024,
        image_cache_directory=tmp_path / "c",
    )
    arguments.update(overrides)
    return render.render(campaign, **arguments)


def test_front_matter_is_parsed_and_validated():
    campaign = frontmatter.parse(FILE)
    assert campaign.lists == ("book-interest",) and campaign.tags == ("early-access",)
    assert campaign.track_clicks is False and campaign.kind == "campaign"
    assert campaign.body.startswith("Hi {{ first_name }}")


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


def test_the_email_has_a_table_an_inline_image_a_footer_and_no_raw_html(tmp_path):
    email = parts(tmp_path)
    assert email.subject == "Hello The Book"
    assert "<table" in email.html and "<th" in email.html
    assert 'src="cid:image1"' in email.html and email.images[0].content_id == "image1"
    assert "<script>" not in email.html and "&lt;script&gt;" in email.html
    assert "1 Main St" in email.html and "Reply UNSUBSCRIBE" in email.text
    assert "Hi there," in email.text and "A | B" in email.text and "[cover]" in email.text
    assert "link (https://example.com/x)" in email.text
    assert email.unsubscribe_mailto == "mailto:ann+book@example.com?subject=unsubscribe"


def test_missing_postal_address_warns_and_marks_the_footer(tmp_path):
    email = parts(tmp_path, postal_address="")
    assert any("postal_address" in w for w in email.warnings)
    assert "[postal address not set" in email.html


def test_a_web_image_or_an_escaping_path_is_rejected(tmp_path):
    with pytest.raises(render.RenderError, match="web address"):
        parts(tmp_path, "---\nsubject: s\n---\n![x](https://example.com/a.png)")
    with pytest.raises(render.RenderError, match="outside the campaign folder"):
        parts(tmp_path, "---\nsubject: s\n---\n![x](../secret.png)")
    with pytest.raises(render.RenderError, match="does not exist"):
        parts(tmp_path, "---\nsubject: s\n---\n![x](gone.png)")


def test_an_unknown_placeholder_is_an_error(tmp_path):
    with pytest.raises(render.RenderError, match="placeholder"):
        parts(tmp_path, "---\nsubject: s\n---\nHi {{ nope }}")


def test_missing_alt_text_warns(tmp_path):
    assert any(
        "alt text" in w for w in parts(tmp_path, "---\nsubject: s\n---\n![](cover.png)").warnings
    )
