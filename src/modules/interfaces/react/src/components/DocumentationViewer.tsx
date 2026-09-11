/** Interactive Markdown documentation browser for Cyber-AutoAgent. */

import React, {useCallback, useEffect, useMemo, useState} from "react";
import {Box, Text, useStdin} from "ink";
import * as fs from "fs/promises";
import * as path from "path";
import {themeManager} from "../themes/theme-manager.js";
import {getDocumentationUrl} from "../utils/documentationLinks.js";
import {MarkdownRow, renderMarkdownRow, tokenizeMarkdown} from "../utils/markdownRows.js";

interface DocumentInfo {
  name: string;
  file: string;
  description: string;
}

interface DocumentationViewerProps {
  onClose: () => void;
  selectedDoc?: number;
}

const documents: DocumentInfo[] = [
  {name: "User Instructions", file: "user-instructions.md", description: "Using Cyber-AutoAgent from the terminal"},
  {name: "Architecture Overview", file: "architecture.md", description: "Workflow concepts and troubleshooting context"},
  {name: "Deployment Guide", file: "deployment.md", description: "Setup, configuration, and deployment troubleshooting"},
  {name: "Observability & Evaluation", file: "observability-evaluation.md", description: "Tracing, evaluation, and operation health"},
  {name: "Memory System", file: "memory.md", description: "Memory modes, storage, and retrieval"},
  {name: "Terminal Interface", file: "terminal-frontend.md", description: "React terminal behavior and diagnostics"},
  {name: "Prompt Management", file: "prompt_management.md", description: "Modules and prompt configuration"},
];

const findDocument = async (filename: string): Promise<string | null> => {
  const cwd = process.cwd();
  const possiblePaths = [
    path.join(cwd, "docs", filename),
    path.join(cwd, "..", "docs", filename),
    path.join(cwd, "..", "..", "docs", filename),
    path.join(cwd, "..", "..", "..", "docs", filename),
    path.join(cwd, "..", "..", "..", "..", "docs", filename),
    path.join("/app", "docs", filename),
  ];

  for (const candidate of possiblePaths) {
    try {
      return await fs.readFile(candidate, "utf-8");
    } catch {
      // Try the next known project location.
    }
  }
  return null;
};

const fallbackContent = async (filename: string): Promise<string> => {
  const url = await getDocumentationUrl(filename);
  return `# Documentation unavailable\n\nThe full document is not available in this installation.\n\nRead the complete document: ${url}`;
};

export const DocumentationViewer: React.FC<DocumentationViewerProps> = React.memo(({onClose, selectedDoc}) => {
  const theme = themeManager.getCurrentTheme();
  const {stdin} = useStdin();
  const [selectedIndex, setSelectedIndex] = useState(() => Math.min(Math.max((selectedDoc ?? 1) - 1, 0), documents.length - 1));
  const [viewMode, setViewMode] = useState<"list" | "view">(selectedDoc ? "view" : "list");
  const [documentContent, setDocumentContent] = useState("");
  const [scrollOffset, setScrollOffset] = useState(0);
  const [loading, setLoading] = useState(false);

  const rows = useMemo(() => tokenizeMarkdown(documentContent), [documentContent]);
  const linesPerPage = 20;
  const maxScroll = Math.max(0, rows.length - linesPerPage);

  useEffect(() => {
    if (viewMode !== "view") return;
    let cancelled = false;
    setLoading(true);
    setScrollOffset(0);
    void (async () => {
      const content = await findDocument(documents[selectedIndex].file) ?? await fallbackContent(documents[selectedIndex].file);
      if (!cancelled) {
        setDocumentContent(content);
        setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [selectedIndex, viewMode]);

  const handleNavigation = useCallback((input: string, key: Record<string, boolean> = {}) => {
    if (key.escape || (key.ctrl && input === "c")) {
      if (viewMode === "view") {
        setViewMode("list");
        setDocumentContent("");
      } else {
        onClose();
      }
      return;
    }

    if (viewMode === "list") {
      if (key.upArrow) setSelectedIndex((previous) => previous > 0 ? previous - 1 : documents.length - 1);
      if (key.downArrow) setSelectedIndex((previous) => previous < documents.length - 1 ? previous + 1 : 0);
      if (key.leftArrow) setSelectedIndex((previous) => previous > 0 ? previous - 1 : documents.length - 1);
      if (key.rightArrow) setSelectedIndex((previous) => previous < documents.length - 1 ? previous + 1 : 0);
      if (key.return) setViewMode("view");
      return;
    }

    if (key.leftArrow || key.rightArrow) {
      setSelectedIndex((previous) => {
        if (key.leftArrow) return previous > 0 ? previous - 1 : documents.length - 1;
        return previous < documents.length - 1 ? previous + 1 : 0;
      });
      setDocumentContent("");
      setScrollOffset(0);
      return;
    }

    if (key.upArrow || input === "k") setScrollOffset((previous) => Math.max(0, previous - 1));
    if (key.downArrow || input === "j") setScrollOffset((previous) => Math.min(maxScroll, previous + 1));
    if (key.pageUp) setScrollOffset((previous) => Math.max(0, previous - linesPerPage));
    if (key.pageDown) setScrollOffset((previous) => Math.min(maxScroll, previous + linesPerPage));
    if (input === "g") setScrollOffset(0);
    if (input === "G") setScrollOffset(maxScroll);
  }, [maxScroll, onClose, viewMode]);

  useEffect(() => {
    let pendingInput = "";
    const sequences: Array<[string, string, Record<string, boolean>]> = [
      ["\u001B[5~", "", {pageUp: true}],
      ["\u001B[6~", "", {pageDown: true}],
      ["\u001B[A", "", {upArrow: true}],
      ["\u001B[B", "", {downArrow: true}],
      ["\u001B[C", "", {rightArrow: true}],
      ["\u001B[D", "", {leftArrow: true}],
    ];

    const processInput = () => {
      while (pendingInput.length > 0) {
        const matchingSequence = sequences.find(([sequence]) => pendingInput.startsWith(sequence));
        if (matchingSequence) {
          const [sequence, input, key] = matchingSequence;
          pendingInput = pendingInput.slice(sequence.length);
          handleNavigation(input, key);
          continue;
        }

        if (pendingInput.startsWith("\u001B")) {
          const isPartialSequence = pendingInput.length > 1
            && sequences.some(([sequence]) => sequence.startsWith(pendingInput));
          if (isPartialSequence) return;
          pendingInput = pendingInput.slice(1);
          handleNavigation("", {escape: true});
          continue;
        }

        const input = pendingInput[0];
        pendingInput = pendingInput.slice(1);
        handleNavigation(input, {return: input === "\r" || input === "\n", ctrl: input === "\u0003"});
      }
    };

    const onData = (chunk: Buffer | string) => {
      pendingInput += chunk.toString();
      processInput();
    };

    stdin.on("data", onData);
    return () => {
      stdin.removeListener("data", onData);
    };
  }, [handleNavigation, stdin]);

  const renderList = () => (
    <Box flexDirection="column">
      <Box marginBottom={1}><Text color={theme.primary} bold>■ Cyber-AutoAgent Documentation</Text></Box>
      <Box borderStyle="single" borderColor={theme.accent} paddingX={1} flexDirection="column">
        <Text color={theme.muted}>Select a document to read:</Text>
        <Box marginTop={1} />
        {documents.map((document, index) => (
          <Box key={document.file} marginBottom={1}>
            <Text color={index === selectedIndex ? theme.primary : theme.foreground}>
              {index === selectedIndex ? "▶ " : "  "}{index + 1}. {document.name}
            </Text>
            <Text color={theme.muted}>{"     "}{document.description}</Text>
          </Box>
        ))}
      </Box>
      <Box marginTop={1}><Text color={theme.muted}>Use ↑↓/←→ to navigate, Enter to read, Esc to exit</Text></Box>
    </Box>
  );

  const renderDocument = () => {
    if (loading) return <Text color={theme.info}>Loading document...</Text>;
    const visibleRows = rows.slice(scrollOffset, scrollOffset + linesPerPage);
    const currentRow = rows.length === 0 ? 0 : scrollOffset + 1;
    const percentage = rows.length === 0 ? 0 : Math.round((currentRow / rows.length) * 100);
    return (
      <Box flexDirection="column">
        <Box borderStyle="single" borderColor={theme.accent} paddingX={1} marginBottom={1} justifyContent="space-between">
          <Text color={theme.primary} bold>▎{documents[selectedIndex].name}</Text>
          <Text color={theme.muted}>Row {currentRow}/{rows.length} ({percentage}%)</Text>
        </Box>
        <Box flexDirection="column" paddingX={1}>
          {visibleRows.map((row, index) => renderMarkdownRow(row, `${scrollOffset + index}`, theme.foreground))}
        </Box>
        <Box marginTop={1} paddingX={1}>
          <Text color={theme.muted}>↑↓/jk: scroll | ←→: document | PgUp/PgDn: page | g/G: top/bottom | Esc: back to list</Text>
        </Box>
      </Box>
    );
  };

  return (
    <Box flexDirection="column" flexGrow={1}>
      <Box flexDirection="column" padding={1} borderStyle="round" borderColor={theme.accent} marginTop={1}>
        {viewMode === "list" ? renderList() : renderDocument()}
      </Box>
    </Box>
  );
});

DocumentationViewer.displayName = "DocumentationViewer";
