import React from "react";
import {Text} from "ink";

export interface MarkdownRow {
  text: string;
  kind: "normal" | "heading" | "code" | "quote" | "rule" | "table";
  level?: number;
}

export const tokenizeMarkdown = (content: string): MarkdownRow[] => {
  const rows: MarkdownRow[] = [];
  let inCode = false;

  for (const line of content.replace(/\r\n/g, "\n").split("\n")) {
    if (line.trimStart().startsWith("```")) {
      inCode = !inCode;
      rows.push({text: "──────── code ────────", kind: "rule"});
      continue;
    }

    if (inCode) {
      rows.push({text: line, kind: "code"});
      continue;
    }

    const heading = line.match(/^(#{1,6})\s+(.*)$/);
    if (heading) {
      rows.push({text: heading[2], kind: "heading", level: heading[1].length});
    } else if (/^\s*(---+|\*\*\*+)\s*$/.test(line)) {
      rows.push({text: "────────────────────────────────────────", kind: "rule"});
    } else if (/^\s*>\s?/.test(line)) {
      rows.push({text: line.replace(/^\s*>\s?/, "│ "), kind: "quote"});
    } else if (/^\s*[-*+]\s+/.test(line)) {
      rows.push({text: line.replace(/^(\s*)[-*+]\s+/, "$1• "), kind: "normal"});
    } else if (/^\s*\d+[.)]\s+/.test(line)) {
      rows.push({text: line, kind: "normal"});
    } else if (/^\s*\|.*\|\s*$/.test(line)) {
      rows.push({text: line, kind: "table"});
    } else {
      rows.push({text: line, kind: "normal"});
    }
  }

  return rows;
};

const inlinePattern = /(`[^`]+`|\*\*[^*]+\*\*|__[^_]+__|\*[^*]+\*|_[^_]+_|\[[^\]]+\]\([^\)]+\))/g;

export const renderInlineMarkdown = (value: string): React.ReactNode[] => {
  const result: React.ReactNode[] = [];
  let lastIndex = 0;

  for (const match of value.matchAll(inlinePattern)) {
    const index = match.index ?? 0;
    if (index > lastIndex) result.push(value.slice(lastIndex, index));
    const token = match[0];

    if (token.startsWith("`") && token.endsWith("`")) {
      result.push(<Text key={`${index}-code`} color="cyan">{token.slice(1, -1)}</Text>);
    } else if (token.startsWith("**") || token.startsWith("__")) {
      result.push(<Text key={`${index}-strong`} bold>{token.slice(2, -2)}</Text>);
    } else if (token.startsWith("*") || token.startsWith("_")) {
      result.push(<Text key={`${index}-em`} italic>{token.slice(1, -1)}</Text>);
    } else {
      const link = token.match(/^\[([^\]]+)\]\(([^\)]+)\)$/);
      result.push(link ? <Text key={`${index}-link`} color="cyan">{link[1]} ({link[2]})</Text> : token);
    }

    lastIndex = index + token.length;
  }

  if (lastIndex < value.length) result.push(value.slice(lastIndex));
  return result;
};
