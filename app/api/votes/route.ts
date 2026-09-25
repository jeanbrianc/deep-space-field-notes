import { and, eq, sql } from "drizzle-orm";

import { getDb } from "../../../db";
import { comparisonVotes } from "../../../db/schema";
import {
  isComparisonId,
  type VoteChoice,
} from "../../comparisons";

const OBSERVER_COOKIE = "night_sky_observer";
const OBSERVER_COOKIE_MAX_AGE_SECONDS = 60 * 60 * 24 * 365;
const OBSERVER_TOKEN_PATTERN = /^[a-f0-9]{64}$/;

type ObserverIdentity = {
  token: string;
  isNew: boolean;
};

type VoteTotals = {
  seestar: number;
  nightskyai: number;
  total: number;
};

type VoteResponse = VoteTotals & {
  comparisonId: string;
  choice: VoteChoice | null;
};

function readCookie(request: Request, name: string): string | undefined {
  const cookieHeader = request.headers.get("cookie");
  if (!cookieHeader) {
    return undefined;
  }

  for (const part of cookieHeader.split(";")) {
    const separator = part.indexOf("=");
    if (separator === -1) {
      continue;
    }
    if (part.slice(0, separator).trim() === name) {
      return part.slice(separator + 1).trim();
    }
  }

  return undefined;
}

function makeObserverToken(): string {
  const bytes = new Uint8Array(32);
  crypto.getRandomValues(bytes);
  return Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join(
    ""
  );
}

function getObserverIdentity(request: Request): ObserverIdentity {
  const candidate = readCookie(request, OBSERVER_COOKIE);
  if (candidate && OBSERVER_TOKEN_PATTERN.test(candidate)) {
    return { token: candidate, isNew: false };
  }
  return { token: makeObserverToken(), isNew: true };
}

async function hashObserverToken(token: string): Promise<string> {
  const digest = await crypto.subtle.digest(
    "SHA-256",
    new TextEncoder().encode(token)
  );
  return Array.from(new Uint8Array(digest), (byte) =>
    byte.toString(16).padStart(2, "0")
  ).join("");
}

function jsonResponse(
  request: Request,
  observer: ObserverIdentity,
  body: unknown,
  status = 200
): Response {
  const headers = new Headers({
    "Cache-Control": "no-store",
    "Content-Type": "application/json; charset=utf-8",
  });

  if (observer.isNew) {
    const secure = new URL(request.url).protocol === "https:" ? "; Secure" : "";
    headers.append(
      "Set-Cookie",
      `${OBSERVER_COOKIE}=${observer.token}; Max-Age=${OBSERVER_COOKIE_MAX_AGE_SECONDS}; Path=/; HttpOnly; SameSite=Lax${secure}`
    );
  }

  return new Response(JSON.stringify(body), { status, headers });
}

function errorResponse(
  request: Request,
  observer: ObserverIdentity,
  message: string,
  status: number
): Response {
  return jsonResponse(request, observer, { error: message }, status);
}

function isVoteChoice(value: unknown): value is VoteChoice {
  return value === "seestar" || value === "nightskyai";
}

async function readVoteResponse(
  comparisonId: string,
  observerHash: string
): Promise<VoteResponse> {
  const db = getDb();
  const grouped = await db
    .select({
      choice: comparisonVotes.choice,
      count: sql<number>`count(*)`,
    })
    .from(comparisonVotes)
    // The existing column stores a versioned comparison ID. Keeping the
    // schema stable preserves old poll rows without counting votes cast on
    // different image bytes or orientations.
    .where(eq(comparisonVotes.captureId, comparisonId))
    .groupBy(comparisonVotes.choice);

  const totals: VoteTotals = { seestar: 0, nightskyai: 0, total: 0 };
  for (const row of grouped) {
    const count = Number(row.count);
    if (row.choice === "seestar" || row.choice === "nightskyai") {
      totals[row.choice] = count;
      totals.total += count;
    }
  }

  const [observerVote] = await db
    .select({ choice: comparisonVotes.choice })
    .from(comparisonVotes)
    .where(
      and(
        eq(comparisonVotes.captureId, comparisonId),
        eq(comparisonVotes.observerHash, observerHash)
      )
    )
    .limit(1);

  return {
    comparisonId,
    ...totals,
    choice: observerVote?.choice ?? null,
  };
}

function publicDatabaseError(error: unknown): string {
  const message = error instanceof Error ? error.message : "";
  if (message.includes("no such table")) {
    return "Voting is being prepared. Please try again shortly.";
  }
  return "Votes are temporarily unavailable. Please try again.";
}

export async function GET(request: Request): Promise<Response> {
  const observer = getObserverIdentity(request);
  const comparisonId = new URL(request.url).searchParams.get("comparisonId") ?? "";

  if (!isComparisonId(comparisonId)) {
    return errorResponse(request, observer, "Unknown comparison.", 404);
  }

  try {
    const observerHash = await hashObserverToken(observer.token);
    const response = await readVoteResponse(comparisonId, observerHash);
    return jsonResponse(request, observer, response);
  } catch (error) {
    return errorResponse(request, observer, publicDatabaseError(error), 503);
  }
}

export async function POST(request: Request): Promise<Response> {
  const observer = getObserverIdentity(request);
  let payload: unknown;

  try {
    payload = await request.json();
  } catch {
    return errorResponse(request, observer, "Send a valid JSON vote.", 400);
  }

  if (!payload || typeof payload !== "object") {
    return errorResponse(request, observer, "Send a valid vote.", 400);
  }

  const { comparisonId, choice } = payload as {
    comparisonId?: unknown;
    choice?: unknown;
  };

  if (typeof comparisonId !== "string" || !isComparisonId(comparisonId)) {
    return errorResponse(request, observer, "Unknown comparison.", 404);
  }
  if (!isVoteChoice(choice)) {
    return errorResponse(
      request,
      observer,
      "Choice must be seestar or nightskyai.",
      400
    );
  }

  try {
    const observerHash = await hashObserverToken(observer.token);
    const db = getDb();
    const now = new Date().toISOString();

    await db
      .insert(comparisonVotes)
      .values({
        captureId: comparisonId,
        observerHash,
        choice,
        createdAt: now,
        updatedAt: now,
      })
      .onConflictDoUpdate({
        target: [comparisonVotes.captureId, comparisonVotes.observerHash],
        set: { choice, updatedAt: now },
      });

    const response = await readVoteResponse(comparisonId, observerHash);
    return jsonResponse(request, observer, response);
  } catch (error) {
    return errorResponse(request, observer, publicDatabaseError(error), 503);
  }
}
