import { sql } from "drizzle-orm";
import { index, primaryKey, sqliteTable, text } from "drizzle-orm/sqlite-core";

export const comparisonVotes = sqliteTable(
  "comparison_votes",
  {
    captureId: text("capture_id").notNull(),
    observerHash: text("observer_hash").notNull(),
    choice: text("choice", { enum: ["seestar", "nightskyai"] }).notNull(),
    createdAt: text("created_at").notNull().default(sql`CURRENT_TIMESTAMP`),
    updatedAt: text("updated_at").notNull().default(sql`CURRENT_TIMESTAMP`),
  },
  (table) => [
    primaryKey({ columns: [table.captureId, table.observerHash] }),
    index("comparison_votes_capture_choice_idx").on(
      table.captureId,
      table.choice
    ),
  ]
);
