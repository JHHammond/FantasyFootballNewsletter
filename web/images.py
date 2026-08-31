"""
What kind of file is this, really?

The upload endpoint used to decide by asking the uploader twice: it checked
that the declared content-type started with "image/", then stored the file
under an extension taken from the declared filename, with the declared
content-type attached. Every one of those three facts came from the caller.

That admitted `image/svg+xml`, which passes a "starts with image/" test and is
a document format that can carry script. The result was executable content
served from the project's own storage domain — the one hole beside a paper
renderer that otherwise sanitizes properly.

So: read the bytes. The magic numbers below are the file's own account of
itself, and the extension and content-type we store are both derived from what
was found rather than from anything the caller said.
"""

from __future__ import annotations

from typing import NamedTuple, Optional


class ImageType(NamedTuple):
    mime: str
    extension: str


#: Raster formats a browser renders in an <img> without any scripting model.
#: SVG is deliberately absent and should stay that way: it is a document
#: format, and supporting it means shipping an XML sanitizer to keep script,
#: foreignObject and external references out of files strangers uploaded.
JPEG = ImageType("image/jpeg", "jpg")
PNG = ImageType("image/png", "png")
GIF = ImageType("image/gif", "gif")
WEBP = ImageType("image/webp", "webp")

ALLOWED_TYPES = (JPEG, PNG, GIF, WEBP)

#: Enough bytes for every signature below, with room to spare.
SNIFF_BYTES = 32


def sniff(data: bytes) -> Optional[ImageType]:
    """The image type these bytes actually are, or None.

    None means "don't store this", not "assume JPEG".
    """
    if not data or len(data) < 12:
        return None

    head = data[:SNIFF_BYTES]

    if head.startswith(b"\xff\xd8\xff"):
        return JPEG
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return PNG
    if head.startswith(b"GIF87a") or head.startswith(b"GIF89a"):
        return GIF
    # RIFF container; bytes 4-8 are the file length, 8-12 name the format.
    if head.startswith(b"RIFF") and head[8:12] == b"WEBP":
        return WEBP

    return None


def describe_rejection(data: bytes) -> str:
    """A message for the uploader that says something useful.

    "That isn't an image" is unhelpful when the user is looking at a picture in
    their file manager. Naming the likely cause saves a support round trip.
    """
    head = (data or b"")[:SNIFF_BYTES].lstrip()
    lowered = head[:20].lower()
    if lowered.startswith(b"<svg") or lowered.startswith(b"<?xml"):
        return ("SVG files can't be used in a paper. Export it as a PNG "
                "and try again.")
    if lowered.startswith(b"%pdf"):
        return "That's a PDF. Screenshot the page you want and upload that."
    if head.startswith(b"\x00\x00\x00") and b"ftypheic" in (data or b"")[:64]:
        return ("That's an iPhone HEIC photo. Sharing it to yourself first "
                "converts it to a JPEG, which works.")
    return "That file isn't a JPEG, PNG, GIF or WebP."
