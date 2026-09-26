import comparisonManifest from "./comparisons.generated.json";

export type VoteChoice = "seestar" | "nightskyai";

export type CaptureComparison = Readonly<{
  captureId: string;
  comparisonId: string;
  target: string;
  seestarImage: string;
  nightskyaiImage: string;
  nightskyaiFullFieldImage: string;
  nightskyaiFullFieldRatio: number;
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
    width: number;
    height: number;
    firstTimestamp: string;
    lastTimestamp: string;
    nightCount: number;
    inputFingerprint: string;
    alignment?: Readonly<{
      mode: string;
      referencePolicy: string;
      sourceFilename: string;
      sourceWidth: number;
      sourceHeight: number;
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
).map((capture) => {
  const alignment = capture.nightSkyAI.alignment;
  const fullFieldFilename = alignment?.sourceFilename ?? capture.nightSkyAI.filename;
  const fullFieldWidth = alignment?.sourceWidth ?? capture.nightSkyAI.width;
  const fullFieldHeight = alignment?.sourceHeight ?? capture.nightSkyAI.height;

  return {
    captureId: capture.captureId,
    comparisonId: capture.comparisonId,
    target: capture.object,
    seestarImage: capture.baseline.filename,
    // Keep the registered derivative for reproducible pair review, but show
    // the native portrait source when a single NightSkyAI treatment is public.
    nightskyaiImage: `/comparisons/${capture.nightSkyAI.filename}`,
    nightskyaiFullFieldImage: `/comparisons/${fullFieldFilename}`,
    nightskyaiFullFieldRatio: fullFieldWidth / fullFieldHeight,
    seestarFrames: capture.baseline.frames,
    nightskyaiFrames: capture.nightSkyAI.frames,
    nightskyFirstTimestamp: capture.nightSkyAI.firstTimestamp,
    nightskyLastTimestamp: capture.nightSkyAI.lastTimestamp,
    nightskyNightCount: capture.nightSkyAI.nightCount,
    curatedDefault: voteChoice(capture.curatedDefault),
    inputFingerprint: capture.nightSkyAI.inputFingerprint,
    isGalleryAligned:
      alignment?.mode === "registered-to-gallery-edit" &&
      alignment?.referencePolicy === "gallery-edit-is-immutable",
  };
});

const comparisonIds = new Set(
  comparisons.map((comparison) => comparison.comparisonId)
);

export function isComparisonId(comparisonId: string): boolean {
  return comparisonIds.has(comparisonId);
}
