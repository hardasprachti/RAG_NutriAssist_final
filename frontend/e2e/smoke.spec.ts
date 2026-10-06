import { expect, test, type Page } from "@playwright/test";

const ask = async (page: Page, text: string) => {
  await page.getByLabel("Your question").fill(text);
  await page.keyboard.press("Enter");
};
const answers = (page: Page) => page.locator(".assistant-bubble .answer");
const newChat = (page: Page) => page.getByRole("button", { name: /New Nutrition Chat/ });

test.beforeEach(async ({ page }) => {
  page.on("dialog", (dialog) => dialog.accept());
  await page.goto("/");
  await expect(page.getByText("What would you like to know?")).toBeVisible();
});

test("ask → answer → open the cited source; the sources survive a reload", async ({ page }) => {
  await ask(page, "How long can raw chicken stay in the fridge?");
  await expect(answers(page).first()).toBeVisible();

  await page.locator(".badge").first().click();
  const card = page.locator(".source-card.selected");
  await expect(card).toBeVisible();
  await expect(card.locator("blockquote")).not.toBeEmpty(); // the supporting excerpt
  await expect(card.locator(".chip-cited")).toBeVisible();
  await expect(card.getByRole("link", { name: /View source/ })).toHaveAttribute("href", /^https?:\/\//);

  await page.reload();
  await expect(answers(page).first()).toBeVisible();
  await expect(page.locator(".source-card").first()).toBeVisible();
});

test("a restricted question gets the out-of-scope refusal, with no sources", async ({ page }) => {
  await ask(page, "How many calories should I eat to lose weight?");
  const banner = page.getByTestId("refusal-out_of_scope");
  await expect(banner).toBeVisible();
  await expect(banner).toContainText(/dietitian|doctor|healthcare/i);
  await expect(page.locator(".source-card")).toHaveCount(0);
});

test("chats are independent: ask in one, open another while it answers, nothing leaks", async ({ page }) => {
  await ask(page, "How long can eggs stay in the fridge?");
  await expect(page.getByText("Checking the official sources")).toBeVisible();

  await newChat(page).click();
  await expect(page.getByText("What would you like to know?")).toBeVisible();
  await expect(page.getByLabel("Your question")).toBeEnabled();
  await ask(page, "What does WHO recommend about free sugars?");
  await expect(answers(page).first()).toBeVisible();
  await expect(page.locator(".bubble").first()).toContainText("free sugars");
  await expect(page.locator(".chat-list")).not.toContainText("Answering");

  await page.locator(".chat-open", { hasText: "eggs" }).click();
  await expect(answers(page).first()).toBeVisible();
  await expect(page.locator(".messages")).not.toContainText("free sugars");

  // rename + delete only touch that chat
  await page.locator(".chat-item", { hasText: "eggs" }).hover();
  await page.getByRole("button", { name: /^Rename/ }).click();
  await page.getByLabel("Chat title").fill("Chicken storage");
  await page.keyboard.press("Enter");
  await expect(page.locator(".chat-title", { hasText: "Chicken storage" })).toBeVisible();

  await page.locator(".chat-item", { hasText: "Chicken storage" }).hover();
  await page.getByRole("button", { name: /^Delete/ }).click();
  await expect(page.locator(".chat-title", { hasText: "Chicken storage" })).toHaveCount(0);
  await expect(page.locator(".chat-title", { hasText: "free sugars" })).toHaveCount(1);
});

test.describe("responsive layout (breakpoints from the design)", () => {
  const sizes = {
    desktop: { width: 1440, height: 900 },
    laptop: { width: 1100, height: 800 },
    tablet: { width: 820, height: 1000 },
    phone: { width: 390, height: 780 },
  };

  const noHorizontalScroll = (page: Page) =>
    page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth);

  for (const [name, size] of Object.entries(sizes)) {
    test(`${name} (${size.width}px)`, async ({ page }) => {
      await page.setViewportSize(size);
      await page.reload();
      await expect(page.getByLabel("Your question")).toBeVisible();
      expect(await noHorizontalScroll(page)).toBe(true);

      if (name === "desktop") {
        await expect(page.locator(".sidebar")).toBeVisible();
        await expect(page.locator(".sources")).toBeVisible();
        expect((await page.locator(".sidebar").boundingBox())?.width).toBe(220);
        expect((await page.locator(".sources").boundingBox())?.width).toBe(320);
      }
      if (name === "laptop") {
        expect((await page.locator(".sidebar").boundingBox())?.width).toBe(200);
        await expect(page.locator(".sources")).toBeHidden();
        await page.getByRole("button", { name: /Evidence sources/ }).click();
        await expect(page.locator(".sources")).toBeVisible();
        await page.locator(".scrim").click({ position: { x: 20, y: 300 } });
        await expect(page.locator(".sources")).toBeHidden();
      }
      if (name === "tablet") {
        await expect(page.locator(".rail")).toBeVisible();
        await expect(page.locator(".sidebar")).toBeHidden();
        await page.getByRole("button", { name: "Chat history" }).click();
        await expect(page.locator(".sidebar")).toBeVisible();
      }
      if (name === "phone") {
        await expect(page.getByLabel("Show chats")).toBeVisible();
        await expect(page.locator(".rail")).toBeHidden();
        const send = await page.getByLabel("Send question").boundingBox();
        expect(send!.width).toBeGreaterThanOrEqual(44);
        expect(send!.height).toBeGreaterThanOrEqual(44);
        await page.getByLabel("Show chats").click();
        await expect(newChat(page)).toBeVisible();
      }
    });
  }
});

test("the conversation is announced to screen readers and every icon button has a name", async ({ page }) => {
  await expect(page.locator('[role="log"][aria-live="polite"]')).toHaveCount(1);
  const unnamed = await page.evaluate(
    () => [...document.querySelectorAll("button")].filter((b) => !b.textContent?.trim() && !b.getAttribute("aria-label") && !b.title).length,
  );
  expect(unnamed).toBe(0);
});
