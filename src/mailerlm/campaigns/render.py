"""Render a campaign into the pieces of an email: subject, HTML, plain text and inline images.

The body is Markdown (tables, links, lists, images, quotes, code); raw HTML in it is not
rendered. The body may use `{{ first_name }}`, `{{ book_title }}` and `{{ sender_name }}`.
Images are local files next to the campaign (or below it); each becomes an inline `cid:`
attachment after `images.prepare`. The footer carries the postal address and the
reply-to-unsubscribe line.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape
from jinja2.sandbox import SandboxedEnvironment
from markdown_it import MarkdownIt
from markdown_it.token import Token

from mailerlm.campaigns import images
from mailerlm.campaigns.frontmatter import Campaign

TEMPLATE_DIRECTORY = Path(__file__).parent / "templates"
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
    campaign: Campaign,
    campaign_directory: Path,
    book_title: str,
    sender_name: str,
    postal_address: str,
    reply_to: str,
    image_budget_bytes: int,
    image_cache_directory: Path,
    first_name: str = "there",
    allowed_root: Path | None = None,
) -> RenderedEmail:
    warnings: list[str] = []
    try:
        placeholders = dict(first_name=first_name, book_title=book_title, sender_name=sender_name)
        body_environment = _body_environment()
        body = body_environment.from_string(campaign.body).render(**placeholders)
        subject = body_environment.from_string(campaign.subject).render(**placeholders)
    except Exception as error:  # jinja raises several types for a bad placeholder
        raise RenderError(
            f"the subject or body uses an unknown placeholder or bad syntax ({error})"
        ) from error
    markdown = _markdown()
    tokens = markdown.parse(body)
    root = (allowed_root or campaign_directory).resolve()
    inline_images: list[InlineImage] = []
    sources: list[tuple[Token, Path]] = []
    for token in tokens:
        for child in token.children or []:
            if child.type == "image":
                source = str(child.attrGet("src") or "")
                if "://" in source or source.startswith("data:"):
                    raise RenderError(
                        f"image '{source[:40]}' is a web address; use a local file next to the campaign"
                    )
                path = (campaign_directory / source).resolve()
                if root not in path.parents and path != root:
                    raise RenderError(f"image '{source}' is outside the campaign folder")
                if not path.is_file():
                    raise RenderError(f"image '{source}' does not exist next to the campaign")
                if not child.content.strip():
                    warnings.append(f"image '{source}' has no alt text")
                sources.append((child, path))
    per_image_budget = image_budget_bytes // max(len(sources), 1)
    seen: dict[Path, tuple[str, images.PreparedImage]] = {}
    for child, path in sources:
        if path not in seen:
            try:
                prepared = images.prepare_cached(path, per_image_budget, image_cache_directory)
            except images.ImageTooLarge as error:
                raise RenderError(f"image '{path.name}': {error}") from error
            content_id = f"image{len(seen) + 1}"
            seen[path] = (content_id, prepared)
            inline_images.append(
                InlineImage(
                    content_id, f"{content_id}.{prepared.extension}", prepared.mime, prepared.data
                )
            )
        content_id, prepared = seen[path]
        child.attrSet("src", f"cid:{content_id}")
        child.attrSet("width", str(prepared.display_width))
        child.attrSet("height", str(prepared.display_height))
        child.attrSet("style", "max-width:100%;height:auto;display:block;margin:0 0 16px;")
    _style_tokens(tokens)
    body_html = markdown.renderer.render(tokens, markdown.options, {})
    body_text = _text_of(tokens)
    unsubscribe_mailto = f"mailto:{reply_to}?subject=unsubscribe" if reply_to else ""
    context = {
        "subject": subject,
        "preview_text": campaign.preview_text,
        "body_html": body_html,
        "body_text": body_text,
        "postal_address": postal_address or "[postal address not set in config.toml]",
        "unsubscribe_line": "Reply UNSUBSCRIBE to this email and I will take you off the list.",
        "sender_name": sender_name,
    }
    if not postal_address:
        warnings.append("config.toml has no sender.postal_address; a real send needs it")
    if not reply_to:
        warnings.append("config.toml has no sender.reply_to; unsubscribe by reply cannot work")
    environment = Environment(
        loader=FileSystemLoader(TEMPLATE_DIRECTORY),
        autoescape=select_autoescape(["html"]),
        undefined=StrictUndefined,
    )
    html_out = environment.get_template("base.html.j2").render(**context)
    text_out = environment.get_template("base.txt.j2").render(**context)
    return RenderedEmail(
        subject=subject,
        html=html_out,
        text=text_out,
        images=tuple(inline_images),
        unsubscribe_mailto=unsubscribe_mailto,
        warnings=tuple(warnings),
    )
