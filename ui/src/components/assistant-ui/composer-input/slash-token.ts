import { Extension } from "@tiptap/react";
import { Plugin, PluginKey } from "@tiptap/pm/state";
import { Decoration, DecorationSet } from "@tiptap/pm/view";

import type { SlashCommand } from "@/lib/slash";

/** Asked to redraw when the commands change — the skill list arrives after the editor exists. */
export const SLASH_REFRESH = "kith-slash-refresh";

const key = new PluginKey("kith-slash-token");

/**
 * A recognised command at the start of the message, drawn as a token.
 *
 * A decoration, not a mark or a node: the document stays plain text, so the markdown the store
 * holds is exactly what was typed, and deleting a letter of `/workflow` simply stops it being
 * one. Teal for `/workflow`, as it is everywhere; the Kith accent for the rest.
 */
export const SlashToken = Extension.create<{ commands: () => readonly SlashCommand[] }>({
  name: "slashToken",
  addOptions() {
    return { commands: () => [] };
  },
  addProseMirrorPlugins() {
    const commands = this.options.commands;
    return [
      new Plugin({
        key,
        props: {
          decorations(state) {
            const first = state.doc.firstChild;
            if (!first || first.type.name !== "paragraph") return null;
            const line = first.textBetween(0, first.content.size, "\n", "\n");
            const found = line.match(/^\/(\S+)(?=\s|$)/);
            if (!found || !commands().some((one) => one.name === found[1])) return null;
            return DecorationSet.create(state.doc, [
              Decoration.inline(1, 1 + found[0].length, {
                class: "kith-slash-token",
                "data-tone": found[1] === "workflow" ? "workflow" : "command",
              }),
            ]);
          },
        },
      }),
    ];
  },
});
