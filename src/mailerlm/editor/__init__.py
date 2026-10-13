"""Editor: change an email template by asking for it in words, with a draft, a saved version and a thread.

A template has a saved version (what campaigns use) and at most one draft (what the owner and the model are
changing). A draft exists only while it differs from the saved version. The conversation is stored against the
template. The model's reply goes through the template guard and then into the draft; only the owner's Save
(`templates_publish`) turns a draft into the saved version. Undo and redo are the browser's, not stored.

Never imports: delivery, inbox, linkedin (enforced by the import contract "channels apart" and "domain below
channels" in pyproject.toml).

Decisions:
- The model has no tools and can only return text; that text reaches the draft only after it is one JSON object
  with exactly html, text and summary, and the template guard accepts it. A rejected or failed reply changes
  nothing and is shown in the thread with its reason. (owner)
- Only the owner's Save changes the saved template; the model and the owner's typing change the draft. (owner)
- The template, the thread, pasted notes and every earlier reply are data: wrapped in tags, named as data in the
  prompt, never obeyed. Each call sends the system prompt, every earlier request and reply summary in order, and
  the template as it is now. (owner)
- The model is named in `[template_editor]` in config.toml (absent section = the editor is off), chosen by the
  metalm model gate (tools/template_edit_gate.py). Spend, request length and turns are capped there; the caps
  raise and never clamp. (owner)
- One edit of a template runs at a time. The model call holds no database lock.

Terms:
- **draft**: the one unsaved version of a template; absent when it equals the saved version.
- **thread**: the stored conversation between the owner and the model about one template.
"""
