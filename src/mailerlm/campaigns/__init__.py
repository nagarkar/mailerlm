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
- The footer carries the postal address and the opt-out line (hidden for readers of a list that hides it, see
  `mailerlm.consent`). Open and click tracking
  default to off per campaign. (owner)
- A campaign reaches `subscribed` members of its lists who carry every tag it names; a
  `confirmation` campaign reaches `imported` members instead. Do-not-mail contacts and contacts
  without an email are always skipped. (owner)
- The owner picks the lists for each send (the web send panel, `sends plan|start|test --list L`, repeatable;
  default: the campaign's lists), and each reader's letter is rendered with the settings of the list they are
  reached through: its footer shown or hidden, its "Testing: " prefix, its sender override. In the owner's words:
  "I should be able to pick the mailing list that I want to send it to. It could be more than one mailing list ...
  And then a separate email should be sent to each mailing list or each recipient of each mailing list with the
  settings of that mailing list." The fingerprint covers the chosen lists in order, and each delivery row records
  the list its reader was reached through. (owner)
- A person who would be mailed through two or more of the lists chosen for a send stops the send (a problem in
  `campaigns check` and `sends plan`, so `can_send` is false) until the owner takes them off all but one of those
  lists or drops a list from the send; there is no override. "Would be mailed" means the status this letter mails
  (`subscribed`, or `imported` for a confirmation), not do-not-mail, carrying the tags. A person on two chosen lists
  who would be mailed through at most one of them (say pending on the other) is a note, not a problem; being on
  several lists of which at most one is chosen is fine and not reported. In the owner's words: "when I'm sending a
  mail to two such lists and a person's on both of them, that means something is wrong ... There should be a check
  to say, do we have any emails that are in more than one mailing list that has been selected for that campaign."
  So each person who is mailed is reached through exactly one chosen list. (owner)
- A frame template must contain the place for the letter (`{{ body_html | safe }}` and
  `{{ body_text }}`); the built-in template can be edited and reset, not deleted. (owner)

Terms:
- **campaign**: one letter with its subject, body, values, lists, tags and flags, sent once.
- **value**: a named piece of text a campaign substitutes into its subject and body, such as `book_title`.
- **template**: the HTML and text frame a campaign body is placed in.
- **starter**: a shipped campaign text that a new campaign is created from.
- **audience**: the subscribed members of the lists chosen for a send that carry the campaign's tags.
- **reached through**: the one chosen list a reader is mailed through; its settings shape their letter.
- **overlap**: a person who would be mailed through two or more of the lists chosen for one send; it stops the send.
"""
