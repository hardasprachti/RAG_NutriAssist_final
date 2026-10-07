"use client";

import { useState } from "react";
import { chatPhase, UNTITLED, useChats } from "@/hooks/useChats";
import ChatWindow from "./ChatWindow";
import ConversationSidebar from "./ConversationSidebar";
import { BookIcon, BowlIcon, HistoryIcon, MenuIcon, PlusIcon } from "./icons";
import SourcesPanel from "./SourcesPanel";

/** Desktop shows the sources column unless collapsed; narrower screens show it as a drawer only when opened. */
const DESKTOP_QUERY = "(min-width: 1280px)";

type SourcesView = "open" | "closed" | null; // null: the default for the screen size

export default function ChatPage() {
  const chats = useChats();
  const { active } = chats;
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [sourcesView, setSourcesView] = useState<SourcesView>(null);

  // The evidence shown: the answer whose badge was clicked, else the newest answer that has any.
  const withSources = active.messages.filter((m) => m.role === "assistant" && m.retrieved_sources.length > 0);
  const shown =
    active.messages.find((m) => m.id === active.selection?.messageId) ?? withSources[withSources.length - 1] ?? null;
  const sourceCount = shown?.retrieved_sources.length ?? 0;
  const phase = chatPhase(active);

  const closeDrawers = () => {
    setSidebarOpen(false);
    // Below desktop the sources panel is a drawer; on desktop it is a column the user keeps as it was.
    if (!window.matchMedia(DESKTOP_QUERY).matches) setSourcesView((v) => (v === "open" ? "closed" : v));
  };

  const toggleSources = () =>
    setSourcesView((view) => {
      const isOpen = view ? view === "open" : window.matchMedia(DESKTOP_QUERY).matches;
      return isOpen ? "closed" : "open";
    });

  const startNewChat = () => {
    chats.newChat();
    closeDrawers();
  };

  return (
    <div className="app" data-sidebar={sidebarOpen} data-sources={sourcesView ?? "default"} data-phase={phase}>
      {/* Tablet: a slim rail instead of the full chat list. */}
      <nav className="rail" aria-label="Quick actions">
        <span className="brand-mark" aria-hidden="true">
          <BowlIcon />
        </span>
        <button type="button" className="rail-btn primary" title="Start a new chat" aria-label="New chat" onClick={startNewChat}>
          <PlusIcon />
        </button>
        <button type="button" className="rail-btn" title="Chat history" aria-label="Chat history" onClick={() => setSidebarOpen(true)}>
          <HistoryIcon />
        </button>
      </nav>

      <aside className="sidebar" aria-label="Chat history">
        <ConversationSidebar
          items={chats.sidebar}
          status={chats.listStatus}
          onNew={startNewChat}
          onOpen={(key) => {
            chats.open(key);
            closeDrawers();
          }}
          onRename={chats.rename}
          onDelete={chats.remove}
          onRetry={chats.refreshList}
        />
      </aside>

      <main className="chat">
        <header className="chat-header">
          <button type="button" className="icon-btn menu" aria-label="Show chats" onClick={() => setSidebarOpen(true)}>
            <MenuIcon />
          </button>
          <span className="wordmark">
            <BowlIcon /> NutriAI
          </span>
          <div className="chat-title-block">
            <h1>{active.title ?? UNTITLED}</h1>
            <p className="muted">Answers cited from the source documents</p>
          </div>
          <button
            type="button"
            className="pill-btn sources-toggle"
            aria-label={`Evidence sources${sourceCount > 0 ? `, ${sourceCount}` : ""}`}
            onClick={toggleSources}
          >
            <BookIcon /> <span className="label-long">Evidence Panel</span>
            <span className="label-short">Sources</span>
            {sourceCount > 0 && <span className="count">{sourceCount}</span>}
          </button>
        </header>

        {chats.notice && (
          <div className="notice" role="alert">
            {chats.notice}
            <button type="button" className="link" onClick={chats.dismissNotice}>
              Dismiss
            </button>
          </div>
        )}

        <ChatWindow
          chat={active}
          onSend={chats.send}
          onInput={chats.setInput}
          onRetry={chats.retry}
          onRetryHistory={chats.retryHistory}
          onCite={(messageId, chunkId) => {
            chats.select({ messageId, chunkId });
            setSourcesView("open");
          }}
        />
      </main>

      <aside className="sources" aria-label="Evidence sources">
        <SourcesPanel
          message={shown}
          chunkId={active.selection && active.selection.messageId === shown?.id ? active.selection.chunkId : null}
          onClose={() => setSourcesView("closed")}
        />
      </aside>

      <div className="scrim" onClick={closeDrawers} aria-hidden="true" />
    </div>
  );
}
