"""The guard between a model's template edit and the owner: shape only, never the content's meaning."""

import pytest

from mailerlm.campaigns import repository, template_guard

HTML, TEXT = repository._built_in_source()


def problems(html=HTML, text=TEXT):
    return template_guard.check(html, text)


def test_the_built_in_template_passes():
    assert problems() == []


@pytest.mark.parametrize(
    "mutation",
    [
        lambda h: h.replace("<body", "<script>alert(1)</script><body", 1),
        lambda h: h.replace("<body", '<iframe src="https://example.com"></iframe><body', 1),
        lambda h: h.replace("<body", '<form action="x"><input></form><body', 1),
        lambda h: h.replace("<body", '<body onload="x()"', 1).replace(
            "<body onload", "<body onload", 1
        ),
        lambda h: h.replace(
            "</body>", '<img src="https://evil.example/p.gif" width="1" height="1"></body>'
        ),
        lambda h: h.replace("</body>", '<img src="//evil.example/p.gif"></body>'),
        lambda h: h.replace("<title>", "<style>@import 'x.css';</style><title>", 1),
        lambda h: h.replace("</body>", '<a href="javascript:alert(1)">x</a></body>'),
        lambda h: h.replace("background:#ffffff", "background:url(https://evil.example/b.png)"),
        lambda h: h.replace("</body>", '<img src="cid:image1" width="1" height="1"></body>'),
    ],
)
def test_forbidden_html_is_rejected(mutation):
    assert mutation(HTML) != HTML
    assert problems(html=mutation(HTML)), "should have been rejected"


def test_an_ordinary_comment_is_inert_but_a_conditional_comment_is_scanned():
    assert problems(html=HTML.replace("<body", "<!-- <script>x</script> -->\n<body", 1)) == []
    assert problems(
        html=HTML.replace("<body", "<!--[if mso]><script>x</script><![endif]-->\n<body", 1)
    )


def test_every_required_placeholder_must_survive():
    for needle in ("{{ body_html | safe }}", "{{ unsubscribe_line }}", "{{ postal_address }}"):
        assert any("required placeholder" in p for p in problems(html=HTML.replace(needle, "")))
    for needle in ("{{ body_text }}", "{{ unsubscribe_line }}", "{{ postal_address }}"):
        assert any("required placeholder" in p for p in problems(text=TEXT.replace(needle, "")))


def test_only_known_placeholders_and_simple_blocks_are_allowed():
    assert any("not allowed" in p for p in problems(html=HTML + "{{ secret }}"))
    assert any("not allowed" in p for p in problems(html=HTML + "{{ ''.__class__ }}"))
    assert any("not allowed" in p for p in problems(html=HTML + "{% include 'x' %}"))
    assert any("not allowed" in p for p in problems(html=HTML + "{% for x in y %}{% endfor %}"))
    assert problems(html=HTML + "{% if preview_text %}{{ preview_text }}{% endif %}") == []


def test_a_frame_that_does_not_render_or_is_huge_is_rejected():
    assert problems(html=HTML + "{% if %}")
    assert any("over" in p for p in problems(html=HTML + "x" * 70000))
    assert problems(html="", text=TEXT)
