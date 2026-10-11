"""Campaigns: the letters you send, kept in the database and edited in the web app.

A campaign holds its subject, Markdown body, values (such as the book title), lists, tags, flags,
optional sender overrides, uploaded images and the frame template it is poured into. Rendering turns
it into HTML, plain text and inline images. The account-wide sender defaults (name, address,
reply-to, footer postal address) are `setting` rows; the editable frame templates are `template`
rows; starters are shipped Markdown files that new campaigns are created from.

Never imports: delivery, inbox, linkedin (enforced by the import contract "domain below
channels" in pyproject.toml).

Decisions:
- Campaigns, templates, settings and uploaded images live in the database, created and edited in
  the web app; the book title and every other value belong to the campaign (with defaults in
  Settings for the sender), never to `config.toml`. Why: the owner runs many campaigns and cannot
  edit a file for each. (owner)
- Markdown supports images, tables and the standard elements; raw HTML blocks are not rendered. (owner)
- Images ride inside the message as inline (CID) attachments, so no public hosting is needed. Each is
  resized to email width (about 600 px, with a 2x file), compressed to a size budget and converted to a
  format every client renders (JPEG for photos, PNG for line art; WebP is converted). (owner)
- The sum of a campaign's inline images stays within `[caps].max_image_kilobytes_per_campaign`
  (default 100 KB); a render that cannot meet it fails and names the image. (owner)
- The footer carries the postal address and the reply-to-unsubscribe line. Open and click tracking
  default to off per campaign. (owner)
- A campaign reaches `subscribed` members of its lists who carry every tag it names; a
  `confirmation` campaign reaches `imported` members instead. Do-not-mail contacts and contacts
  without an email are always skipped. (owner)
- A frame template must contain the place for the letter (`{{ body_html | safe }}` and
  `{{ body_text }}`); the built-in template can be edited and reset, not deleted. (owner)

Terms:
- **campaign**: one letter with its subject, body, values, lists, tags and flags, sent once.
- **value**: a named piece of text a campaign substitutes into its subject and body, such as `book_title`.
- **template**: the HTML and text frame a campaign body is placed in.
- **starter**: a shipped campaign text that a new campaign is created from.
- **audience**: the subscribed members of the campaign's lists that carry its tags.
"""
