// ---------------------------------------------------------------- markdown, hand-rolled
//
// Agent prose IS markdown, and `## Heading` / `**bold**` read worse raw on a phone than they do
// formatted. WHAT to support is measured rather than guessed: across the 2,572 assistant text
// blocks (838KB) in the 25 largest transcripts on this machine, inline code appears in 43.8% of
// them, bold in 32.6%, bullets 11.2%, headings 10.3%, GFM tables 8.7% -- more often than fenced
// code at 5.2% -- ordered lists 5.7%, rules 2.5%, italics 2.4%, quotes 1.4%, links 1.3%.
// Strikethrough appears in 0.1% and is not supported.
//
// NODES, NEVER HTML STRINGS. Every character of transcript text lands in a `textContent`, which is
// the same boundary ansiFragment holds: nothing a transcript contains can become markup, and that
// is provable by construction rather than by remembering to escape. It is also why this is
// hand-rolled -- the page is one file with no build step, behind a CSP that blocks every CDN.
//
// TWO DELIBERATE OMISSIONS, because this content is code:
//  - `_underscore_` emphasis, which would mangle snake_case_identifiers. Claude writes emphasis
//    with asterisks anyway.
//  - `*emphasis*` requires a non-space character just inside both delimiters, so `rename *.ts to
//    *.tsx` is not swallowed into one italic run.
// BLOCKS ARE FLAT: a table or a list inside a quote or a list item reads as the outer block's
// text. Agents put both at the top level, which is where the collapse would have hurt.

// A link is the one place this view could hand a URL to the browser, so only these become one.
const MD_HREF_OK = /^(https?:|mailto:)/i;
// Code first, so ``**`x`**`` finds the bold and recurses into the code rather than the reverse.
const MD_INLINE = /(`+)([\s\S]*?)\1|\*\*(\S[\s\S]*?\S|\S)\*\*|\*(\S[\s\S]*?\S|\S)\*|\[([^\]\n]+)\]\(([^)\s]+)\)/g;
const MD_MAX_DEPTH = 6;

function mdInline(parent, text, depth = 0) {
  if (depth >= MD_MAX_DEPTH) { parent.appendChild(document.createTextNode(text)); return; }
  let cursor = 0;
  for (const m of String(text).matchAll(MD_INLINE)) {
    if (m.index > cursor) parent.appendChild(document.createTextNode(text.slice(cursor, m.index)));
    cursor = m.index + m[0].length;
    if (m[2] !== undefined) {
      // Verbatim by definition: never re-parsed, and the surrounding spaces GFM allows are trimmed.
      const code = document.createElement('code');
      code.textContent = m[2].replace(/^ (.*) $/s, '$1');
      parent.appendChild(code);
    } else if (m[3] !== undefined) {
      const strong = document.createElement('strong');
      mdInline(strong, m[3], depth + 1);
      parent.appendChild(strong);
    } else if (m[4] !== undefined) {
      const em = document.createElement('em');
      mdInline(em, m[4], depth + 1);
      parent.appendChild(em);
    } else {
      const label = m[5], href = m[6];
      if (MD_HREF_OK.test(href)) {
        const a = document.createElement('a');
        a.href = href; a.target = '_blank'; a.rel = 'noopener noreferrer';
        mdInline(a, label, depth + 1);
        parent.appendChild(a);
      } else {
        // Not a link -- and not silently dropped either: the reader still sees what was written.
        parent.appendChild(document.createTextNode(m[0]));
      }
    }
  }
  if (cursor < text.length) parent.appendChild(document.createTextNode(text.slice(cursor)));
}

const MD_FENCE = /^\s*(```+|~~~+)\s*([\w.+#-]*)\s*$/;
const MD_HEADING = /^(#{1,6})\s+(.*)$/;
const MD_RULE = /^\s*(?:-{3,}|\*{3,}|_{3,})\s*$/;
const MD_BULLET = /^\s*[-*+]\s+(.*)$/;
const MD_ORDERED = /^\s*\d+[.)]\s+(.*)$/;
const MD_QUOTE = /^\s*>\s?(.*)$/;
const MD_TABLE_RULE = /^\s*\|?\s*:?-{1,}:?\s*(\|\s*:?-{1,}:?\s*)+\|?\s*$/;

function mdCells(line) {
  const trimmed = line.trim().replace(/^\|/, '').replace(/\|$/, '');
  return trimmed.split('|').map(cell => cell.trim());
}

function mdAlign(line) {
  return mdCells(line).map(spec => {
    const left = spec.startsWith(':'), right = spec.endsWith(':');
    return right && left ? 'center' : right ? 'right' : left ? 'left' : '';
  });
}

function mdFragment(text) {
  const out = document.createDocumentFragment();
  const lines = String(text ?? '').split('\n');
  let i = 0;
  const para = [];
  const flush = () => {
    if (!para.length) return;
    const p = document.createElement('p');
    mdInline(p, para.join('\n'));
    out.appendChild(p);
    para.length = 0;
  };
  while (i < lines.length) {
    const line = lines[i];
    const fence = line.match(MD_FENCE);
    if (fence) {
      flush();
      const closer = fence[1][0];  // ``` is not closed by ~~~
      const body = [];
      i += 1;
      while (i < lines.length) {
        const end = lines[i].match(MD_FENCE);
        if (end && end[1].startsWith(closer)) break;
        body.push(lines[i]); i += 1;
      }
      i += 1;  // past the closing fence, or past the end of an unclosed block
      const pre = document.createElement('pre');
      const code = document.createElement('code');
      if (fence[2]) code.dataset.lang = fence[2];
      code.textContent = body.join('\n');
      pre.appendChild(code);
      out.appendChild(pre);
      continue;
    }
    if (!line.trim()) { flush(); i += 1; continue; }
    const heading = line.match(MD_HEADING);
    if (heading) {
      flush();
      // Levels are relative to the panel, not the document: an agent's `#` is a section of one
      // message, so h1 would outrank the panel's own title.
      const h = document.createElement('div');
      h.className = `md-h md-h${heading[1].length}`;
      mdInline(h, heading[2]);
      out.appendChild(h);
      i += 1; continue;
    }
    if (MD_RULE.test(line)) { flush(); out.appendChild(document.createElement('hr')); i += 1; continue; }
    // A table is its header row plus a delimiter row; without the delimiter it is just prose.
    if (line.includes('|') && i + 1 < lines.length && MD_TABLE_RULE.test(lines[i + 1])) {
      flush();
      const align = mdAlign(lines[i + 1]);
      const table = document.createElement('table');
      const head = document.createElement('thead');
      const headRow = document.createElement('tr');
      mdCells(line).forEach((cell, index) => {
        const th = document.createElement('th');
        if (align[index]) th.style.textAlign = align[index];
        mdInline(th, cell);
        headRow.appendChild(th);
      });
      head.appendChild(headRow); table.appendChild(head);
      const body = document.createElement('tbody');
      i += 2;
      while (i < lines.length && lines[i].includes('|') && lines[i].trim()) {
        const tr = document.createElement('tr');
        mdCells(lines[i]).forEach((cell, index) => {
          const td = document.createElement('td');
          if (align[index]) td.style.textAlign = align[index];
          mdInline(td, cell);
          tr.appendChild(td);
        });
        body.appendChild(tr); i += 1;
      }
      table.appendChild(body);
      const wrap = document.createElement('div');
      wrap.className = 'md-table';
      wrap.appendChild(table);
      out.appendChild(wrap);
      continue;
    }
    const bullet = line.match(MD_BULLET), ordered = !bullet && line.match(MD_ORDERED);
    if (bullet || ordered) {
      flush();
      const list = document.createElement(bullet ? 'ul' : 'ol');
      const rowRe = bullet ? MD_BULLET : MD_ORDERED;
      while (i < lines.length) {
        const item = lines[i].match(rowRe);
        if (!item) break;
        const li = document.createElement('li');
        mdInline(li, item[1]);
        list.appendChild(li); i += 1;
      }
      out.appendChild(list);
      continue;
    }
    const quote = line.match(MD_QUOTE);
    if (quote) {
      flush();
      const block = document.createElement('blockquote');
      const body = [];
      while (i < lines.length) {
        const row = lines[i].match(MD_QUOTE);
        if (!row) break;
        body.push(row[1]); i += 1;
      }
      mdInline(block, body.join('\n'));
      out.appendChild(block);
      continue;
    }
    para.push(line); i += 1;
  }
  flush();
  return out;
}

