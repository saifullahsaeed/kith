/**
 * A project's conversations: the list on the left, the one you are on summarised on the right.
 *
 * It was one long column of rows, each opening the chat — which made "which of these was the one
 * where he built the notifications?" a matter of opening them in turn. The summary answers that
 * without leaving: what you asked for in it, and what he wrote there. Opening is still one key.
 */
import { useEffect, useRef, useState, type KeyboardEvent } from "react";
import { useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { ArrowUpRight, FileText } from "lucide-react";
import { fetchConversation, type ConversationSummary } from "@/lib/backend";
import type { Project } from "@/lib/backend/brain";
import { keys } from "@/lib/query-keys";
import { pathForConversation } from "@/lib/router";
import { cn } from "@/lib/utils";
import { BUTTON, Empty, Kbd, Label, LiveDot, Segmented } from "./chrome";
import {
  chatState,
  clock,
  dayAndClock,
  filesWritten,
  filterChats,
  homeShort,
  insideProject,
  looksLikeALink,
  rowWhen,
  shortDay,
  whatYouAsked,
  type ChatFilter,
} from "./project-page-model";

/** A pasted link as a title reads without its `<https://` — the address is the part you know. */
const displayTitle = (title: string) =>
  looksLikeALink(title) ? title.trim().replace(/^<?https?:\/\//, "").replace(/>$/, "") : title;

/** "Today, 11:19 AM" → "today, 11:19 AM": the same words, mid-sentence. */
const sentence = (text: string) => text.replace(/^(Today|Yesterday)/, (word) => word.toLowerCase());

/** Rows before "Show N more". Eight is what the list holds at the window's real height. */
const PAGE = 8;

/** Of the files he wrote, how many the summary names. */
const FILES_SHOWN = 6;

export function ProjectChats({
  items,
  total,
  loading,
  project,
  onNew,
}: {
  /** This project's conversations, newest first, already through the panel's search. */
  items: ConversationSummary[];
  /** Before the search — so an empty list can tell "nothing here" from "nothing matches". */
  total: number;
  loading: boolean;
  project: Project;
  onNew?: () => void;
}) {
  const navigate = useNavigate();
  const [filter, setFilter] = useState<ChatFilter>("all");
  const [text, setText] = useState("");
  const [chosen, setChosen] = useState<string | null>(null);
  const [all, setAll] = useState(false);
  const list = useRef<HTMLDivElement>(null);

  const shown = filterChats(items, filter, text);
  const visible = all ? shown : shown.slice(0, PAGE);
  // The first row stands in until you pick one, so the summary is never an empty half-screen.
  const selected = shown.find((one) => one.id === chosen) ?? shown[0] ?? null;
  const working = items.filter((one) => chatState(one) === "working").length;
  const waiting = items.filter((one) => chatState(one) === "waiting").length;

  /* Straight into the conversation, which closes the panel on the way — the route for a chat is
   * not a panel route, so opening one is also leaving here. */
  const open = (id: string) => navigate(pathForConversation(id));

  const go = (direction: -1 | 1) => {
    if (!shown.length) return;
    const at = selected ? shown.findIndex((one) => one.id === selected.id) : -1;
    const next = shown[Math.max(0, Math.min(shown.length - 1, at + direction))];
    if (shown.indexOf(next) >= PAGE) setAll(true);
    setChosen(next.id);
  };

  // Keep the row the keys moved to in view.
  const selectedId = selected?.id;
  useEffect(() => {
    if (!selectedId) return;
    list.current?.querySelector(`[data-chat="${CSS.escape(selectedId)}"]`)?.scrollIntoView({ block: "nearest" });
  }, [selectedId]);

  /* ↑ ↓ work from the page as well as from inside the list — the hint says "↑ ↓ to move", not
   * "click the list, then ↑ ↓". Pressed anywhere that does not use the arrows itself (a field, a
   * menu, the list), they move the selection and put the keyboard on the list, so the Enter after
   * them opens it. Enter alone is only taken when nothing has focus: a focused button owns its
   * Enter. */
  const goRef = useRef(go);
  goRef.current = go;
  const openRef = useRef<() => void>(() => {});
  openRef.current = () => (selected ? open(selected.id) : undefined);
  useEffect(() => {
    const onKey = (event: globalThis.KeyboardEvent) => {
      if (event.defaultPrevented || event.metaKey || event.ctrlKey || event.altKey) return;
      if (document.querySelector('[role="dialog"], [role="menu"]')) return;
      const target = event.target instanceof HTMLElement ? event.target : null;
      const typing = target?.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(target?.tagName ?? "");
      if (typing || target?.closest('[role="listbox"]')) return;
      if (event.key === "ArrowUp" || event.key === "ArrowDown") {
        event.preventDefault();
        goRef.current(event.key === "ArrowUp" ? -1 : 1);
        list.current?.focus();
      } else if (event.key === "Enter" && (!target || target === document.body)) {
        event.preventDefault();
        openRef.current();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const onListKey = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key === "ArrowUp" || event.key === "ArrowDown") {
      event.preventDefault();
      go(event.key === "ArrowUp" ? -1 : 1);
    } else if (event.key === "Enter" && selected) {
      event.preventDefault();
      open(selected.id);
    }
  };

  const hidden = shown.slice(PAGE);
  const oldest = hidden[hidden.length - 1];

  return (
    <section aria-label="Conversations" className="flex min-h-0 flex-1 flex-col">
      <div className="mt-4 flex h-9 shrink-0 items-center gap-3">
        <Segmented
          label="Show conversations"
          value={filter}
          onChange={(value) => {
            setFilter(value);
            setAll(false);
          }}
          options={[
            { value: "all", label: "All", count: items.length },
            {
              value: "working",
              label: (
                <span className="flex items-center gap-1.5">
                  <span className="bg-roam size-1.5 rounded-full" />
                  Working
                  <span className="text-muted-foreground/80 tabular-nums">{working}</span>
                </span>
              ),
            },
            {
              value: "waiting",
              label: (
                <span className="flex items-center gap-1.5">
                  <span className="bg-kith size-1.5 rounded-full" />
                  Waiting on you
                  <span className={cn("tabular-nums", waiting ? "text-kith" : "text-muted-foreground/80")}>{waiting}</span>
                </span>
              ),
            },
          ]}
        />
        <span className="flex-1" />
        <span className="text-muted-foreground/80 flex items-center gap-1.5 text-xs">
          <Kbd>↑</Kbd>
          <Kbd>↓</Kbd>
          to move
          <span className="text-muted-foreground/50 px-0.5">·</span>
          <Kbd>Enter</Kbd>
          opens it
        </span>
        <label htmlFor="chat-filter" className="sr-only">
          Filter conversations
        </label>
        <input
          id="chat-filter"
          type="text"
          value={text}
          onChange={(event) => {
            setText(event.target.value);
            setAll(false);
          }}
          onKeyDown={(event) => {
            // ↓ from the filter goes into the list it filtered.
            if (event.key === "ArrowDown") {
              event.preventDefault();
              list.current?.focus();
            }
            if (event.key === "Escape" && text) {
              event.preventDefault();
              setText("");
            }
          }}
          placeholder="Filter by title or what he said…"
          className="border-input bg-card text-foreground placeholder:text-muted-foreground/80 focus-visible:border-ring h-8 w-[260px] rounded-lg border px-3 text-[12.5px] outline-none"
        />
      </div>

      {loading ? null : total === 0 ? (
        <Empty
          className="mt-3.5"
          title="Nothing has been said in this project yet."
          action={
            onNew ? (
              <button type="button" onClick={onNew} className={BUTTON.secondary}>
                New chat in this project
              </button>
            ) : undefined
          }
        >
          A chat started here begins inside the project, with its board and memory already in front of him.
        </Empty>
      ) : (
        <div className="mt-3.5 grid min-h-0 flex-1 grid-cols-[minmax(0,3fr)_minmax(0,2fr)] gap-4">
          <section
            aria-label={`Conversations in ${project.name}`}
            className="border-border bg-card flex min-h-0 flex-col overflow-hidden rounded-[14px] border"
          >
            <div
              ref={list}
              role="listbox"
              tabIndex={0}
              aria-label="Conversations, newest first"
              aria-activedescendant={selected ? `chat-${selected.id}` : undefined}
              onKeyDown={onListKey}
              // The focus is drawn on the selected row, not around the list: it is the row the
              // keys act on, and a ring round the whole list says nothing about which one.
              className="group/list flex min-h-0 flex-col gap-0.5 overflow-y-auto p-1.5 outline-none"
            >
              {visible.map((one) => (
                <ChatRow
                  key={one.id}
                  chat={one}
                  selected={one.id === selected?.id}
                  onSelect={() => setChosen(one.id)}
                  onOpen={() => open(one.id)}
                />
              ))}
              {shown.length === 0 ? (
                <p className="text-muted-foreground/80 px-3.5 py-6 text-[12.5px]">
                  {filter === "waiting"
                    ? "Nothing here is waiting on you."
                    : filter === "working"
                      ? "He isn't working in any of these right now."
                      : "Nothing matches that."}
                </p>
              ) : null}
            </div>
            {hidden.length && !all ? (
              <button
                type="button"
                onClick={() => setAll(true)}
                className="text-muted-foreground/80 hover:text-foreground hover:bg-muted/50 mx-1.5 mt-auto mb-1.5 h-9 shrink-0 rounded-lg px-3.5 text-left text-xs transition-colors"
              >
                Show {hidden.length} more{oldest ? ` — back to ${shortDay(oldest.updatedAt)}` : ""}
              </button>
            ) : null}
          </section>

          {selected ? (
            <ChatSummary chat={selected} project={project} onOpen={() => open(selected.id)} onNew={onNew} />
          ) : (
            <div />
          )}
        </div>
      )}
    </section>
  );
}

function ChatRow({
  chat,
  selected,
  onSelect,
  onOpen,
}: {
  chat: ConversationSummary;
  selected: boolean;
  onSelect: () => void;
  onOpen: () => void;
}) {
  const state = chatState(chat);
  return (
    // An option of the listbox above, which owns the keyboard — the row only answers the mouse.
    <div
      id={`chat-${chat.id}`}
      data-chat={chat.id}
      role="option"
      aria-selected={selected}
      onClick={onSelect}
      onDoubleClick={onOpen}
      title="Double-click, or press Enter, to open it"
      className={cn(
        "grid cursor-default grid-cols-[minmax(0,1fr)_112px] items-start gap-x-5 rounded-[10px] px-3.5 pt-2.5 pb-[11px] transition-colors",
        selected ? "bg-muted group-focus-visible/list:ring-ring/50 group-focus-visible/list:ring-1" : "hover:bg-muted/50",
      )}
    >
      <span className="flex min-w-0 flex-col gap-[5px]">
        <span className="flex h-[18px] min-w-0 items-center gap-2.5">
          <span
            className={cn(
              "text-foreground min-w-0 truncate",
              looksLikeALink(chat.title) ? "font-mono text-xs font-medium" : "text-[13.5px] font-semibold",
            )}
          >
            {displayTitle(chat.title)}
          </span>
          {state === "working" ? (
            <span className="text-roam flex shrink-0 items-center gap-1.5 text-[11.5px]">
              <LiveDot />
              working
            </span>
          ) : state === "waiting" ? (
            <span className="text-kith flex shrink-0 items-center gap-1.5 text-[11.5px] font-semibold">
              <span className="bg-kith size-[7px] rounded-full" />
              waiting on you
            </span>
          ) : null}
        </span>
        {chat.lastSaid ? (
          <span className="text-muted-foreground line-clamp-2 text-[12.5px] leading-normal">
            {state === "waiting" ? <span className="text-kith">Asked you: </span> : null}
            {chat.lastSaid}
          </span>
        ) : null}
      </span>
      <span className="flex flex-col items-end gap-[5px] pt-px">
        <span className="text-foreground/80 text-xs whitespace-nowrap tabular-nums" title={dayAndClock(chat.updatedAt)}>
          {rowWhen(chat.updatedAt)}
        </span>
        <span className="text-muted-foreground/80 text-[11.5px] whitespace-nowrap tabular-nums">
          {chat.messages} message{chat.messages === 1 ? "" : "s"}
        </span>
      </span>
    </div>
  );
}

/**
 * The selected conversation, without opening it: when, how long, what you asked for, and the
 * files he wrote there.
 *
 * Read from the same transcript page opening the chat would fetch, under the same key — so
 * looking here warms the chat rather than costing a second request when you do open it. Both
 * lists come out of that one page: the asks from your turns, the files from his write and edit
 * calls. A long conversation's page is its last forty turns, and the lists say so rather than
 * passing for the whole of it.
 */
function ChatSummary({
  chat,
  project,
  onOpen,
  onNew,
}: {
  chat: ConversationSummary;
  project: Project;
  onOpen: () => void;
  onNew?: () => void;
}) {
  const detail = useQuery({
    queryKey: keys.conversation(chat.id),
    queryFn: () => fetchConversation(chat.id),
    staleTime: 30_000,
  });
  const state = chatState(chat);
  const timeline = detail.data?.timeline ?? [];
  const { asks, left } = whatYouAsked(timeline);
  const files = filesWritten(timeline);
  const partial = detail.data?.hasMore ?? false;
  const last = asks.length - 1;

  return (
    <section
      aria-label="Selected conversation"
      className="border-border bg-card flex min-h-0 flex-col overflow-hidden rounded-[14px] border px-5 pt-5 pb-[18px]"
    >
      <div className="flex shrink-0 flex-col gap-2">
        <Label>Conversation</Label>
        <div className="flex min-w-0 items-center gap-2.5">
          <h2
            className={cn(
              "m-0 min-w-0 truncate tracking-[-0.01em]",
              looksLikeALink(chat.title) ? "font-mono text-base font-semibold" : "text-xl font-[650]",
            )}
            title={chat.title}
          >
            {displayTitle(chat.title)}
          </h2>
          {state === "working" ? (
            <span className="bg-roam/10 text-roam flex h-[22px] shrink-0 items-center gap-1.5 rounded-full ps-2 pe-[9px] text-[11px]">
              <LiveDot className="size-1.5" />
              working
            </span>
          ) : state === "waiting" ? (
            <span className="bg-kith/10 text-kith flex h-[22px] shrink-0 items-center gap-1.5 rounded-full ps-2 pe-[9px] text-[11px] font-semibold">
              <span className="bg-kith size-1.5 rounded-full" />
              waiting on you
            </span>
          ) : null}
        </div>
        <span className="text-muted-foreground flex flex-wrap gap-x-2 text-[12.5px] tabular-nums">
          <span>Started {sentence(dayAndClock(chat.createdAt))}</span>
          <span className="text-muted-foreground/50">·</span>
          <span>
            {chat.messages} message{chat.messages === 1 ? "" : "s"}
          </span>
          <span className="text-muted-foreground/50">·</span>
          <span>
            last message {shortDay(chat.updatedAt) === "Today" ? clock(chat.updatedAt) : sentence(dayAndClock(chat.updatedAt))}
          </span>
        </span>
      </div>

      <div className="mt-[22px] flex min-h-0 flex-col gap-1.5 overflow-hidden">
        <div className="flex shrink-0 items-baseline gap-2 px-0.5">
          <Label count={detail.data ? asks.length : undefined}>What you asked for</Label>
          <span className="flex-1" />
          {left ? (
            <span className="text-muted-foreground/80 text-[11.5px]">
              {left} short repl{left === 1 ? "y" : "ies"} left out
            </span>
          ) : null}
        </div>
        {detail.isPending ? (
          <p className="text-muted-foreground/80 px-2 text-[12.5px]">Reading it…</p>
        ) : detail.isError ? (
          <p className="text-muted-foreground/80 px-2 text-[12.5px]">Couldn't read this conversation.</p>
        ) : asks.length === 0 ? (
          <p className="text-muted-foreground/80 px-2 text-[12.5px]">Nothing you typed here, yet.</p>
        ) : (
          <ol className="m-0 flex min-h-0 list-none flex-col gap-px overflow-y-auto p-0">
            {partial ? (
              <li className="text-muted-foreground/80 px-2 pb-1 text-[11.5px]">From the last part of a long conversation</li>
            ) : null}
            {asks.map((ask, at) => {
              const now = at === last && state === "working";
              return (
                <li
                  key={`${ask.at}-${at}`}
                  title={ask.at ? `${dayAndClock(ask.at)} — ${ask.text}` : ask.text}
                  className={cn("flex h-7 shrink-0 items-center gap-2 rounded-[7px] px-2", now && "bg-muted/60")}
                >
                  <span className="text-muted-foreground/80 w-16 shrink-0 font-mono text-[11px] tabular-nums">
                    {ask.at ? clock(ask.at) : ""}
                  </span>
                  <span className={cn("min-w-0 flex-1 truncate text-[12.5px]", now ? "text-foreground" : "text-foreground/80")}>
                    {ask.text}
                  </span>
                  {now ? (
                    <span className="text-roam flex shrink-0 items-center gap-1.5 text-[11px]">
                      <LiveDot className="size-1.5" />
                      on it now
                    </span>
                  ) : null}
                </li>
              );
            })}
          </ol>
        )}
      </div>

      {files.length ? (
        <div className="mt-[22px] flex shrink-0 flex-col gap-1.5">
          <div className="flex items-baseline gap-2 px-0.5">
            <Label count={files.length}>Files he wrote here</Label>
            <span className="flex-1" />
            {project.directory ? (
              <span className="text-muted-foreground/80 truncate font-mono text-[11px]">{homeShort(project.directory)}</span>
            ) : null}
          </div>
          <ul className="m-0 flex list-none flex-col gap-px p-0">
            {files.slice(0, FILES_SHOWN).map((file) => (
              <li
                key={file.path}
                title={file.at ? `${file.path} — ${dayAndClock(file.at)}` : file.path}
                className="text-foreground/80 flex h-7 items-center gap-2 rounded-[7px] px-2"
              >
                <FileText className="text-muted-foreground/80 size-[13px] shrink-0" />
                <span className="min-w-0 flex-1 truncate font-mono text-[11.5px]">
                  {insideProject(file.path, project.directory)}
                </span>
                <span className="text-muted-foreground/80 shrink-0 font-mono text-[11px] tabular-nums">
                  {file.at ? clock(file.at) : ""}
                </span>
              </li>
            ))}
            {files.length > FILES_SHOWN ? (
              <li className="text-muted-foreground/80 px-2 pt-0.5 text-[11.5px]">
                and {files.length - FILES_SHOWN} more
              </li>
            ) : null}
          </ul>
        </div>
      ) : null}

      <div className="border-border/80 mt-auto flex shrink-0 items-center gap-2 border-t pt-4">
        <button type="button" onClick={onOpen} className={cn(BUTTON.primary, "gap-[7px]")}>
          Open conversation
          <ArrowUpRight className="size-3.5" strokeWidth={2.2} />
        </button>
        {onNew ? (
          <button type="button" onClick={onNew} className={BUTTON.secondary}>
            New chat in this project
          </button>
        ) : null}
        <span className="flex-1" />
        <Kbd>Enter</Kbd>
      </div>
    </section>
  );
}
