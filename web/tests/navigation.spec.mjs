import { expect, test } from "@playwright/test";

const repairedLink = "/#case=CMP-02";
const regressionLink = "/#comparison=regressed&case=CMP-02";
const question = "I have a Desk Display 18. Does the Plate C tabletop mount fit?";

function investigation(page) {
  return page.getByRole("heading", { name: "Case investigation", level: 2 });
}

function inspectLink(page) {
  return page.getByRole("link", { name: "Inspect CMP-02", exact: true });
}

async function expectGate(page, title, verdict) {
  await expect(page.getByRole("heading", { name: title, level: 1 })).toBeVisible();
  await expect(page.getByRole("region", { name: "Release gate" })
    .getByText(verdict, { exact: true })).toBeVisible();
}

async function expectOverviewAtTop(page) {
  await expect(investigation(page)).toBeHidden();
  await expect(page.getByRole("region", { name: "Case results table" })
    .locator('[aria-current="true"]')).toHaveCount(0);
  await expect(page.locator("body")).toBeFocused();
  const scroll = await page.evaluate(() => [window.scrollX, window.scrollY]);
  expect(scroll).toEqual([0, 0]);
}

test("the bare console opens the repaired overview with no selected case", async ({ page }) => {
  await page.goto("/");

  await expectGate(page, "Repaired candidate", "PASS");
  await expect(page.getByRole("link", { name: /^Inspect / })).toHaveCount(0);
  await expectOverviewAtTop(page);
});

test("a cold case-only link stays at the top until Inspect is activated with Enter", async ({ page }) => {
  await page.goto(repairedLink);

  await expectGate(page, "Repaired candidate", "PASS");
  await expect(inspectLink(page)).toBeInViewport();
  await expectOverviewAtTop(page);
  await expect(page).toHaveURL(repairedLink);

  await inspectLink(page).focus();
  await page.keyboard.press("Enter");

  await expect(investigation(page)).toBeFocused();
  await expect(investigation(page)).toBeInViewport();
  await expect(page.getByRole("region", { name: "Case investigation" }).getByText(question)).toBeVisible();
});

test("the explicit seeded run shows the recorded CMP-02 failure after Inspect is clicked", async ({ page }) => {
  await page.goto(regressionLink);

  await expectGate(page, "Seeded regression", "FAIL");
  await expect(inspectLink(page)).toBeInViewport();
  await expectOverviewAtTop(page);

  await inspectLink(page).click();

  await expect(investigation(page)).toBeFocused();
  const evidence = page.getByRole("region", { name: "Case investigation" });
  await expect(evidence.getByText(question)).toBeVisible();
  const baselineOutput = evidence.getByLabel("Baseline raw provider output");
  await expect(baselineOutput).toContainText("cmp-mount");
  await expect(baselineOutput).not.toContainText("It also supports every other Plate C accessory.");
  await expect(evidence.getByLabel("Candidate raw provider output"))
    .toContainText("It also supports every other Plate C accessory.");
  await expect(evidence.getByText("missing ['cmp-mount'], unexpected ['cmp-cart']")).toBeVisible();
  await expect(evidence.getByText(/Expected approved wording/)).toBeVisible();
});

test("a current comparison opens by exact artifact ID", async ({ page, request }) => {
  const response = await request.get("/data/index.json");
  await expect(response).toBeOK();
  const { comparisons } = await response.json();
  const seeded = comparisons.find((comparison) => comparison.scenario === "regressed");
  const params = new URLSearchParams({ comparison: seeded.id, case: "CMP-02" });

  await page.goto(`/#${params}`);

  await expectGate(page, "Seeded regression", "FAIL");
  await expect(inspectLink(page)).toBeInViewport();
  await expectOverviewAtTop(page);
});

test("a published legacy link offers the current example and preserves its case", async ({ page }) => {
  await page.goto("/#comparison=fixture-bca00797df094798b99c2fa7&case=CMP-02");

  await expect(page.getByRole("heading", { name: "Earlier demo comparison", level: 1 })).toBeVisible();
  await page.getByRole("link", { name: "Open current seeded example" }).click();

  await expect(page).toHaveURL(regressionLink);
  await expectGate(page, "Seeded regression", "FAIL");
  await expect(inspectLink(page)).toBeInViewport();
  await expect(investigation(page)).toBeHidden();
});

test("switching runs from a scrolled investigation reveals the new overview", async ({ page }) => {
  await page.goto(regressionLink);
  await inspectLink(page).click();
  await expect(investigation(page)).toBeFocused();
  expect(await page.evaluate(() => window.scrollY)).toBeGreaterThan(0);

  await page.getByRole("navigation", { name: "Run history" })
    .getByRole("link", { name: /Repaired candidate/ }).click();

  await expectGate(page, "Repaired candidate", "PASS");
  await expect(investigation(page)).toBeHidden();
  await expect(page.getByRole("heading", { name: "Repaired candidate", level: 1 })).toBeInViewport();
});

test("case clicks focus evidence and Back/Forward restore the address without opening it", async ({ page }) => {
  await page.goto("/");

  const cases = page.getByRole("region", { name: "Case results table" });
  const rowLink = cases.getByRole("link", { name: /^CMP-02\b/ });
  await rowLink.click();
  await expect(investigation(page)).toBeFocused();
  await expect(rowLink).toHaveAttribute("aria-current", "true");
  await expect(page).toHaveURL(/[?&#]case=CMP-02$/);

  await page.goBack();
  await expect(page).toHaveURL("/");
  await expect(investigation(page)).toBeHidden();
  await expect(inspectLink(page)).toHaveCount(0);

  await page.goForward();
  await expect(inspectLink(page)).toBeVisible();
  await expect(investigation(page)).toBeHidden();

  await rowLink.click();
  await expect(investigation(page)).toBeFocused();
  await rowLink.click();
  await expect(investigation(page)).toBeFocused();
});

test("an unknown case is reported without choosing a substitute", async ({ page }) => {
  await page.goto("/#case=CMP-MISSING");

  await expectGate(page, "Repaired candidate", "PASS");
  await expect(page.getByRole("status").filter({ hasText: "CMP-MISSING" })).toBeVisible();
  await expect(page.getByRole("link", { name: /^Inspect / })).toHaveCount(0);
  await expectOverviewAtTop(page);
});

test("an unknown comparison offers a path back to the repaired overview", async ({ page }) => {
  await page.goto("/#comparison=missing&case=CMP-02");

  await expect(page.getByRole("heading", { name: "Comparison unavailable", level: 1 })).toBeVisible();
  await page.getByRole("link", { name: "Open first comparison" }).click();
  await expectGate(page, "Repaired candidate", "PASS");
  await expect(investigation(page)).toBeHidden();
});
