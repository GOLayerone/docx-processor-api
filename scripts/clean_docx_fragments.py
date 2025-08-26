#!/usr/bin/env python3
"""
Clean fragmented Jinja tags in a DOCX (Word) file by reconstructing contiguous tags
in all word/*.xml parts inside the DOCX archive.

By default, this script targets the classic Jinja delimiters {{ ... }}.
Optionally, you can enable [[ ... ]] normalization via --enable-square.

Usage:
  python3 scripts/clean_docx_fragments.py input.docx -o output.docx
  python3 scripts/clean_docx_fragments.py input.docx --inplace

Notes:
- The DOCX is a zip archive. We rewrite only word/*.xml entries, keeping others intact.
- This operation is safe for non-tag text; only tag fragments resembling Jinja braces are compacted.
"""

import argparse
import os
import re
import shutil
import sys
import tempfile
import zipfile
from typing import Tuple


def _normalize_jinja_curly(xml_text: str) -> Tuple[str, int]:
    """Normalize fragmented {{ ... }} tags within an XML text.

    Example: "{" <w:t>"{"</w:t> <w:t> name </w:t> <w:t>"}"</w:t> "}" -> "{{name}}"
    Returns (new_text, count_replacements)
    """
    pattern_curly = re.compile(r"\{(?:<[^>]+>|\s)*\{(.*?)(?:\}(?:<[^>]+>|\s)*\})", re.DOTALL)

    def repl_curly(m: re.Match) -> str:
        inner = m.group(1) or ""
        cleaned = re.sub(r"<[^>]+>", "", inner)
        cleaned = cleaned.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")
        cleaned = re.sub(r"\s+", "", cleaned)
        # Keep only a valid variable-like name, otherwise leave text as-is
        mname = re.match(r"^([A-Za-z_][A-Za-z0-9_-]*)$", cleaned)
        if not mname:
            return m.group(0)
        var = mname.group(1)
        return f"{{{{{var}}}}}"

    new_text, n = pattern_curly.subn(repl_curly, xml_text)
    return new_text, n


def _normalize_jinja_square(xml_text: str) -> Tuple[str, int]:
    """Optionally normalize fragmented [[ ... ]] tags. Disabled by default.
    Returns (new_text, count_replacements)
    """
    pattern_square = re.compile(r"\[(?:<[^>]+>|\s)*\[(.*?)(?:\](?:<[^>]+>|\s)*\])", re.DOTALL)

    def repl_square(m: re.Match) -> str:
        inner = m.group(1) or ""
        cleaned = re.sub(r"<[^>]+>", "", inner)
        cleaned = cleaned.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")
        cleaned = re.sub(r"\s+", "", cleaned)
        mname = re.match(r"^([A-Za-z_][A-Za-z0-9_-]*)$", cleaned)
        if not mname:
            return m.group(0)
        var = mname.group(1)
        return f"[[{var}]]"

    new_text, n = pattern_square.subn(repl_square, xml_text)
    return new_text, n


def normalize_docx(input_path: str, output_path: str, enable_square: bool = False) -> int:
    """Open a DOCX, normalize fragmented Jinja tags in word/*.xml, and write output.

    Returns the total number of tag reconstructions performed.
    """
    if not os.path.exists(input_path):
        raise FileNotFoundError(f"Input DOCX not found: {input_path}")

    total = 0
    with zipfile.ZipFile(input_path, 'r') as zin, zipfile.ZipFile(output_path, 'w', zipfile.ZIP_DEFLATED) as zout:
        for item in zin.infolist():
            data = zin.read(item.filename)
            if item.filename.startswith('word/') and item.filename.endswith('.xml'):
                try:
                    text = data.decode('utf-8', 'ignore')
                    text, n1 = _normalize_jinja_curly(text)
                    total += n1
                    if enable_square:
                        text, n2 = _normalize_jinja_square(text)
                        total += n2
                    data = text.encode('utf-8')
                except Exception:
                    # On failure, keep original content
                    pass
            zout.writestr(item, data)
    return total


def main():
    parser = argparse.ArgumentParser(description="Clean fragmented Jinja tags in a DOCX file.")
    parser.add_argument("input", help="Path to input .docx")
    parser.add_argument("-o", "--output", help="Path to output .docx. If omitted with --inplace, input is modified in place.")
    parser.add_argument("--inplace", action="store_true", help="Modify the input file in place (safe temp swap).")
    parser.add_argument("--enable-square", action="store_true", help="Also normalize [[ ... ]] tags.")
    args = parser.parse_args()

    if not args.output and not args.inplace:
        print("Error: specify --output or --inplace", file=sys.stderr)
        sys.exit(2)

    if args.inplace:
        # Write to temp file, then replace atomically
        with tempfile.TemporaryDirectory() as td:
            tmp_out = os.path.join(td, "normalized.docx")
            count = normalize_docx(args.input, tmp_out, enable_square=args.enable_square)
            shutil.move(tmp_out, args.input)
        print(f"Done. Reconstructed {count} tag(s). Wrote in-place: {args.input}")
    else:
        # Normal copy to specified output
        out = args.output
        os.makedirs(os.path.dirname(out) or '.', exist_ok=True)
        count = normalize_docx(args.input, out, enable_square=args.enable_square)
        print(f"Done. Reconstructed {count} tag(s). Output: {out}")


if __name__ == "__main__":
    main()
