"""The one error type the command line turns into `error: <what>. <fix>` (metalm cli.md)."""


class MailerlmError(RuntimeError):
    """Something the owner can fix; the message names the exact fix command or setting."""


class NotFoundError(MailerlmError):
    """A named thing (campaign, list, contact, template) does not exist; the web page answers 404."""
