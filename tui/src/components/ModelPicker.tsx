import React from "react";
import { Box, Text } from "ink";

import type { ModelItem } from "../protocol.js";
import { ACCENT } from "../theme.js";

interface Props {
  models: ModelItem[];
  highlighted: number;
  current: string;
}

const MAX_VISIBLE = 16;

export function ModelPicker({ models, highlighted, current }: Props) {
  const start = Math.max(
    0,
    Math.min(highlighted - Math.floor(MAX_VISIBLE / 2), models.length - MAX_VISIBLE),
  );
  const visible = models.slice(start, start + MAX_VISIBLE);
  return (
    <Box flexDirection="column" borderStyle="round" borderColor={ACCENT} paddingX={2} paddingY={1} width={72}>
      <Text bold color={ACCENT}>
        Switch model ({models.length})
      </Text>
      <Box flexDirection="column" marginTop={1}>
        {visible.map((model, index) => {
          const absolute = start + index;
          const active = absolute === highlighted;
          const mark = model.id === current ? "● " : "  ";
          const provider = model.provider ? `  ${model.provider}` : "";
          return (
            <Text key={`${model.provider}:${model.id}`} inverse={active} color={active ? ACCENT : undefined}>
              {mark}
              {model.id}
              <Text dimColor={!active}>{provider}</Text>
            </Text>
          );
        })}
      </Box>
      <Box marginTop={1}>
        <Text dimColor>{"\u2191/\u2193 move \u00b7 Enter select \u00b7 Esc cancel \u00b7 type /model <id>"}</Text>
      </Box>
    </Box>
  );
}
