import type { ReactNode } from "react";

interface MarkdownTextProps {
  content: string;
}

function inlineContent(value: string, keyPrefix: string): ReactNode[] {
  const parts = value.split(/(\*\*[^*]+\*\*|`[^`]+`)/g).filter(Boolean);
  return parts.map((part, index) => {
    const key = `${keyPrefix}-${index}`;
    if (part.startsWith("**") && part.endsWith("**")) {
      return <strong key={key}>{part.slice(2, -2)}</strong>;
    }
    if (part.startsWith("`") && part.endsWith("`")) {
      return <code key={key}>{part.slice(1, -1)}</code>;
    }
    return part;
  });
}

function isBlockStart(line: string) {
  return /^(#{1,3})\s+/.test(line)
    || /^[-*]\s+/.test(line)
    || /^\d+\.\s+/.test(line)
    || /^>\s?/.test(line);
}

export function MarkdownText({ content }: MarkdownTextProps) {
  const lines = content.replace(/\r\n/g, "\n").split("\n");
  const blocks: ReactNode[] = [];
  let index = 0;

  while (index < lines.length) {
    const line = lines[index];
    if (!line.trim()) {
      index += 1;
      continue;
    }

    const heading = line.match(/^(#{1,3})\s+(.+)$/);
    if (heading) {
      const level = heading[1].length;
      const children = inlineContent(heading[2], `heading-${index}`);
      if (level === 1) blocks.push(<h1 key={`block-${index}`}>{children}</h1>);
      else if (level === 2) blocks.push(<h2 key={`block-${index}`}>{children}</h2>);
      else blocks.push(<h3 key={`block-${index}`}>{children}</h3>);
      index += 1;
      continue;
    }

    if (/^[-*]\s+/.test(line)) {
      const items: ReactNode[] = [];
      const start = index;
      while (index < lines.length && /^[-*]\s+/.test(lines[index])) {
        const value = lines[index].replace(/^[-*]\s+/, "");
        items.push(<li key={`item-${index}`}>{inlineContent(value, `item-${index}`)}</li>);
        index += 1;
      }
      blocks.push(<ul key={`block-${start}`}>{items}</ul>);
      continue;
    }

    if (/^\d+\.\s+/.test(line)) {
      const items: ReactNode[] = [];
      const start = index;
      while (index < lines.length && /^\d+\.\s+/.test(lines[index])) {
        const value = lines[index].replace(/^\d+\.\s+/, "");
        items.push(<li key={`item-${index}`}>{inlineContent(value, `item-${index}`)}</li>);
        index += 1;
      }
      blocks.push(<ol key={`block-${start}`}>{items}</ol>);
      continue;
    }

    if (/^>\s?/.test(line)) {
      const start = index;
      const quote: string[] = [];
      while (index < lines.length && /^>\s?/.test(lines[index])) {
        quote.push(lines[index].replace(/^>\s?/, ""));
        index += 1;
      }
      blocks.push(<blockquote key={`block-${start}`}>{inlineContent(quote.join("\n"), `quote-${start}`)}</blockquote>);
      continue;
    }

    const start = index;
    const paragraph: string[] = [];
    while (index < lines.length && lines[index].trim() && !isBlockStart(lines[index])) {
      paragraph.push(lines[index]);
      index += 1;
    }
    blocks.push(<p key={`block-${start}`}>{inlineContent(paragraph.join("\n"), `paragraph-${start}`)}</p>);
  }

  return <div className="markdown-text">{blocks}</div>;
}
