import assert from "node:assert/strict";
import { readFile, readdir } from "node:fs/promises";
import { DatabaseSync } from "node:sqlite";
import test from "node:test";

const root = new URL("../", import.meta.url);

test("vote migration supports one changeable vote per capture and observer", async () => {
  const migrationNames = (await readdir(new URL("drizzle/", root))).filter(
    (name) => name.endsWith(".sql")
  );
  assert.equal(migrationNames.length, 1);

  const migration = await readFile(
    new URL(`drizzle/${migrationNames[0]}`, root),
    "utf8"
  );
  const database = new DatabaseSync(":memory:");

  for (const statement of migration.split("--> statement-breakpoint")) {
    database.exec(statement);
  }

  const indexColumns = database
    .prepare("PRAGMA index_info('comparison_votes_capture_choice_idx')")
    .all()
    .map((column) => column.name);
  assert.deepEqual(indexColumns, ["capture_id", "choice"]);

  const upsert = database.prepare(`
    INSERT INTO comparison_votes (
      capture_id, observer_hash, choice, created_at, updated_at
    ) VALUES (?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
    ON CONFLICT(capture_id, observer_hash) DO UPDATE SET
      choice = excluded.choice,
      updated_at = excluded.updated_at
  `);

  upsert.run("sh2-142", "observer-one-hash", "seestar");
  upsert.run("sh2-142", "observer-one-hash", "nightskyai");
  upsert.run("sh2-142", "observer-two-hash", "seestar");

  const rows = database
    .prepare(`
      SELECT choice, count(*) AS total
      FROM comparison_votes
      WHERE capture_id = ?
      GROUP BY choice
      ORDER BY choice
    `)
    .all("sh2-142")
    .map(({ choice, total }) => ({ choice, total }));

  assert.deepEqual(rows, [
    { choice: "nightskyai", total: 1 },
    { choice: "seestar", total: 1 },
  ]);
});

test("vote endpoint keeps observer identity private and long-lived", async () => {
  const route = await readFile(new URL("app/api/votes/route.ts", root), "utf8");

  assert.match(route, /crypto\.getRandomValues/);
  assert.match(route, /crypto\.subtle\.digest/);
  assert.match(route, /HttpOnly/);
  assert.match(route, /SameSite=Lax/);
  assert.match(route, /Max-Age=/);
  assert.match(route, /observerHash/);
  assert.doesNotMatch(route, /observerToken:\s*text/);
});
