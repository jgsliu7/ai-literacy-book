#!/usr/bin/env python3
from __future__ import annotations

import html
import mimetypes
import re
import shutil
import uuid
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BOOK_HTML = ROOT / "ai_history_book" / "ai_history_v3.html"
OUT_DIR = ROOT / "dist" / "epub_build"
EPUB_PATH = ROOT / "dist" / "ai-brief-history.epub"

TITLE = "AI 简史：从算盘到智能时代"
SUBTITLE = "写给普通人的人工智能通识读本"
AUTHOR = "刘7"
LANGUAGE = "zh-CN"
IDENTIFIER = "urn:uuid:" + str(uuid.uuid5(uuid.NAMESPACE_URL, "https://jgsliu7.github.io/ai-literacy-book/"))


@dataclass
class Section:
    sid: str
    title: str
    filename: str
    html_fragment: str


class FragmentSerializer(HTMLParser):
    void_tags = {"br", "hr", "img"}
    allowed_tags = {
        "a",
        "blockquote",
        "br",
        "div",
        "em",
        "figcaption",
        "figure",
        "h1",
        "h2",
        "h3",
        "hr",
        "i",
        "img",
        "li",
        "ol",
        "p",
        "span",
        "strong",
        "ul",
    }
    allowed_attrs = {"alt", "class", "href", "id", "src", "title"}

    def __init__(self, section_dir: str = "Text"):
        super().__init__(convert_charrefs=False)
        self.parts: list[str] = []
        self.stack: list[str] = []
        self.section_dir = section_dir

    def handle_starttag(self, tag: str, attrs):
        tag = tag.lower()
        if tag not in self.allowed_tags:
            return
        attrs_out: list[tuple[str, str]] = []
        for name, value in attrs:
            name = name.lower()
            value = "" if value is None else value
            if name not in self.allowed_attrs:
                continue
            if name == "src" and value.startswith("assets/"):
                value = "../" + value
            attrs_out.append((name, value))
        attr_text = "".join(
            f' {name}="{html.escape(value, quote=True)}"' for name, value in attrs_out
        )
        if tag in self.void_tags:
            self.parts.append(f"<{tag}{attr_text} />")
        else:
            self.parts.append(f"<{tag}{attr_text}>")
            self.stack.append(tag)

    def handle_endtag(self, tag: str):
        tag = tag.lower()
        if tag not in self.allowed_tags or tag in self.void_tags:
            return
        if tag in self.stack:
            while self.stack:
                open_tag = self.stack.pop()
                self.parts.append(f"</{open_tag}>")
                if open_tag == tag:
                    break

    def handle_data(self, data: str):
        self.parts.append(html.escape(data, quote=False))

    def handle_entityref(self, name: str):
        self.parts.append(f"&{name};")

    def handle_charref(self, name: str):
        self.parts.append(f"&#{name};")

    def close_fragment(self) -> str:
        while self.stack:
            self.parts.append(f"</{self.stack.pop()}>")
        return "".join(self.parts)


def strip_tags(value: str) -> str:
    value = re.sub(r"<[^>]+>", "", value)
    return html.unescape(re.sub(r"\s+", " ", value)).strip()


def serialize_fragment(fragment: str) -> str:
    serializer = FragmentSerializer()
    serializer.feed(fragment)
    return serializer.close_fragment()


def page(title: str, body: str, extra_head: str = "") -> str:
    return f"""<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" xml:lang="{LANGUAGE}" lang="{LANGUAGE}">
<head>
  <meta charset="utf-8" />
  <title>{html.escape(title)}</title>
  <link rel="stylesheet" type="text/css" href="../Styles/book.css" />
  {extra_head}
</head>
<body>
{body}
</body>
</html>
"""


def extract_sections(source: str) -> list[Section]:
    sections: list[Section] = []

    preface_start = source.find('<div class="preface" id="preface">')
    part1_start = source.find("<!-- ========== PART 1 ========== -->")
    if preface_start != -1 and part1_start != -1:
        preface_open_end = source.find(">", preface_start) + 1
        fragment = source[preface_open_end:part1_start]
        fragment = re.sub(r'<h1([^>]*)>', r'<h1 id="preface"\1>', fragment, count=1)
        sections.append(Section("preface", "前言", "preface.xhtml", serialize_fragment(fragment)))

    events = []
    body_end = source.rfind("</body>")
    scan_start = part1_start if part1_start != -1 else 0
    for match in re.finditer(r'<h1[^>]*id="([^"]+)"[^>]*>.*?</h1>', source[scan_start:], flags=re.S):
        absolute_start = scan_start + match.start()
        absolute_end = scan_start + match.end()
        sid = match.group(1)
        events.append(
            {
                "kind": "h1",
                "sid": sid,
                "start": absolute_start,
                "end": absolute_end,
                "title": strip_tags(match.group(0)),
            }
        )

    for match in re.finditer(
        r'(<div class="part-divider" id="(part\d+)">.*?\n</div>)',
        source[scan_start:],
        flags=re.S,
    ):
        absolute_start = scan_start + match.start()
        absolute_end = scan_start + match.end()
        fragment = match.group(1)
        title = " ".join(
            value
            for value in [
                strip_tags(re.search(r'<div class="part-number">(.*?)</div>', fragment, flags=re.S).group(1)),
                strip_tags(re.search(r'<div class="part-title">(.*?)</div>', fragment, flags=re.S).group(1)),
            ]
            if value
        )
        events.append(
            {
                "kind": "part",
                "sid": match.group(2),
                "start": absolute_start,
                "end": absolute_end,
                "title": title,
                "fragment": fragment,
            }
        )

    events.sort(key=lambda item: item["start"])
    for idx, event in enumerate(events):
        sid = event["sid"]
        if event["kind"] == "part":
            fragment = event["fragment"]
        else:
            end = events[idx + 1]["start"] if idx + 1 < len(events) else body_end
            if end <= event["start"]:
                continue
            fragment = source[event["start"]:end]
        safe = sid.replace("_", "-")
        sections.append(Section(sid, event["title"], f"{safe}.xhtml", serialize_fragment(fragment)))
    return sections


def write_static_files(sections: list[Section], image_paths: list[str]):
    if OUT_DIR.exists():
        shutil.rmtree(OUT_DIR)
    (OUT_DIR / "META-INF").mkdir(parents=True)
    (OUT_DIR / "OEBPS" / "Text").mkdir(parents=True)
    (OUT_DIR / "OEBPS" / "Styles").mkdir(parents=True)
    (OUT_DIR / "OEBPS" / "assets").mkdir(parents=True)

    (OUT_DIR / "mimetype").write_text("application/epub+zip", encoding="utf-8")
    (OUT_DIR / "META-INF" / "container.xml").write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml" />
  </rootfiles>
</container>
""",
        encoding="utf-8",
    )

    css = """
body {
  font-family: "Noto Serif CJK SC", "Songti SC", serif;
  line-height: 1.75;
  color: #222;
}
h1 {
  font-size: 1.7em;
  margin: 1.6em 0 0.8em;
  border-bottom: 1px solid #999;
  padding-bottom: 0.35em;
}
h2 {
  font-size: 1.25em;
  margin: 1.4em 0 0.6em;
}
p {
  text-indent: 2em;
  margin: 0.6em 0;
}
.chapter-quote {
  text-indent: 0;
  color: #666;
  font-style: italic;
  border-left: 0.25em solid #aaa;
  padding-left: 0.8em;
}
.part-divider,
.learning-box,
.source-note {
  border: 1px solid #ccc;
  padding: 0.8em;
  margin: 1em 0;
  background: #f7f7f7;
}
.part-divider {
  text-align: center;
  padding: 3em 1em;
  margin: 2em 0;
  page-break-before: always;
  page-break-after: always;
  break-before: page;
  break-after: page;
  min-height: 80vh;
  display: flex;
  flex-direction: column;
  justify-content: center;
}
.part-number {
  color: #666;
  font-size: 1.05em;
  letter-spacing: 0.25em;
  margin-bottom: 0.8em;
}
.part-title {
  color: #222;
  font-size: 2em;
  font-weight: 700;
  line-height: 1.25;
  margin: 0.3em 0 0.6em;
}
.part-line {
  width: 8em;
  height: 1px;
  background: #777;
  margin: 1em auto;
}
.part-desc {
  color: #666;
  font-size: 1em;
  line-height: 1.8;
  margin: 1em auto 0;
  max-width: 32em;
}
figure {
  margin: 1.2em 0;
  text-align: center;
}
img {
  max-width: 100%;
  height: auto;
}
figcaption {
  color: #666;
  font-size: 0.9em;
  margin-top: 0.5em;
}
.cover-page {
  margin: 0;
  padding: 0;
  text-align: center;
}
.cover-page img {
  width: 100%;
  max-height: 100vh;
  object-fit: contain;
}
.references p {
  text-indent: 0;
}
"""
    (OUT_DIR / "OEBPS" / "Styles" / "book.css").write_text(css, encoding="utf-8")

    cover_body = '<div class="cover-page"><img src="../assets/images/cover.png" alt="封面" /></div>'
    (OUT_DIR / "OEBPS" / "Text" / "cover.xhtml").write_text(
        page(TITLE, cover_body),
        encoding="utf-8",
    )

    intro_body = f"""
<section>
  <h1>{html.escape(TITLE)}</h1>
  <p style="text-indent:0;">{html.escape(SUBTITLE)}</p>
  <p style="text-indent:0;">作者：{html.escape(AUTHOR)}</p>
</section>
"""
    (OUT_DIR / "OEBPS" / "Text" / "title.xhtml").write_text(
        page(TITLE, intro_body),
        encoding="utf-8",
    )

    for section in sections:
        (OUT_DIR / "OEBPS" / "Text" / section.filename).write_text(
            page(section.title, f"<section>\n{section.html_fragment}\n</section>"),
            encoding="utf-8",
        )

    for rel in image_paths:
        src = ROOT / "ai_history_book" / rel
        if not src.exists() or src.name == ".DS_Store":
            continue
        dest = OUT_DIR / "OEBPS" / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)


def media_type(path: str) -> str:
    if path.endswith(".svg"):
        return "image/svg+xml"
    if path.endswith(".xhtml"):
        return "application/xhtml+xml"
    if path.endswith(".css"):
        return "text/css"
    return mimetypes.guess_type(path)[0] or "application/octet-stream"


def write_nav_and_opf(sections: list[Section], image_paths: list[str]):
    nav_items = ['<li><a href="Text/cover.xhtml">封面</a></li>', '<li><a href="Text/title.xhtml">书名页</a></li>']
    for section in sections:
        nav_items.append(
            f'<li><a href="Text/{section.filename}">{html.escape(section.title)}</a></li>'
        )
    nav_body = f"""
<nav epub:type="toc" id="toc">
  <h1>目录</h1>
  <ol>
    {"".join(nav_items)}
  </ol>
</nav>
"""
    (OUT_DIR / "OEBPS" / "nav.xhtml").write_text(
        page("目录", nav_body, '  <meta name="viewport" content="width=device-width, initial-scale=1.0" />'),
        encoding="utf-8",
    )

    manifest = [
        '<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav" />',
        '<item id="style" href="Styles/book.css" media-type="text/css" />',
        '<item id="cover-page" href="Text/cover.xhtml" media-type="application/xhtml+xml" />',
        '<item id="title-page" href="Text/title.xhtml" media-type="application/xhtml+xml" />',
    ]
    spine = ['<itemref idref="cover-page" linear="yes" />', '<itemref idref="title-page" />']
    for index, section in enumerate(sections, 1):
        item_id = f"section-{index}"
        manifest.append(
            f'<item id="{item_id}" href="Text/{section.filename}" media-type="application/xhtml+xml" />'
        )
        spine.append(f'<itemref idref="{item_id}" />')

    cover_image_id = ""
    for index, rel in enumerate(image_paths, 1):
        if rel == "assets/images/cover.png":
            cover_image_id = f"image-{index}"
        props = ' properties="cover-image"' if rel == "assets/images/cover.png" else ""
        manifest.append(
            f'<item id="image-{index}" href="{rel}" media-type="{media_type(rel)}"{props} />'
        )

    modified = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    opf = f"""<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="bookid">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="bookid">{IDENTIFIER}</dc:identifier>
    <dc:title>{html.escape(TITLE)}</dc:title>
    <dc:creator>{html.escape(AUTHOR)}</dc:creator>
    <dc:language>{LANGUAGE}</dc:language>
    <dc:description>{html.escape(SUBTITLE)}</dc:description>
    <dc:date>{datetime.now().date().isoformat()}</dc:date>
    <meta property="dcterms:modified">{modified}</meta>
    <meta name="cover" content="{cover_image_id}" />
  </metadata>
  <manifest>
    {"".join(manifest)}
  </manifest>
  <spine>
    {"".join(spine)}
  </spine>
</package>
"""
    (OUT_DIR / "OEBPS" / "content.opf").write_text(opf, encoding="utf-8")


def package_epub():
    EPUB_PATH.parent.mkdir(parents=True, exist_ok=True)
    if EPUB_PATH.exists():
        EPUB_PATH.unlink()
    with zipfile.ZipFile(EPUB_PATH, "w") as zf:
        zf.write(OUT_DIR / "mimetype", "mimetype", compress_type=zipfile.ZIP_STORED)
        for path in sorted(OUT_DIR.rglob("*")):
            if path.is_dir() or path.name == "mimetype":
                continue
            zf.write(path, path.relative_to(OUT_DIR).as_posix(), compress_type=zipfile.ZIP_DEFLATED)


def main():
    source = BOOK_HTML.read_text(encoding="utf-8")
    sections = extract_sections(source)
    image_paths = sorted(set(re.findall(r'src="(assets/[^"]+)"', source)))
    image_paths = [p for p in image_paths if not p.endswith(".DS_Store")]
    write_static_files(sections, image_paths)
    write_nav_and_opf(sections, image_paths)
    package_epub()
    print(f"Wrote {EPUB_PATH}")
    print(f"Sections: {len(sections)}")
    print(f"Images: {len(image_paths)}")


if __name__ == "__main__":
    main()
