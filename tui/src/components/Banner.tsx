import React from "react";
import { Box, Text } from "ink";

interface Props {
  cwd: string;
  session: string;
  model: string;
  version: string;
}

export function Banner({ cwd, session, model, version }: Props) {
  return (
    <Box flexDirection="column" borderStyle="round" borderColor="blue" paddingX={2} paddingY={1} marginX={2} marginTop={1}>
      <Text bold>LangBridge</Text>
      <Text dimColor>Send /help for commands.</Text>
      <Text>
        <Text dimColor>{"Directory:  "}</Text>
        {cwd}
      </Text>
      <Text>
        <Text dimColor>{"Session:    "}</Text>
        {session}
      </Text>
      <Text>
        <Text dimColor>{"Model:      "}</Text>
        {model}
      </Text>
      <Text>
        <Text dimColor>{"Version:    "}</Text>
        {version}
      </Text>
    </Box>
  );
}
