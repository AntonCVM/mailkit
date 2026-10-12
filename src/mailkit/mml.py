"""Drafts are MML: headers, a blank line, a plain-text body, and parts.

The subset is the one himalaya writes and people actually use:

    <#part type=application/pdf filename=/abs/path/invoice.pdf><#/part>   attachment
    <#part filename="~/x.zip" name=renamed.zip><#/part>                    attachment, renamed
    <#part type=text/html>…<#/part>                                        HTML alternative
    <#!part …>  <#!/part>                                                  literal text, not a tag

A draft stays text until it is sent, so a person or an agent can edit it with
anything. `compile_draft` turns it into the MIME that goes out, and refuses
what would be embarrassing to send: an unfilled `{{PLACEHOLDER}}`, an
attachment that is not there, a relative attachment path (relative to what?).
"""

from __future__ import annotations

import mimetypes
import os
import re
from dataclasses import dataclass, field
from email.message import EmailMessage
from email.parser import HeaderParser
from email.policy import default as policy
from email.utils import formatdate, getaddresses

from .util import MailkitError

TAG = re.compile(r"<#(!?)(/?)(part|multipart)\b([^>]*)>")
ATTR = re.compile(r"""([a-zA-Z-]+)=("([^"]*)"|'([^']*)'|[^\s>]+)""")
PLACEHOLDER = re.compile(r"\{\{[^{}\n]{1,60}\}\}")
# Never copied from a draft into what is sent: set at send time, or envelope only.
DROP = {"date", "bcc", "content-type", "content-transfer-encoding", "mime-version"}


@dataclass
class Attachment:
    path: str
    mimetype: str
    name: str


@dataclass
class Draft:
    headers: list[tuple[str, str]]
    text: str
    html: str | None = None
    attachments: list[Attachment] = field(default_factory=list)

    def get(self, name: str) -> str | None:
        for k, v in self.headers:
            if k.lower() == name.lower():
                return v
        return None

    def get_all(self, name: str) -> list[str]:
        return [v for k, v in self.headers if k.lower() == name.lower()]


def split(raw: str) -> tuple[str, str]:
    """(header block, body). Accepts CRLF; a draft with no body is all headers."""
    raw = raw.replace("\r\n", "\n")
    head, sep, body = raw.partition("\n\n")
    return head, body if sep else ""


def parse_headers(head: str) -> list[tuple[str, str]]:
    msg = HeaderParser(policy=policy).parsestr(head + "\n\n")
    return [(k, str(v)) for k, v in msg.items()]


def _attrs(s: str) -> dict[str, str]:
    out = {}
    for m in ATTR.finditer(s):
        val = m.group(3) if m.group(3) is not None else m.group(4) if m.group(4) is not None else m.group(2)
        out[m.group(1).lower()] = val
    return out


def parse(raw: str) -> Draft:
    head, body = split(raw)
    draft = Draft(headers=parse_headers(head), text="")
    text: list[str] = []
    pos = 0
    while True:
        m = TAG.search(body, pos)
        if not m:
            text.append(body[pos:])
            break
        text.append(body[pos:m.start()])
        escaped, closing, kind, rest = m.groups()
        if escaped:
            # `<#!part …>` is the text `<#part …>`: one level of escape removed.
            text.append(f"<#{closing}{kind}{rest}>")
            pos = m.end()
            continue
        if closing:
            raise MailkitError(f"stray <#/{kind}> at offset {m.start()}")
        if kind == "multipart":
            raise MailkitError("<#multipart> is not supported: write parts one after another")
        end = body.find("<#/part>", m.end())
        if end < 0:
            raise MailkitError(f"<#part{rest}> is never closed with <#/part>")
        inner = body[m.end():end]
        attrs = _attrs(rest)
        if "filename" in attrs:
            path = os.path.expanduser(attrs["filename"])
            mimetype = attrs.get("type") or mimetypes.guess_type(path)[0] or "application/octet-stream"
            draft.attachments.append(Attachment(path, mimetype, attrs.get("name") or os.path.basename(path)))
        elif attrs.get("type", "text/plain") == "text/html":
            if draft.html is not None:
                raise MailkitError("more than one HTML part")
            draft.html = inner
        elif attrs.get("type", "text/plain") == "text/plain":
            text.append(inner)
        else:
            raise MailkitError(f"inline part of type {attrs.get('type')} needs a filename=")
        pos = end + len("<#/part>")
    draft.text = "".join(text).strip("\n") + "\n"
    return draft


def problems(raw: str, draft: Draft) -> list[str]:
    """Everything that makes a draft unsendable, all at once rather than one per try."""
    out = []
    for ph in sorted(set(PLACEHOLDER.findall(raw))):
        out.append(f"unfilled placeholder {ph}")
    for att in draft.attachments:
        if not os.path.isabs(att.path):
            out.append(f"attachment path must be absolute (or ~/…): {att.path}")
        elif not os.path.isfile(att.path):
            out.append(f"attachment not found: {att.path}")
    if not draft.get("from"):
        out.append("no From:")
    if not recipients(draft):
        out.append("no recipients (To:/Cc:/Bcc:)")
    if not draft.get("subject"):
        out.append("no Subject:")
    return out


def recipients(draft: Draft) -> list[str]:
    fields = draft.get_all("to") + draft.get_all("cc") + draft.get_all("bcc")
    return [addr for _, addr in getaddresses(fields) if addr]


def compile_draft(raw: str) -> tuple[EmailMessage, list[str]]:
    """MML text → (message to send, envelope recipients). Raises on any problem."""
    draft = parse(raw)
    bad = problems(raw, draft)
    if bad:
        raise MailkitError("draft not sendable:\n  " + "\n  ".join(bad))
    msg = EmailMessage(policy=policy)
    for k, v in draft.headers:
        if k.lower() not in DROP and not k.lower().startswith("x-mailkit-"):
            msg[k] = v
    msg["Date"] = formatdate(localtime=True)
    msg.set_content(draft.text)
    if draft.html is not None:
        msg.add_alternative(draft.html, subtype="html")
    for att in draft.attachments:
        maintype, _, subtype = att.mimetype.partition("/")
        with open(att.path, "rb") as f:
            data = f.read()
        if att.mimetype == "message/rfc822":
            # A forwarded message travels as itself, not as an opaque blob.
            from email import message_from_bytes

            inner = message_from_bytes(data, policy=policy)
            msg.add_attachment(inner, filename=att.name)
        elif maintype == "text":
            msg.add_attachment(data.decode("utf-8", errors="replace"), subtype=subtype or "plain",
                               filename=att.name)
        else:
            msg.add_attachment(data, maintype=maintype, subtype=subtype or "octet-stream",
                               filename=att.name)
    return msg, recipients(draft)


def set_header(raw: str, name: str, value: str, replace: bool = True) -> str:
    """Edit one header in the raw text without re-serialising the body (which
    holds MML tags a MIME round-trip would mangle)."""
    head, body = split(raw)
    lines = head.split("\n") if head else []
    out, i, found = [], 0, False
    while i < len(lines):
        line = lines[i]
        j = i + 1
        while j < len(lines) and lines[j][:1] in (" ", "\t"):
            j += 1
        if line.split(":", 1)[0].strip().lower() == name.lower():
            found = True
            if replace:
                out.append(f"{name}: {value}")
            else:
                out.extend(lines[i:j])
        else:
            out.extend(lines[i:j])
        i = j
    if not found:
        out.append(f"{name}: {value}")
    return "\n".join(out) + "\n\n" + body
