"""Render a campaign into the pieces of an email: subject, HTML, plain text and inline images.

The body is Markdown (tables, links, lists, images, quotes, code); raw HTML in it is not
rendered. The body may use `{{ first_name }}`, `{{ book_title }}` and `{{ sender_name }}`.
Images are local files next to the campaign (or below it); each becomes an inline `cid:`
attachment after `images.prepare`. The footer carries the postal address and the
reply-to-unsubscribe line.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from jinja2 import StrictUndefined
from jinja2.sandbox import SandboxedEnvironment
from markdown_it import MarkdownIt
from markdown_it.token import Token

from mailerlm.campaigns import images

STYLES = {
    "p": "margin:0 0 16px;line-height:1.55;",
    "h1": "margin:24px 0 12px;font-size:24px;line-height:1.25;",
    "h2": "margin:22px 0 10px;font-size:20px;line-height:1.3;",
    "h3": "margin:18px 0 8px;font-size:17px;line-height:1.3;",
    "ul": "margin:0 0 16px;padding-left:22px;",
    "ol": "margin:0 0 16px;padding-left:22px;",
    "li": "margin:0 0 6px;line-height:1.5;",
    "blockquote": "margin:0 0 16px;padding:4px 16px;border-left:3px solid #c9c9c9;color:#555;",
    "a": "color:#1a56c4;",
    "table": "border-collapse:collapse;margin:0 0 16px;width:100%;",
    "th": "border:1px solid #d5d5d5;padding:6px 10px;background:#f3f3f3;text-align:left;",
    "td": "border:1px solid #d5d5d5;padding:6px 10px;text-align:left;",
    "pre": "margin:0 0 16px;padding:12px;background:#f3f3f3;overflow:auto;font-size:14px;",
    "code": "font-family:Menlo,Consolas,monospace;font-size:14px;",
    "hr": "border:0;border-top:1px solid #d5d5d5;margin:24px 0;",
}


class RenderError(ValueError):
    """The campaign cannot be rendered; the message names the line, image or setting."""


@dataclass(frozen=True, slots=True)
class InlineImage:
    content_id: str
    filename: str
    mime: str
    data: bytes


@dataclass(frozen=True, slots=True)
class RenderedEmail:
    subject: str
    html: str
    text: str
    images: tuple[InlineImage, ...]
    unsubscribe_mailto: str
    warnings: tuple[str, ...]


def _markdown() -> MarkdownIt:
    return MarkdownIt("commonmark", {"html": False, "linkify": False}).enable("table")


def _text_of(tokens: list[Token]) -> str:
    """Plain text of a token stream: paragraphs, lists, quotes and tables, links as text (url)."""
    lines: list[str] = []
    buffer = ""
    row: list[str] = []
    in_table = False
    list_stack: list[list] = []  # [ordered, counter]
    link_stack: list[str] = []
    for token in tokens:
        kind = token.type
        if kind == "inline":
            for child in token.children or []:
                if child.type == "text" or child.type == "code_inline":
                    buffer += child.content
                elif child.type in ("softbreak", "hardbreak"):
                    buffer += "\n"
                elif child.type == "link_open":
                    link_stack.append(str(child.attrGet("href") or ""))
                elif child.type == "link_close":
                    href = link_stack.pop()
                    if href and href not in buffer.rsplit("\n", 1)[-1]:
                        buffer += f" ({href})"
                elif child.type == "image":
                    buffer += f"[{child.content}]" if child.content else "[image]"
        elif kind in ("ordered_list_open", "bullet_list_open"):
            list_stack.append([kind == "ordered_list_open", 0])
        elif kind in ("ordered_list_close", "bullet_list_close"):
            list_stack.pop()
            lines.append("")
        elif kind == "list_item_open" and list_stack:
            list_stack[-1][1] += 1
        elif kind == "table_open":
            in_table = True
        elif kind == "table_close":
            in_table = False
            lines.append("")
        elif kind in ("th_close", "td_close"):
            row.append(buffer.strip())
            buffer = ""
        elif kind == "tr_close":
            lines.append(" | ".join(row))
            row = []
        elif kind in ("paragraph_close", "heading_close") and not in_table:
            prefix = ""
            if list_stack:
                ordered, counter = list_stack[-1]
                prefix = f"{counter}. " if ordered else "- "
            if kind == "heading_close":
                lines.append(buffer.strip().upper())
            else:
                lines.append(prefix + buffer.strip())
            if not list_stack:
                lines.append("")
            buffer = ""
        elif kind in ("fence", "code_block"):
            lines += [token.content.rstrip("\n"), ""]
        elif kind == "hr":
            lines += ["-----", ""]
    return "\n".join(lines).strip()


def _style_tokens(tokens: list[Token]) -> None:
    for token in tokens:
        if token.type.endswith("_open") and token.tag in STYLES:
            token.attrSet("style", STYLES[token.tag])
        if token.type == "inline":
            for child in token.children or []:
                if child.type == "link_open":
                    child.attrSet("style", STYLES["a"])
                if child.type == "code_inline":
                    child.attrSet("style", STYLES["code"])


def _body_environment() -> SandboxedEnvironment:
    return SandboxedEnvironment(undefined=StrictUndefined, autoescape=False)


def render(
    subject: str,
    body: str,
    preview_text: str,
    frame_html: str,
    frame_text: str,
    variables: dict[str, str],
    sender_name: str,
    postal_address: str,
    reply_to: str,
    resolve_image: Callable[[str], bytes | None],
    image_budget_bytes: int,
    first_name: str = "there",
) -> RenderedEmail:
    """Everything is passed in; nothing is read from disk. `resolve_image(name)` returns the bytes of
    the campaign's image of that name, or None."""
    warnings: list[str] = []
    placeholders = {**variables, "first_name": first_name, "sender_name": sender_name}
    for key, value in variables.items():
        if not value.strip():
            warnings.append(f"the value '{key}' is empty")
    try:
        text_environment = _body_environment()
        body = text_environment.from_string(body).render(**placeholders)
        subject = text_environment.from_string(subject).render(**placeholders)
        preview_text = text_environment.from_string(preview_text).render(**placeholders)
    except Exception as error:  # jinja raises several types for a bad placeholder
        raise RenderError(
            f"the subject, preview or body uses an unknown value or bad syntax ({error})"
        ) from error
    markdown = _markdown()
    tokens = markdown.parse(body)
    sources: list[tuple[Token, str]] = []
    for token in tokens:
        for child in token.children or []:
            if child.type == "image":
                name = str(child.attrGet("src") or "")
                if "://" in name or name.startswith("data:"):
                    raise RenderError(
                        f"image '{name[:40]}' is a web address; upload the picture to this campaign instead"
                    )
                if resolve_image(name) is None:
                    raise RenderError(f"image '{name}' is not uploaded to this campaign")
                if not child.content.strip():
                    warnings.append(f"image '{name}' has no alt text")
                sources.append((child, name))
    per_image_budget = image_budget_bytes // max(len(sources), 1)
    seen: dict[str, tuple[str, images.PreparedImage]] = {}
    inline_images: list[InlineImage] = []
    for child, name in sources:
        if name not in seen:
            data = resolve_image(name) or b""
            try:
                prepared = images.prepare_cached(data, per_image_budget)
            except images.ImageTooLarge as error:
                raise RenderError(f"image '{name}': {error}") from error
            content_id = f"image{len(seen) + 1}"
            seen[name] = (content_id, prepared)
            inline_images.append(
                InlineImage(
                    content_id, f"{content_id}.{prepared.extension}", prepared.mime, prepared.data
                )
            )
        content_id, prepared = seen[name]
        child.attrSet("src", f"cid:{content_id}")
        child.attrSet("width", str(prepared.display_width))
        child.attrSet("height", str(prepared.display_height))
        child.attrSet("style", "max-width:100%;height:auto;display:block;margin:0 0 16px;")
    _style_tokens(tokens)
    body_html = markdown.renderer.render(tokens, markdown.options, {})
    body_text = _text_of(tokens)
    context = {
        "subject": subject,
        "preview_text": preview_text,
        "body_html": body_html,
        "body_text": body_text,
        "postal_address": postal_address or "[postal address not set]",
        "unsubscribe_line": "Reply UNSUBSCRIBE to this email and I will take you off the list.",
        "sender_name": sender_name,
    }
    if not postal_address:
        warnings.append("no postal address set (Settings or this campaign); a real send needs one")
    if not reply_to:
        warnings.append("no reply-to address set; unsubscribe by reply cannot work")
    try:
        html_out = (
            SandboxedEnvironment(autoescape=True, undefined=StrictUndefined)
            .from_string(frame_html)
            .render(**context)
        )
        text_out = (
            SandboxedEnvironment(autoescape=False, undefined=StrictUndefined)
            .from_string(frame_text)
            .render(**context)
        )
    except Exception as error:  # a template the owner edited
        raise RenderError(f"the template has a problem ({error})") from error
    return RenderedEmail(
        subject=subject,
        html=html_out,
        text=text_out,
        images=tuple(inline_images),
        unsubscribe_mailto=f"mailto:{reply_to}?subject=unsubscribe" if reply_to else "",
        warnings=tuple(warnings),
    )
