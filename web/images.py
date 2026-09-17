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


# ---------------------------------------------------------------------------
# How big is it?
#
# The classifieds page lays ads out at their own shape — nothing is cropped,
# because a cropped meme is a dead meme. To do that without the page jumping
# around as images load, the server has to know each image's real dimensions
# at upload time and write them into the markup.
#
# Read out of the file's own header rather than by decoding it. Pillow would
# do this in one line, and it is a C extension that has to build on the host,
# for a job that is forty lines of struct-reading. The bytes below are the
# same bytes sniff() is already looking at.
#
# Every one of these returns None rather than guessing. A guessed aspect ratio
# is worse than no aspect ratio: the page would silently letterbox.
# ---------------------------------------------------------------------------

#: A JPEG frame header. SOF0 through SOF15 all carry the dimensions in the
#: same place, but C4 (Huffman tables), C8 (reserved) and CC (arithmetic
#: coding conditioning) are not frame headers at all and must be skipped or
#: the numbers come out of the middle of a Huffman table.
_JPEG_FRAME_MARKERS = {
    0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
    0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF,
}


def _u16be(data: bytes, at: int) -> int:
    return (data[at] << 8) | data[at + 1]


def _u16le(data: bytes, at: int) -> int:
    return data[at] | (data[at + 1] << 8)


def _jpeg_size(data: bytes):
    """Walk the segment chain to the frame header.

    A JPEG is a sequence of length-prefixed segments, and the dimensions live
    in the frame header, which can be anywhere — after an EXIF block holding a
    whole thumbnail, commonly. There is no shortcut; you follow the chain.
    """
    at = 2  # past the SOI
    end = len(data)
    while at + 9 < end:
        if data[at] != 0xFF:
            # Fill bytes are legal between segments; anything else means the
            # chain is broken and nothing further can be trusted.
            at += 1
            continue
        marker = data[at + 1]
        if marker == 0xFF:
            at += 1
            continue
        if marker in _JPEG_FRAME_MARKERS:
            return _u16be(data, at + 7), _u16be(data, at + 5)  # w, h
        length = _u16be(data, at + 2)
        if length < 2:
            return None
        at += 2 + length
    return None


def _webp_size(data: bytes):
    """Three different formats behind one four-letter name."""
    chunk = data[12:16]

    if chunk == b"VP8 " and len(data) >= 30:
        # Lossy. The key frame starts at 20; 23-26 is the start code.
        if data[23:26] != b"\x9d\x01\x2a":
            return None
        return _u16le(data, 26) & 0x3FFF, _u16le(data, 28) & 0x3FFF

    if chunk == b"VP8L" and len(data) >= 25:
        # Lossless. 14 bits each, minus one, packed little-endian.
        if data[20] != 0x2F:
            return None
        bits = int.from_bytes(data[21:25], "little")
        return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1

    if chunk == b"VP8X" and len(data) >= 30:
        # Extended (animation, alpha). 24 bits each, minus one.
        width = int.from_bytes(data[24:27], "little") + 1
        height = int.from_bytes(data[27:30], "little") + 1
        return width, height

    return None


def dimensions(data: bytes):
    """(width, height) in pixels, or None if the header doesn't say.

    None is a real answer and callers must handle it — a truncated upload, an
    unusual encoder, a format variant not covered here. The page falls back to
    a sensible default shape rather than refusing the image.
    """
    kind = sniff(data)
    if kind is None:
        return None

    try:
        if kind is PNG:
            # IHDR is mandatory and always first: 8 bytes signature, then a
            # length and a type, then the two dimensions.
            if data[12:16] != b"IHDR":
                return None
            width = int.from_bytes(data[16:20], "big")
            height = int.from_bytes(data[20:24], "big")
        elif kind is GIF:
            width, height = _u16le(data, 6), _u16le(data, 8)
        elif kind is JPEG:
            found = _jpeg_size(data)
            if not found:
                return None
            width, height = found
        elif kind is WEBP:
            found = _webp_size(data)
            if not found:
                return None
            width, height = found
        else:
            return None
    except (IndexError, ValueError):
        return None

    # A zero dimension is not a small image, it is a corrupt header, and it
    # would divide by zero the moment anything computed an aspect ratio.
    if width <= 0 or height <= 0:
        return None
    return width, height
