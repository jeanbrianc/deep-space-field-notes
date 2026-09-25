"use client";

import { useCallback, useEffect, useMemo, useRef, useState, type CSSProperties } from "react";
import { comparisons, type CaptureComparison, type VoteChoice } from "./comparisons";

const imageFiles = [
  "Stacked_101_IC 443_10.0s_LP_20260303-213610_cleaned.jpg",
  "Stacked_114_IC 1318A_10.0s_LP_20250821-221720_cleaned.jpg",
  "Stacked_124_M 92_10.0s_IRCUT_20250915-210455_cleaned.jpg",
  "Stacked_136_C 34_20.0s_IRCUT_20260924-001000_cleaned.jpg",
  "Stacked_144_NGC 5907_10.0s_IRCUT_20250915-213552_cleaned.jpg",
  "Stacked_144_SH2-142_20.0s_LP_20260923-230000_cleaned.jpg",
  "Stacked_150_NGC 281_10.0s_LP_20251123-203503_cleaned.jpg",
  "Stacked_151_Unknown_10.0s_IRCUT_20250606-231447_cleaned.jpg",
  "Stacked_153_IC 5146_10.0s_LP_20251009-214559_cleaned.jpg",
  "Stacked_159_M 27_10.0s_LP_20250916-211701_cleaned.jpg",
  "Stacked_162_M 81_10.0s_IRCUT_20260422-220323_hand_processed.jpg",
  "Stacked_163_C 27_10.0s_IRCUT_20260922-210708_cleaned.jpg",
  "Stacked_184_IC 5070_10.0s_LP_20250915-221725_hand_processed.png",
  "Stacked_185_M 101_10.0s_IRCUT_20260602-230131_hand_processed.jpg",
  "Stacked_187_IC 1396A_10.0s_LP_20250821-225811_cleaned.jpg",
  "Stacked_189_NGC 6946_10.0s_IRCUT_20251009-211135_cleaned.jpg",
  "Stacked_191_M 33_10.0s_IRCUT_20251123-200213_cleaned.jpg",
  "Stacked_201_M 31_10.0s_IRCUT_20251118-191752_hand_processed.png",
  "Stacked_223_M 42_10.0s_LP_20260407-215641_hand_processed.jpg",
  "Stacked_232_C 31_10.0s_IRCUT_20251223-211803_cleaned.jpg",
  "Stacked_240_NGC 6992_10.0s_LP_20250925-213428_cleaned.jpg",
  "Stacked_240_NGC 7023_10.0s_IRCUT_20250925-222538_cleaned.jpg",
  "Stacked_269_NGC 6888_10.0s_LP_20250831-224513_hand_processed.png",
  "Stacked_277_NGC 6960_10.0s_LP_20260922-221229_cleaned.jpg",
  "Stacked_419_M 51_10.0s_IRCUT_20260728-233258_hand_processed.png",
  "Stacked_61_mosaic_M 31_10.0s_IRCUT_20251223-200629_hand_processed.png",
  "Stacked_62_M 102_10.0s_IRCUT_20250606-223915_cleaned.jpg",
  "Stacked_63_NGC 7331_10.0s_IRCUT_20251009-220208_cleaned.jpg",
  "Stacked_64_IC 1318B_10.0s_LP_20250925-203715_cleaned.jpg",
  "Stacked_71_M 13_10.0s_IRCUT_20260724-223913_cleaned.jpg",
  "Stacked_76_M 3_10.0s_IRCUT_20250717-224225_cleaned.jpg",
  "Stacked_79_M 29_10.0s_IRCUT_20250925-225032_cleaned.jpg",
  "Stacked_86_M 97_10.0s_LP_20250524-232111_cleaned.jpg",
];

const imageRatios: Record<string, number> = {
  "Stacked_162_M 81_10.0s_IRCUT_20260422-220323_hand_processed.jpg": 833 / 1708,
  "Stacked_184_IC 5070_10.0s_LP_20250915-221725_hand_processed.png": 899 / 1736,
  "Stacked_185_M 101_10.0s_IRCUT_20260602-230131_hand_processed.jpg": 956 / 1237,
  "Stacked_201_M 31_10.0s_IRCUT_20251118-191752_hand_processed.png": 373 / 664,
  "Stacked_223_M 42_10.0s_LP_20260407-215641_hand_processed.jpg": 846 / 1814,
  "Stacked_269_NGC 6888_10.0s_LP_20250831-224513_hand_processed.png": 858 / 1619,
  "Stacked_419_M 51_10.0s_IRCUT_20260728-233258_hand_processed.png": 443 / 706,
  "Stacked_61_mosaic_M 31_10.0s_IRCUT_20251223-200629_hand_processed.png": 373 / 664,
};

const commonNames: Record<string, string> = {
  "IC 443": "Jellyfish Nebula", "M 45": "Pleiades", "IC 1318A": "Gamma Cygni Nebula", "M 92": "Messier 92",
  Vega: "Vega", "C 34": "Western Veil Nebula", "NGC 5907": "Splinter Galaxy",
  "SH2-142": "Wizard Nebula", "C 39": "Eskimo Nebula", "NGC 281": "Pacman Nebula", Unknown: "Uncharted Field",
  "IC 5146": "Cocoon Nebula", "M 27": "Dumbbell Nebula", "M 81": "Bode’s Galaxy", "C 27": "Crescent Nebula",
  "IC 5070": "Pelican Nebula", "M 101": "Pinwheel Galaxy", "IC 1396A": "Elephant Trunk Nebula", "NGC 6946": "Fireworks Galaxy",
  "M 33": "Triangulum Galaxy", "M 31": "Andromeda Galaxy", "NGC 7640": "NGC 7640", "M 42": "Orion Nebula",
  "C 31": "Flaming Star Nebula", "NGC 6992": "Eastern Veil Nebula", "NGC 7023": "Iris Nebula", "M 109": "Messier 109",
  "NGC 6888": "Crescent Nebula", "NGC 6960": "Western Veil Nebula", "SH2-235": "Sh2-235", "NGC 5033": "NGC 5033",
  Arcturus: "Arcturus", "C 22": "Blue Snowball Nebula", Jupiter: "Jupiter", "M 51": "Whirlpool Galaxy",
  "M 108": "Surfboard Galaxy", "M 110": "Messier 110", "M 100": "Mirror Galaxy", "NGC 4631": "Whale Galaxy",
  "M 102": "Spindle Galaxy", "NGC 7331": "NGC 7331", "IC 1318B": "Butterfly Nebula", Uranus: "Uranus",
  "M 13": "Great Hercules Cluster", "M 3": "Messier 3", "M 29": "Cooling Tower Cluster", "M 97": "Owl Nebula",
};

const facts: Record<string, string> = {
  "IC 443": "Nicknamed the Jellyfish Nebula, IC 443 is the expanding debris of a shattered star in Gemini. Its tangled shock fronts meet nearby molecular clouds, heating gas that glows across visible, radio, and X-ray wavelengths.",
  "IC 1318A": "IC 1318A is part of a broad emission-nebula complex around Sadr in Cygnus. Ultraviolet light energizes hydrogen while dark foreground dust divides the glow into sweeping lanes, giving this crowded Milky Way field its dramatic texture.",
  "M 92": "M 92 is an ancient globular cluster orbiting in the Milky Way’s halo. Its tightly concentrated core contains generations of old, metal-poor stars, offering astronomers a luminous fossil record of the Galaxy’s earliest chapters.",
  "C 34": "Caldwell 34, the Western Veil Nebula, is one luminous arc of the much larger Cygnus Loop. Delicate filaments mark places where an expanding supernova shock encounters surrounding gas, compressing and heating it until it shines.",
  "NGC 5907": "NGC 5907 is a spiral galaxy presented almost perfectly edge-on in Draco. That viewing angle compresses its broad stellar disk into a slender streak, while a dark dust lane traces the cold material from which future stars may form.",
  "SH2-142": "Sh2-142, commonly called the Wizard Nebula, surrounds the young cluster NGC 7380 in Cepheus. Radiation and stellar winds from hot newborn stars illuminate and erode the surrounding hydrogen and dust into a richly structured cloud.",
  "NGC 281": "NGC 281 is a star-forming emission nebula in Cassiopeia, better known as the Pacman Nebula. Bright hydrogen frames dark, dense globules where gas and dust can collapse, while a young stellar cluster lights the larger cloud.",
  Unknown: "This frame arrived without a reliable target identification, so it remains an honest uncharted field. Its pattern of stars still records a real patch of the northern sky, inviting comparison with a star atlas before any name is assigned.",
  "IC 5146": "The Cocoon Nebula combines glowing hydrogen, reflected starlight, and obscuring dust around a young cluster in Cygnus. The dark cloud Barnard 168 trails away from it, marking a cold reservoir of material for future star formation.",
  "M 27": "The Dumbbell Nebula is the expanding outer atmosphere of a dying, Sun-like star in Vulpecula. Its hot central remnant energizes the cast-off gas, producing layered colors and a shape whose appearance changes with wavelength and exposure.",
  "M 81": "Bode’s Galaxy is a nearby grand-design spiral in Ursa Major. Its crisp arms, dusty lanes, and bright core have been shaped in part by gravitational encounters with M82 and NGC 3077, fellow members of the M81 Group.",
  "C 27": "The Crescent Nebula is a wind-blown shell around the Wolf–Rayet star WR 136. A fast stellar wind is overtaking material shed during an earlier phase, compressing it into luminous arcs of ionized gas in Cygnus.",
  "IC 5070": "The Pelican Nebula is an emission cloud in Cygnus, beside the North America Nebula and divided from it by a dark lane of dust. Ultraviolet light carves bright ionization fronts where new stars continue to form.",
  "M 101": "The Pinwheel Galaxy is a broad, nearly face-on spiral in Ursa Major. Its uneven arms and bright star-forming knots likely reflect gravitational encounters with smaller neighbors, making its delicate symmetry visibly lopsided.",
  "IC 1396A": "The Elephant Trunk is a dense pillar of gas and dust silhouetted within the larger IC 1396 emission region in Cepheus. Radiation from nearby massive stars erodes its rim while compressed pockets shelter young stars.",
  "NGC 6946": "The Fireworks Galaxy is a face-on spiral seen through the star-rich plane of our own Milky Way. Its nickname reflects the unusual number of stellar explosions recorded there, alongside vigorous star formation across its arms.",
  "M 33": "The Triangulum Galaxy is a nearby spiral and one of the Local Group’s major members. Its loosely wound arms contain immense nurseries such as NGC 604, where hot young stars energize vast clouds of glowing hydrogen.",
  "M 31": "Andromeda is the nearest major spiral galaxy to the Milky Way. Dark dust lanes cross its tilted disk, blue regions mark young stars, and several smaller satellites accompany it as gravity draws it toward a distant encounter with our galaxy.",
  "M 42": "The Orion Nebula is a nearby stellar nursery visible to the unaided eye beneath Orion’s Belt. Young stars in the Trapezium cluster flood the surrounding gas with ultraviolet light, revealing bright folds, dust, and newborn systems.",
  "C 31": "The Flaming Star Nebula surrounds the runaway star AE Aurigae, whose ultraviolet light ionizes hydrogen while starlight scattered by dust adds a blue reflection glow. The star and cloud appear to be a chance encounter in Auriga.",
  "NGC 6992": "The Eastern Veil is a luminous segment of the Cygnus Loop, the expanding remains of a long-ago supernova. Its fine filaments trace shock waves heating interstellar gas, which cools and radiates in distinct colors.",
  "NGC 7023": "The Iris Nebula is a reflection nebula in Cepheus, where dust around a young star scatters its light into cool blue tones. Dense surrounding clouds absorb background starlight, framing the luminous center with dark, textured petals.",
  "NGC 6888": "The Crescent Nebula is a wind-blown shell around a massive Wolf–Rayet star in Cygnus. Fast stellar winds overtake material shed during an earlier phase, creating shocks that excite the tangled arcs of hydrogen and oxygen.",
  "NGC 6960": "NGC 6960 forms the western sweep of the Veil Nebula, the expanding remains of a stellar explosion in Cygnus. Its broom-like filaments trace a shock wave moving through thin interstellar gas; the bright nearby star is foreground.",
  "M 51": "The Whirlpool Galaxy displays a grand spiral pattern shaped in part by its smaller companion, NGC 5195. Their gravitational encounter gathers gas along the arms, encouraging new stars while a faint bridge of material links the pair.",
  "M 102": "Messier 102 has a famously uncertain historical identity and is commonly associated with the Spindle Galaxy, NGC 5866. Seen nearly edge-on, its smooth lenticular glow is divided by a sharp lane of cool, light-blocking dust.",
  "NGC 7331": "NGC 7331 is a large spiral galaxy in Pegasus, viewed at an angle that reveals both its bright central bulge and dusty disk. Several smaller galaxies share the field of view, though their apparent closeness can be misleading.",
  "IC 1318B": "IC 1318B belongs to the sprawling Gamma Cygni emission complex in Cygnus. Energetic starlight makes hydrogen glow, while a broad foreground dust lane blocks part of the light and carves the luminous cloud into wing-like forms.",
  "M 13": "The Great Hercules Cluster is a vast, gravitationally bound gathering of ancient stars in the Milky Way’s halo. Its crowded core softens into branching streams of points, revealing structure that sharper skies gradually resolve.",
  "M 3": "M 3 is an old globular cluster traveling through the Milky Way’s halo in Canes Venatici. Its rich population of pulsating variable stars helps astronomers test stellar evolution and compare distances across our Galaxy.",
  "M 29": "M 29 is a young open cluster set against the crowded star fields of Cygnus. Its hot blue-white members formed from the same cloud and still travel together, while intervening Milky Way dust dims and reddens their light.",
  "M 97": "The Owl Nebula is a planetary nebula in Ursa Major, formed when a Sun-like star released its outer atmosphere. Its famous dark “eyes” arise from the shell’s layered structure and viewing angle, not from empty holes in space.",
};

type SkyLocation = { raDeg: number | null; decDeg: number | null; constellation: string };

const skyLocations: Record<string, SkyLocation> = {
  "IC 443": { raDeg: 94.25, decDeg: 22.57, constellation: "Gemini" },
  "IC 1318A": { raDeg: 304.5, decDeg: 41.5, constellation: "Cygnus" },
  "M 92": { raDeg: 259.28, decDeg: 43.14, constellation: "Hercules" },
  "C 34": { raDeg: 311.41, decDeg: 30.72, constellation: "Cygnus" },
  "NGC 5907": { raDeg: 228.97, decDeg: 56.33, constellation: "Draco" },
  "SH2-142": { raDeg: 341.84, decDeg: 58.12, constellation: "Cepheus" },
  "NGC 281": { raDeg: 13.25, decDeg: 56.62, constellation: "Cassiopeia" },
  Unknown: { raDeg: null, decDeg: null, constellation: "Uncharted field" },
  "IC 5146": { raDeg: 328.35, decDeg: 47.27, constellation: "Cygnus" },
  "M 27": { raDeg: 299.9, decDeg: 22.72, constellation: "Vulpecula" },
  "M 81": { raDeg: 148.89, decDeg: 69.07, constellation: "Ursa Major" },
  "C 27": { raDeg: 303.03, decDeg: 38.36, constellation: "Cygnus" },
  "IC 5070": { raDeg: 312.7, decDeg: 44.35, constellation: "Cygnus" },
  "M 101": { raDeg: 210.8, decDeg: 54.35, constellation: "Ursa Major" },
  "IC 1396A": { raDeg: 324.21, decDeg: 57.52, constellation: "Cepheus" },
  "NGC 6946": { raDeg: 308.72, decDeg: 60.15, constellation: "Cepheus" },
  "M 33": { raDeg: 23.46, decDeg: 30.66, constellation: "Triangulum" },
  "M 31": { raDeg: 10.68, decDeg: 41.27, constellation: "Andromeda" },
  "M 42": { raDeg: 83.82, decDeg: -5.39, constellation: "Orion" },
  "C 31": { raDeg: 79.02, decDeg: 34.45, constellation: "Auriga" },
  "NGC 6992": { raDeg: 314.08, decDeg: 31.72, constellation: "Cygnus" },
  "NGC 7023": { raDeg: 315.4, decDeg: 68.17, constellation: "Cepheus" },
  "NGC 6888": { raDeg: 303.03, decDeg: 38.36, constellation: "Cygnus" },
  "NGC 6960": { raDeg: 311.41, decDeg: 30.72, constellation: "Cygnus" },
  "M 51": { raDeg: 202.47, decDeg: 47.2, constellation: "Canes Venatici" },
  "M 102": { raDeg: 226.62, decDeg: 55.76, constellation: "Draco" },
  "NGC 7331": { raDeg: 339.27, decDeg: 34.42, constellation: "Pegasus" },
  "IC 1318B": { raDeg: 304.75, decDeg: 40.25, constellation: "Cygnus" },
  "M 13": { raDeg: 250.42, decDeg: 36.46, constellation: "Hercules" },
  "M 3": { raDeg: 205.55, decDeg: 28.38, constellation: "Canes Venatici" },
  "M 29": { raDeg: 305.99, decDeg: 38.52, constellation: "Cygnus" },
  "M 97": { raDeg: 168.7, decDeg: 55.02, constellation: "Ursa Major" },
};

type Capture = {
  file: string; frames: number; object: string; title: string; exposure: string; filter: string;
  date: string; fact: string; provenance: string; imageRatio: number;
  raDeg: number | null; decDeg: number | null; constellation: string;
  comparison: CaptureComparison | null;
};

function parseCapture(file: string): Capture {
  const match = file.match(/^Stacked_(\d+)_(.+)_([\d.]+)s_(LP|IRCUT)_(\d{8})-(\d{6})_(cleaned|hand_processed)\.(jpg|png)$/)!;
  const [, frames, rawObject, exposure, filter, date, treatment] = match;
  const isMosaic = rawObject.startsWith("mosaic_");
  const object = rawObject.replace(/^mosaic_/, "");
  const displayObject = isMosaic ? `${object} · Mosaic` : object;
  const sky = skyLocations[object] ?? skyLocations.Unknown;
  const comparison = comparisons.find((item) => item.seestarImage === file) ?? null;
  const isoDate = `${date.slice(0, 4)}-${date.slice(4, 6)}-${date.slice(6, 8)}T12:00:00`;
  return {
    file,
    frames: Number(frames),
    object: displayObject,
    title: commonNames[object] ?? object,
    exposure: `${exposure}s`,
    filter,
    date: new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", year: "numeric" }).format(new Date(isoDate)),
    fact: facts[object] ?? "Every field is a time capsule: the light recorded here began its journey long before it reached the telescope.",
    provenance: treatment === "hand_processed" ? "Hand processed" : "Color corrected",
    imageRatio: imageRatios[file] ?? 1080 / 1920,
    comparison,
    ...sky,
  };
}

const captures = imageFiles.map(parseCapture).sort((a, b) => a.title.localeCompare(b.title));

type Phase = "focused" | "pullback" | "traveling" | "arriving";

type VoteSnapshot = {
  captureId: string;
  seestar: number;
  nightskyai: number;
  total: number;
  choice: VoteChoice | null;
};

type VoteUiState = {
  captureId: string;
  snapshot: VoteSnapshot | null;
  busy: boolean;
  error: string;
};

function publicImageUrl(path: string) {
  return path.startsWith("/") ? path : `/images/${encodeURIComponent(path)}`;
}

function curatedImage(capture: Capture) {
  const comparison = capture.comparison;
  if (comparison?.curatedDefault === "nightskyai") return comparison.nightskyaiImage;
  return comparison?.seestarImage ?? capture.file;
}

function skyPoint(capture: Capture) {
  if (capture.raDeg === null || capture.decDeg === null) return { x: 50, y: 35 };
  const x = 8 + (capture.raDeg / 360) * 84;
  const normalizedDeclination = Math.max(0, Math.min(1, (capture.decDeg + 10) / 85));
  return { x, y: 59 - normalizedDeclination * 43 };
}

function formatRa(degrees: number | null) {
  if (degrees === null) return "Not cataloged";
  const totalMinutes = Math.round((degrees / 15) * 60);
  return `${String(Math.floor(totalMinutes / 60)).padStart(2, "0")}h ${String(totalMinutes % 60).padStart(2, "0")}m`;
}

function formatDec(degrees: number | null) {
  if (degrees === null) return "Not cataloged";
  return `${degrees >= 0 ? "+" : "−"}${Math.abs(degrees).toFixed(1)}°`;
}

function formatObservationDay(timestamp: string) {
  const dateParts = timestamp.match(/^(\d{4})-?(\d{2})-?(\d{2})/);
  const date = dateParts
    ? new Date(Date.UTC(Number(dateParts[1]), Number(dateParts[2]) - 1, Number(dateParts[3])))
    : new Date(timestamp);
  if (Number.isNaN(date.getTime())) return timestamp;
  return new Intl.DateTimeFormat("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
    timeZone: "UTC",
  }).format(date);
}

function formatObservationSpan(comparison: CaptureComparison) {
  const first = formatObservationDay(comparison.nightskyFirstTimestamp);
  const last = formatObservationDay(comparison.nightskyLastTimestamp);
  const dateRange = first === last ? first : `${first} – ${last}`;
  const nights = `${comparison.nightskyNightCount} observing ${comparison.nightskyNightCount === 1 ? "night" : "nights"}`;
  return `${dateRange} · ${nights}`;
}

export default function Home() {
  const initial = Math.max(0, captures.findIndex((capture) => capture.object === "IC 5146"));
  const [index, setIndex] = useState(initial);
  const [originIndex, setOriginIndex] = useState(initial);
  const [targetIndex, setTargetIndex] = useState(initial);
  const [phase, setPhase] = useState<Phase>("focused");
  const [reducedMotion, setReducedMotion] = useState(false);
  const [variantOverride, setVariantOverride] = useState<{ captureId: string; choice: VoteChoice } | null>(null);
  const [voteState, setVoteState] = useState<VoteUiState | null>(null);
  const touchX = useRef<number | null>(null);
  const capture = captures[index];
  const origin = captures[originIndex];
  const target = captures[targetIndex];
  const comparison = capture.comparison;
  const variant = comparison && variantOverride?.captureId === comparison.captureId
    ? variantOverride.choice
    : comparison?.curatedDefault ?? "seestar";
  const vote = comparison && voteState?.captureId === comparison.captureId ? voteState.snapshot : null;
  const voteReady = Boolean(comparison && voteState?.captureId === comparison.captureId);
  const voteBusy = comparison && voteState?.captureId === comparison.captureId ? voteState.busy : false;
  const voteError = comparison && voteState?.captureId === comparison.captureId ? voteState.error : "";

  const selectVariant = (choice: VoteChoice) => {
    if (comparison) setVariantOverride({ captureId: comparison.captureId, choice });
  };

  const activeImage = comparison && variant === "nightskyai" ? comparison.nightskyaiImage : comparison?.seestarImage ?? capture.file;
  const activeFrames = comparison && variant === "nightskyai" ? comparison.nightskyaiFrames : capture.frames;
  const activeProvenance = comparison && variant === "nightskyai" ? "NightSkyAI restack" : capture.provenance;
  // Keep the viewing window fixed while blinking between treatments so the
  // comparison does not resize or jump beneath the visitor's gaze.
  const activeRatio = capture.imageRatio;

  const move = useCallback((delta: number) => {
    if (phase !== "focused") return;
    const nextIndex = (index + delta + captures.length) % captures.length;
    if (reducedMotion) {
      setIndex(nextIndex);
      setOriginIndex(nextIndex);
      setTargetIndex(nextIndex);
      return;
    }
    setOriginIndex(index);
    setTargetIndex(nextIndex);
    setPhase("pullback");
  }, [index, phase, reducedMotion]);

  useEffect(() => {
    const media = window.matchMedia("(prefers-reduced-motion: reduce)");
    const updatePreference = () => setReducedMotion(media.matches);
    updatePreference();
    media.addEventListener("change", updatePreference);
    return () => media.removeEventListener("change", updatePreference);
  }, []);

  useEffect(() => {
    if (phase === "focused") return;
    const nextPhase = phase === "pullback" ? "traveling" : phase === "traveling" ? "arriving" : "focused";
    const delay = phase === "traveling" ? 1150 : 720;
    const timer = window.setTimeout(() => {
      if (phase === "traveling") setIndex(targetIndex);
      if (phase === "arriving") setOriginIndex(targetIndex);
      setPhase(nextPhase);
    }, delay);
    return () => window.clearTimeout(timer);
  }, [phase, targetIndex]);

  useEffect(() => {
    const warm = [captures[(index + 1) % captures.length], captures[(index - 1 + captures.length) % captures.length]];
    warm.forEach((item) => { const image = new Image(); image.src = publicImageUrl(curatedImage(item)); });
    if (comparison) {
      const alternate = new Image();
      alternate.src = publicImageUrl(variant === "seestar" ? comparison.nightskyaiImage : comparison.seestarImage);
    }
  }, [comparison, index, variant]);

  useEffect(() => {
    if (!comparison) return;

    const controller = new AbortController();
    const captureId = comparison.captureId;
    fetch(`/api/votes?captureId=${encodeURIComponent(comparison.captureId)}`, { signal: controller.signal })
      .then(async (response) => {
        if (!response.ok) throw new Error("Votes are temporarily unavailable.");
        return response.json() as Promise<VoteSnapshot>;
      })
      .then((snapshot) => setVoteState({ captureId, snapshot, busy: false, error: "" }))
      .catch((error: unknown) => {
        if (error instanceof DOMException && error.name === "AbortError") return;
        setVoteState({ captureId, snapshot: null, busy: false, error: "Votes are temporarily unavailable." });
      });

    return () => controller.abort();
  }, [comparison]);

  const submitVote = async (choice: VoteChoice) => {
    if (!comparison || !voteReady || voteBusy) return;
    const captureId = comparison.captureId;
    selectVariant(choice);
    setVoteState((current) => ({
      captureId,
      snapshot: current?.captureId === captureId ? current.snapshot : null,
      busy: true,
      error: "",
    }));
    try {
      const response = await fetch("/api/votes", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ captureId: comparison.captureId, choice }),
      });
      if (!response.ok) throw new Error("Vote failed");
      setVoteState({ captureId, snapshot: await response.json() as VoteSnapshot, busy: false, error: "" });
    } catch {
      setVoteState((current) => ({
        captureId,
        snapshot: current?.captureId === captureId ? current.snapshot : null,
        busy: false,
        error: "Your vote did not save. Please try again.",
      }));
    }
  };

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.repeat) return;
      if (event.key === "ArrowLeft") move(-1);
      if (event.key === "ArrowRight") move(1);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [move]);

  const progress = useMemo(() => `${String(index + 1).padStart(2, "0")} / ${String(captures.length).padStart(2, "0")}`, [index]);
  const fromPoint = skyPoint(origin);
  const toPoint = skyPoint(target);
  const skyStyle = {
    "--from-x": `${fromPoint.x}%`, "--from-y": `${fromPoint.y}%`,
    "--to-x": `${toPoint.x}%`, "--to-y": `${toPoint.y}%`,
    "--capture-ratio": activeRatio,
  } as CSSProperties;
  const journeyLabel = phase === "pullback"
    ? `Pulling back from ${origin.title}`
    : phase === "traveling"
      ? `Crossing the sky from ${origin.constellation} to ${target.constellation}`
      : phase === "arriving"
        ? `Diving into ${target.title}`
        : `Viewing ${capture.title}`;

  return (
    <main className="gallery-shell" onTouchStart={(e) => { touchX.current = e.touches[0].clientX; }} onTouchEnd={(e) => {
      if (touchX.current === null) return;
      const distance = e.changedTouches[0].clientX - touchX.current;
      if (Math.abs(distance) > 55) move(distance > 0 ? -1 : 1);
      touchX.current = null;
    }}>
      <header className="site-header">
        <div className="brand"><span className="brand-mark" />Deep Space Field Notes</div>
        <div className="collection-count">Observatory Archive · {captures.length} Captures · 50+ Frames</div>
      </header>

      <section className={`portal-stage phase-${phase}`} style={skyStyle} aria-busy={phase !== "focused"}>
        <div className="sky-dome" aria-hidden="true">
          <div className="sky-grid" />
          <div className="sky-origin" style={{ left: `${fromPoint.x}%`, top: `${fromPoint.y}%` }}><i /><span>{origin.object}</span></div>
          <div className="sky-destination" style={{ left: `${toPoint.x}%`, top: `${toPoint.y}%` }}><i /><span>{target.object}</span></div>
          <div className="sky-reticle"><i /></div>
          <div className="ground-location"><span>You are here</span><strong>Northern Michigan</strong><small>45.1° N · Celestial atlas projection</small></div>
        </div>

        <div className="journey-status" role="status" aria-live="polite">
          <span>{phase === "focused" ? "Telescope locked" : phase === "traveling" ? "Slewing across the sky" : "Changing field of view"}</span>
          <strong>{journeyLabel}</strong>
        </div>

        <figure className="capture-frame" key={`${capture.file}-${phase}`}>
          <img src={publicImageUrl(activeImage)} alt={`${capture.title}, ${variant === "nightskyai" ? "NightSkyAI restack" : "gallery edit"}`} />
          {comparison && (
            <div className="processing-switch" role="group" aria-label="Choose processing view">
              <span>Processing view</span>
              <div>
                <button type="button" aria-pressed={variant === "seestar"} onClick={() => selectVariant("seestar")}>Gallery edit</button>
                <button type="button" aria-pressed={variant === "nightskyai"} onClick={() => selectVariant("nightskyai")}>NightSkyAI</button>
              </div>
            </div>
          )}
          <span className="photo-watermark" aria-hidden="true"><b>Deep Space Field Notes</b><small>© Brian Jean</small></span>
          <figcaption>
            <span className="catalog-line"><i />{capture.object}</span>
          </figcaption>
        </figure>

        <aside className="observation-rail">
          <article className="telemetry">
            <p className="eyebrow">Observation · {capture.object}</p>
            <h1>{capture.title}</h1>
            <p className="provenance">{activeProvenance}</p>
            <dl>
              <div><dt>Constellation</dt><dd>{capture.constellation}</dd></div>
              <div><dt>Apparent position · J2000</dt><dd>{formatRa(capture.raDeg)} · {formatDec(capture.decDeg)}</dd></div>
              <div><dt>Frames</dt><dd>{activeFrames} × {capture.exposure}</dd></div>
              <div>
                <dt>{comparison && variant === "nightskyai" ? "Observation span" : "Captured"}</dt>
                <dd>{comparison && variant === "nightskyai" ? formatObservationSpan(comparison) : capture.date}</dd>
              </div>
            </dl>
          </article>
          <article className="field-note">
            <p className="eyebrow">Field Note · {capture.object}</p>
            <p className="fact">{capture.fact}</p>
            <p className="filter-note">{capture.filter === "LP" ? "Light-pollution filter" : "IR-cut filter"} · Seestar field observation</p>
            {comparison && (
              <section className="vote-panel" aria-label="Informal browser poll">
                <p className="vote-question">Which treatment earns the sky?</p>
                <p className="vote-intro">The Gallery edit is the selected Seestar or hand-finished image. Compare the completed results above: NightSkyAI may combine more frames across multiple nights, so this is not a controlled same-light test.</p>
                <div className="vote-options">
                  <button type="button" aria-pressed={vote?.choice === "seestar"} disabled={!voteReady || voteBusy} onClick={() => submitVote("seestar")}>Gallery edit</button>
                  <button type="button" aria-pressed={vote?.choice === "nightskyai"} disabled={!voteReady || voteBusy} onClick={() => submitVote("nightskyai")}>NightSkyAI</button>
                </div>
                {vote?.choice && (
                  <div className="vote-results" aria-live="polite">
                    <div><span>Gallery edit</span><i><b style={{ width: `${vote.total ? (vote.seestar / vote.total) * 100 : 0}%` }} /></i><strong>{vote.seestar}</strong></div>
                    <div><span>NightSkyAI</span><i><b style={{ width: `${vote.total ? (vote.nightskyai / vote.total) * 100 : 0}%` }} /></i><strong>{vote.nightskyai}</strong></div>
                  </div>
                )}
                {voteError && <p className="vote-error" role="status">{voteError}</p>}
                <p className="vote-privacy">Informal browser poll · counts are browsers, not verified people · change your choice anytime</p>
              </section>
            )}
            <p className="projection-note">Sky travel follows catalog coordinates; the horizon scene is interpretive rather than a live time-and-direction calculation.</p>
          </article>
        </aside>
      </section>

      <nav className="capture-nav" aria-label="Browse captures">
        <button onClick={() => move(-1)} aria-label="Previous capture" disabled={phase !== "focused"}><span>←</span> Previous sky field</button>
        <div className="progress-wrap">
          <span>{progress}</span>
          <div className="progress-track"><i style={{ width: `${((index + 1) / captures.length) * 100}%` }} /></div>
        </div>
        <button onClick={() => move(1)} aria-label="Next capture" disabled={phase !== "focused"}>Next sky field <span>→</span></button>
      </nav>
      <p className="hint">Use arrow keys or swipe to travel the collection</p>
    </main>
  );
}
