import React from "react";
import TestRenderer, { ReactTestRenderer, act } from "../test-renderer.js";
import { describe, expect, it, jest } from "@jest/globals";
import { ModalType } from "../../../src/hooks/useModalManager.js";

(globalThis as any).IS_REACT_ACT_ENVIRONMENT = true;

jest.unstable_mockModule("../../../src/components/LazyComponents.js", () => ({
  ConfigEditorLazy: ({ onClose }: any) => <button onClick={onClose}>config</button>,
  ModuleSelectorLazy: ({ onClose }: any) => <button onClick={onClose}>module</button>,
  DocumentationViewerLazy: ({ onClose, selectedDoc }: any) => <button onClick={onClose}>doc:{selectedDoc}</button>,
}));

jest.unstable_mockModule("../../../src/components/InitializationFlow.js", () => ({
  InitializationFlow: ({ onComplete }: any) => <button onClick={onComplete}>initialization</button>,
}));

const textFromTree = (node: unknown): string => {
  if (node === null || node === undefined) {
    return "";
  }
  if (typeof node === "string") {
    return node;
  }
  if (Array.isArray(node)) {
    return node.map(textFromTree).join("");
  }
  if (typeof node === "object" && "children" in node) {
    return textFromTree((node as { children?: unknown }).children);
  }
  return "";
};

const load = async () => {
  const { ModalRegistry } = await import("../../../src/components/ModalRegistry.js");
  return { ModalRegistry };
};

describe("ModalRegistry safety warning state", () => {
  it("preserves authorization acknowledgement across parent rerenders", async () => {
    const { ModalRegistry } = await load();
    const onClose = jest.fn();
    const onSafetyConfirm = jest.fn();
    const modalContext = {
      pendingExecution: {
        target: "https://authorized.example",
        module: "web",
        objective: "test",
      },
    };

    let view!: ReactTestRenderer;
    act(() => {
      view = TestRenderer.create(
        <ModalRegistry
          activeModal={ModalType.SAFETY_WARNING}
          modalContext={modalContext}
          onClose={onClose}
          terminalWidth={100}
          onSafetyConfirm={onSafetyConfirm}
        />
      );
    });

    act(() => {
      (global as any).__inkInputHandler("y", {});
    });
    expect(textFromTree(view.toJSON())).toContain("Proceed with cyber operation?");

    act(() => {
      view.update(
        <ModalRegistry
          activeModal={ModalType.SAFETY_WARNING}
          modalContext={modalContext}
          onClose={onClose}
          terminalWidth={120}
          onSafetyConfirm={onSafetyConfirm}
        />
      );
    });

    expect(textFromTree(view.toJSON())).toContain("Proceed with cyber operation?");

    act(() => {
      (global as any).__inkInputHandler("y", {});
    });
    expect(onClose).toHaveBeenCalledTimes(1);
    expect(onSafetyConfirm).toHaveBeenCalledTimes(1);

    act(() => {
      view.unmount();
    });
  });
});
