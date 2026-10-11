"""Campaigns: a Markdown file with front matter becomes an email.

Renders the body to HTML and plain text with a template, places images, and resolves the
audience (lists and tags). A campaign file lives in the workspace, never in git; templates
and the first book-flavoured starting points (confirmation, update, early-access chapter,
launch) live in this package and read the book title from config.

Never imports: delivery, inbox, linkedin (enforced by the import contract "domain below
channels" in pyproject.toml).

Decisions:
- Markdown supports images, tables and the standard elements; raw HTML blocks are not
  allowed. (owner)
- Images ride inside the message as inline (CID) attachments, so no public hosting is needed.
  Each image is resized to email width (about 600 px, with a 2x copy), compressed to a size
  budget and converted to a format every client renders (JPEG for photos, PNG for line art;
  WebP and AVIF are converted), cached by content hash. (owner)
- The footer carries the owner's postal address from config and the unsubscribe-by-reply line. (owner)
- Open and click tracking default to off; `track_clicks: true` in the front matter turns clicks on. (owner)

- A campaign file lives in the workspace under `campaigns/<name>.md`; `campaigns new` copies a
  starter (confirmation, update, early-access, launch) there and never overwrites. Preview files
  are derived output under `previews/`. (owner)
- A campaign reaches `subscribed` members of its lists who carry every tag it names; a
  `confirmation` campaign reaches `imported` members instead. Do-not-mail contacts and contacts
  without an email are always skipped. (owner)
- The sum of a campaign's inline images stays within `[caps].max_image_kilobytes_per_campaign`
  (default 100 KB); a render that cannot meet it fails and names the image. (owner)

Terms:
- **campaign**: one Markdown file plus its front matter (subject, lists, tags, flags), sent once.
- **template**: the HTML and text frame a campaign body is placed in.
- **audience**: the subscribed members of the campaign's lists that carry its tags.
"""
