import comparisonManifest from "./comparisons.generated.json";

export type VoteChoice = "seestar" | "nightskyai";

export type CaptureComparison = Readonly<{
  captureId: string;
  comparisonId: string;
  target: string;
  seestarImage: string;
  nightskyaiImage: string;
  seestarFrames: number;
  nightskyaiFrames: number;
  nightskyFirstTimestamp: string;
  nightskyLastTimestamp: string;
  nightskyNightCount: number;
  curatedDefault: VoteChoice;
  inputFingerprint: string;
  isGalleryAligned: boolean;
}>;

type PublicComparisonRecord = Readonly<{
  captureId: string;
  comparisonId: string;
  object: string;
  baseline: Readonly<{
    filename: string;
    frames: number;
  }>;
  nightSkyAI: Readonly<{
    filename: string;
    frames: number;
    firstTimestamp: string;
    lastTimestamp: string;
    nightCount: number;
    inputFingerprint: string;
    alignment?: Readonly<{
      mode: string;
      referencePolicy: string;
    }>;
  }>;
  curatedDefault: string;
}>;

function voteChoice(value: string): VoteChoice {
  if (value === "seestar" || value === "nightskyai") return value;
  throw new Error(`Invalid public comparison default: ${value}`);
}

// The immutable export manifest is the single registry for both the page and
// vote API. This prevents media, metadata, and accepted vote IDs from drifting.
export const comparisons: readonly CaptureComparison[] = (
  comparisonManifest.captures as readonly PublicComparisonRecord[]
).map((capture) => ({
  captureId: capture.captureId,
  comparisonId: capture.comparisonId,
  target: capture.object,
  seestarImage: capture.baseline.filename,
  nightskyaiImage: `/comparisons/${capture.nightSkyAI.filename}`,
  seestarFrames: capture.baseline.frames,
  nightskyaiFrames: capture.nightSkyAI.frames,
  nightskyFirstTimestamp: capture.nightSkyAI.firstTimestamp,
  nightskyLastTimestamp: capture.nightSkyAI.lastTimestamp,
  nightskyNightCount: capture.nightSkyAI.nightCount,
  curatedDefault: voteChoice(capture.curatedDefault),
  inputFingerprint: capture.nightSkyAI.inputFingerprint,
  isGalleryAligned:
    capture.nightSkyAI.alignment?.mode === "registered-to-gallery-edit" &&
    capture.nightSkyAI.alignment?.referencePolicy === "gallery-edit-is-immutable",
}));

const comparisonIds = new Set(
  comparisons.map((comparison) => comparison.comparisonId)
);

export function isComparisonId(comparisonId: string): boolean {
  return comparisonIds.has(comparisonId);
}
