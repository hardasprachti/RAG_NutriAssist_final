import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach, vi } from "vitest";

// jsdom has no layout, so no scrolling.
Element.prototype.scrollIntoView = vi.fn();

afterEach(() => {
  cleanup();
  window.history.replaceState(null, "", "/");
});
