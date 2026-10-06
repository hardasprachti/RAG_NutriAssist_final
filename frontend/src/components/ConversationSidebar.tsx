"use client";

import { useState } from "react";
import type { SidebarItem } from "@/hooks/useChats";
import { relativeTime, useNow } from "@/lib/format";
import { BowlIcon, PencilIcon, PlusIcon, TrashIcon } from "./icons";

interface Props {
  items: SidebarItem[];
  status: "loading" | "ready" | "error";
  onNew: () => void;
  onOpen: (key: string) => void;
  onRename: (conversationId: string, title: string) => void;
  onDelete: (conversationId: string) => void;
  onRetry: () => void;
}

export default function ConversationSidebar({ items, status, onNew, onOpen, onRename, onDelete, onRetry }: Props) {
  const [editing, setEditing] = useState<string | null>(null);
  const [draftTitle, setDraftTitle] = useState("");
  const now = useNow();

  const finishRename = (item: SidebarItem, save: boolean) => {
    setEditing(null);
    if (save && item.conversationId && draftTitle.trim() && draftTitle.trim() !== item.title) {
      onRename(item.conversationId, draftTitle);
    }
  };

  return (
    <div className="sidebar-inner">
      <div className="brand">
        <span className="brand-mark">
          <BowlIcon />
        </span>
        <span>
          <span className="brand-name">NutriAI</span>
          <span className="brand-tag">Evidence Bot</span>
        </span>
      </div>

      <button type="button" className="btn-primary new-chat" onClick={onNew}>
        <PlusIcon /> <span>New Nutrition Chat</span>
      </button>

      <div className="section-label">Recent Chats</div>
      <nav aria-label="Chats" className="chat-list">
        {status === "loading" && items.length === 0 && <p className="muted pad">Loading chats…</p>}
        {status === "error" && (
          <p className="muted pad">
            Could not load your chats.{" "}
            <button type="button" className="link" onClick={onRetry}>
              Retry
            </button>
          </p>
        )}
        {status === "ready" && items.length === 0 && <p className="muted pad">Your chats will appear here.</p>}
        <ul>
          {items.map((item) => (
            <li key={item.key} className={`chat-item${item.active ? " active" : ""}`}>
              {editing === item.key ? (
                <input
                  className="rename-input"
                  aria-label="Chat title"
                  value={draftTitle}
                  maxLength={255}
                  autoFocus
                  onChange={(e) => setDraftTitle(e.target.value)}
                  onBlur={() => finishRename(item, true)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") finishRename(item, true);
                    if (e.key === "Escape") finishRename(item, false);
                  }}
                />
              ) : (
                <>
                  <button
                    type="button"
                    className="chat-open"
                    aria-current={item.active ? "page" : undefined}
                    title={item.title}
                    onClick={() => onOpen(item.key)}
                  >
                    <span className="chat-title">{item.title}</span>
                    <span className="chat-sub">
                      {item.pending ? (
                        <>
                          <span className="spinner" role="status" aria-label="Answering" /> Answering…
                        </>
                      ) : item.updatedAt ? (
                        relativeTime(item.updatedAt, now)
                      ) : null}
                    </span>
                  </button>
                  {item.conversationId && (
                    <span className="chat-actions">
                      <button
                        type="button"
                        aria-label={`Rename ${item.title}`}
                        title="Rename"
                        onClick={() => {
                          setDraftTitle(item.title);
                          setEditing(item.key);
                        }}
                      >
                        <PencilIcon />
                      </button>
                      <button
                        type="button"
                        aria-label={`Delete ${item.title}`}
                        title="Delete"
                        onClick={() => {
                          if (window.confirm(`Delete "${item.title}"? This cannot be undone.`)) {
                            onDelete(item.conversationId!);
                          }
                        }}
                      >
                        <TrashIcon />
                      </button>
                    </span>
                  )}
                </>
              )}
            </li>
          ))}
        </ul>
      </nav>

      <p className="sidebar-foot">Answers come only from WHO, USDA, FDA, the UK Eatwell Guide, EFSA and ICMR-NIN guidance.</p>
    </div>
  );
}
