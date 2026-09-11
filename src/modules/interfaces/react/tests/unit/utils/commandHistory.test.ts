import fs from "fs";
import os from "os";
import path from "path";
import { afterEach, beforeEach, describe, expect, it } from "@jest/globals";
import {
    appendCommandHistory,
    getCommandHistoryPath,
    loadCommandHistory,
    normalizeCommandHistory,
    saveCommandHistory,
} from "../../../src/utils/commandHistory.js";

describe("commandHistory", () => {
    const configDir = path.join(os.tmpdir(), `cyber-history-test-${process.pid}`);
    const originalConfigDir = process.env.CYBER_CONFIG_DIR;

    beforeEach(() => {
        process.env.CYBER_CONFIG_DIR = configDir;
        fs.rmSync(configDir, {recursive: true, force: true});
    });

    afterEach(() => {
        if (originalConfigDir === undefined) {
            delete process.env.CYBER_CONFIG_DIR;
        } else {
            process.env.CYBER_CONFIG_DIR = originalConfigDir;
        }
        fs.rmSync(configDir, {recursive: true, force: true});
    });

    it("normalizes command entries and limits history length", () => {
        expect(normalizeCommandHistory(["", " target one ", "target one", 12, "execute"], 2)).toEqual([
            "target one",
            "execute",
        ]);
    });

    it("loads missing or invalid history as empty", () => {
        expect(loadCommandHistory()).toEqual([]);

        fs.mkdirSync(configDir, {recursive: true});
        fs.writeFileSync(getCommandHistoryPath(), "{not-json", "utf8");
        expect(loadCommandHistory()).toEqual([]);
    });

    it("saves and appends persistent command history", () => {
        saveCommandHistory(["target https://one.example"]);

        expect(loadCommandHistory()).toEqual(["target https://one.example"]);
        expect(appendCommandHistory(loadCommandHistory(), " execute scan ")).toEqual([
            "target https://one.example",
            "execute scan",
        ]);
        expect(appendCommandHistory(loadCommandHistory(), "execute scan")).toEqual([
            "target https://one.example",
            "execute scan",
        ]);
    });

    it("does not append slash commands to persistent history", () => {
        saveCommandHistory(["target https://one.example"]);

        expect(appendCommandHistory(loadCommandHistory(), " /help ")).toEqual(["target https://one.example"]);
        expect(loadCommandHistory()).toEqual(["target https://one.example"]);
    });
});
