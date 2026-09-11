"""
Extracts the page images from a scanned gazette PDF so its schedules can be read.

`docs/KAC NEW GAzETTE (4).pdf` has no text layer — `pypdf` returns zero
characters from all 144 pages — so no amount of text parsing will get a rate
schedule out of it. What it does have is one full-page JPEG per page at 200 DPI,
stored with the `DCTDecode` filter, which means the embedded bytes are already a
valid JPEG file. This command writes those bytes straight out: no decoding, no
re-encoding, no imaging library, and no quality loss to OCR against.

    python manage.py extract_gazette_pages --pdf "docs/KAC NEW GAzETTE (4).pdf" --out build/gazette

Pages that are not a single DCTDecode image are reported and skipped rather than
silently dropped, so a mixed-format scan doesn't quietly lose pages.

Finding a page: the PDF index and the gazette's printed "B" number do not track
each other. In this scan the offset drifts from +166 at the front to +177 at the
back, so about ten printed pages are missing from it. Estimate with
`--locate B292`, then confirm against the page's own printed header — the
estimate is a starting point, not an answer.
"""

from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

# Anchors measured by reading the printed "B" number off these PDF pages.
# Used only to estimate where a given B-page sits; always confirm by eye.
_ANCHORS = [(1, 167), (3, 169), (50, 219), (57, 226), (88, 261), (98, 272),
            (101, 275), (108, 282), (117, 292), (121, 297), (130, 306), (140, 317)]


class Command(BaseCommand):
    help = "Extract page images from a scanned (image-only) gazette PDF for reading/OCR."

    def add_arguments(self, parser):
        parser.add_argument("--pdf", default="docs/KAC NEW GAzETTE (4).pdf", help="Source PDF.")
        parser.add_argument("--out", default="build/gazette_pages", help="Output directory.")
        parser.add_argument("--first", type=int, default=None, help="First page index (0-based, inclusive).")
        parser.add_argument("--last", type=int, default=None, help="Last page index (0-based, inclusive).")
        parser.add_argument(
            "--locate",
            default=None,
            metavar="Bnnn",
            help="Estimate which PDF index holds a printed gazette page, e.g. --locate B292. Prints and exits.",
        )

    def handle(self, *args, **options):
        if options["locate"]:
            self._locate(options["locate"])
            return

        try:
            from pypdf import PdfReader
        except ImportError as exc:  # pragma: no cover - dependency is in requirements
            raise CommandError("pypdf is required: pip install pypdf") from exc

        pdf_path = Path(options["pdf"])
        if not pdf_path.exists():
            raise CommandError(f"No such PDF: {pdf_path}")

        out_dir = Path(options["out"])
        out_dir.mkdir(parents=True, exist_ok=True)

        reader = PdfReader(str(pdf_path))
        first = options["first"] if options["first"] is not None else 0
        last = options["last"] if options["last"] is not None else len(reader.pages) - 1

        written, skipped = 0, []
        for index in range(first, min(last, len(reader.pages) - 1) + 1):
            page = reader.pages[index]
            xobjects = (page.get("/Resources") or {}).get("/XObject")
            if xobjects is None:
                skipped.append(index)
                continue

            page_written = False
            for name, ref in xobjects.get_object().items():
                obj = ref.get_object()
                if obj.get("/Subtype") != "/Image":
                    continue
                if "/DCTDecode" not in (obj.get("/Filter") or []):
                    continue
                # `_data` is the raw embedded stream. For DCTDecode that stream
                # *is* the JPEG file, so writing it verbatim avoids a decode /
                # re-encode round trip and keeps the scan pristine for OCR.
                (out_dir / f"page_{index:03d}.jpg").write_bytes(obj._data)
                written += 1
                page_written = True
                break
            if not page_written:
                skipped.append(index)

        self.stdout.write(self.style.SUCCESS(f"Wrote {written} page image(s) to {out_dir}"))
        if skipped:
            self.stdout.write(
                self.style.WARNING(
                    f"{len(skipped)} page(s) had no single DCTDecode image and were skipped: "
                    + ", ".join(str(i) for i in skipped[:20])
                    + ("..." if len(skipped) > 20 else "")
                )
            )

    def _locate(self, token):
        raw = token.upper().removeprefix("B").strip()
        if not raw.isdigit():
            raise CommandError(f"Expected a gazette page like 'B292', got {token!r}")
        target = int(raw)

        # Interpolate between the two nearest measured anchors.
        below = max((a for a in _ANCHORS if a[1] <= target), default=_ANCHORS[0])
        above = min((a for a in _ANCHORS if a[1] >= target), default=_ANCHORS[-1])
        if below[1] == above[1]:
            estimate = below[0]
        else:
            span = (target - below[1]) / (above[1] - below[1])
            estimate = round(below[0] + span * (above[0] - below[0]))

        self.stdout.write(
            f"B{target} is near PDF index {estimate} (file page_{estimate:03d}.jpg).\n"
            f"  bracketed by measured anchors: index {below[0]}=B{below[1]} and index {above[0]}=B{above[1]}\n"
            "  The offset drifts across this scan — confirm against the page's printed header."
        )
