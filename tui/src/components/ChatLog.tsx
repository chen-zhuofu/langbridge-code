import React from "react";
import { Box, Text } from "ink";

import type { ChatLine } from "../state.js";
import { ACCENT, GREEN, styleColor } from "../theme.js";

/** Approximate terminal column width of one character (CJK and fullwidth = 2). */
function charWidth(char: string): number {
  const code = char.codePointAt(0) ?? 0;
  if (
    (code >= 0x1100 && code <= 0x115f) ||
    (code >= 0x2e80 && code <= 0xa4cf) ||
    (code >= 0xac00 && code <= 0xd7a3) ||
    (code >= 0xf900 && code <= 0xfaff) ||
    (code >= 0xfe30 && code <= 0xfe4f) ||
    (code >= 0xff00 && code <= 0xff60) ||
    (code >= 0xffe0 && code <= 0xffe6) ||
    code >= 0x20000
  ) {
    return 2;
  }
  return 1;
}

/** Split one text segment into chunks that each fit `columns` terminal cells. */
function wrapSegment(segment: string, columns: number): string[] {
  if (segment === "") return [""];
  const chunks: string[] = [];
  let current = "";
  let used = 0;
  for (const char of segment) {
    const w = charWidth(char);
    if (used + w > columns && current !== "") {
      chunks.push(current);
      current = "";
      used = 0;
    }
    current += char;
    used += w;
  }
  if (current !== "") chunks.push(current);
  return chunks;
}

interface RenderRow {
  key: string;
  line: ChatLine;
  text: string;
  first: boolean;
}

function linePrefix(line: ChatLine): string {
  if (line.kind === "user") return "\u2726 ";
  if (line.kind === "assistant") return "\u25cf ";
  if (line.kind === "trace" && line.style) return `${line.style}: `;
  return "";
}

/** Explode one chat line into exactly the rows the terminal will show. */
function explodeLine(line: ChatLine, width: number): RenderRow[] {
  const prefix = linePrefix(line);
  const prefixCols = [...prefix].reduce((total, char) => total + charWidth(char), 0);
  const continuationIndent = prefix ? 2 : 0;
  const text = line.queued ? `${line.text} (queued)` : line.text;
  const rows: RenderRow[] = [];
  for (const segment of text.split("\n")) {
    const firstOfLine = rows.length === 0;
    const columns = Math.max(10, width - (firstOfLine ? prefixCols : continuationIndent));
    for (const chunk of wrapSegment(segment, columns)) {
      rows.push({
        key: `${line.id}:${rows.length}`,
        line,
        text: chunk,
        first: rows.length === 0,
      });
    }
  }
  return rows;
}

function explodeAll(lines: ChatLine[], width: number): RenderRow[] {
  const rows: RenderRow[] = [];
  for (const line of lines) rows.push(...explodeLine(line, width));
  return rows;
}

const SCROLLBAR_COLS = 1;
/** Horizontal padding on the chat text panel (each side). */
const CONTENT_PAD_X = 2;

/** Columns available for wrapped chat text (outer width − scrollbar − padding). */
export function chatContentWidth(width: number): number {
  return Math.max(20, width - SCROLLBAR_COLS - CONTENT_PAD_X * 2);
}

/** Total rendered rows for the chat content; used to clamp scrolling. */
export function totalRowCount(lines: ChatLine[], width: number): number {
  return explodeAll(lines, chatContentWidth(width)).length;
}

interface Props {
  lines: ChatLine[];
  height: number;
  width: number;
  scrollOffset: number; // 0 = follow tail; N = rows scrolled up from bottom
}

/**
 * Build a 1-column scrollbar: dim track + accent thumb.
 * scrollOffset 0 = pinned to bottom (thumb at bottom); higher = scrolled up.
 */
function scrollbarGlyphs(height: number, total: number, view: number, offset: number): string[] {
  const glyphs = Array.from({ length: height }, () => "│");
  if (total <= view || height <= 0) {
    return glyphs.map(() => " ");
  }
  const maxScroll = total - view;
  const thumbSize = Math.max(1, Math.min(height, Math.round((view / total) * height)));
  const travel = Math.max(0, height - thumbSize);
  const clamped = Math.max(0, Math.min(maxScroll, offset));
  // Convert bottom-based offset into a top-based thumb position.
  const thumbTop = travel === 0 ? 0 : Math.round(((maxScroll - clamped) / maxScroll) * travel);
  for (let i = thumbTop; i < thumbTop + thumbSize && i < height; i++) {
    glyphs[i] = "█";
  }
  return glyphs;
}

export function ChatLog({ lines, height, width, scrollOffset }: Props) {
  const wrapWidth = chatContentWidth(width);
  const panelWidth = Math.max(1, width - SCROLLBAR_COLS);
  const rows = explodeAll(lines, wrapWidth);
  const end = Math.max(0, rows.length - Math.max(0, scrollOffset));
  const start = Math.max(0, end - height);
  const visible = rows.slice(start, end);
  const bar = scrollbarGlyphs(height, rows.length, height, scrollOffset);
  // column-reverse anchors the newest row to the bottom edge, so any estimate
  // error clips old rows at the top instead of hiding the newest.
  return (
    <Box flexDirection="row" width={width} height={height} overflow="hidden">
      <Box
        flexDirection="column-reverse"
        height={height}
        width={panelWidth}
        paddingX={CONTENT_PAD_X}
        overflow="hidden"
        flexGrow={1}
      >
        {[...visible].reverse().map((row) => (
          <RowView key={row.key} row={row} />
        ))}
      </Box>
      <Box flexDirection="column" width={SCROLLBAR_COLS} height={height} overflow="hidden">
        {bar.map((glyph, index) => (
          <Box key={index} height={1} width={SCROLLBAR_COLS}>
            <Text color={glyph === "█" ? ACCENT : undefined} dimColor={glyph !== "█"}>
              {glyph}
            </Text>
          </Box>
        ))}
      </Box>
    </Box>
  );
}

function RowView({ row }: { row: RenderRow }) {
  const { line, first, text } = row;
  if (line.kind === "user") {
    return (
      <Text wrap="truncate">
        <Text bold color={ACCENT}>
          {first ? "\u2726 " : "  "}
        </Text>
        {text}
      </Text>
    );
  }
  if (line.kind === "assistant") {
    return (
      <Text wrap="truncate">
        <Text bold color={GREEN}>
          {first ? "\u25cf " : "  "}
        </Text>
        {text}
      </Text>
    );
  }
  if (line.kind === "trace") {
    return (
      <Text wrap="truncate" dimColor italic>
        <Text color={ACCENT}>{first && line.style ? `${line.style}: ` : ""}</Text>
        {text}
      </Text>
    );
  }
  const color = styleColor(line.style);
  return (
    <Text wrap="truncate" color={color} dimColor={!color}>
      {text}
    </Text>
  );
}
