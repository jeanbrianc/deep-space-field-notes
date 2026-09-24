"use client";

import { useCallback, useEffect, useMemo, useRef, useState, type CSSProperties } from "react";

const imageFiles = [
  "Stacked_101_IC 443_10.0s_LP_20260303-213610_cleaned.jpg",
  "Stacked_114_IC 1318A_10.0s_LP_20250821-221720_cleaned.jpg",
  "Stacked_124_M 92_10.0s_IRCUT_20250915-210455_cleaned.jpg",
  "Stacked_133_M 106_10.0s_LP_20250530-225212_cleaned.jpg",
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
  Vega: "Vega", "M 106": "Messier 106", "C 34": "Western Veil Nebula", "NGC 5907": "Splinter Galaxy",
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
  "IC 443": "A supernova remnant where an ancient stellar explosion is colliding with a molecular cloud.",
  "M 45": "This nearby open cluster is wrapped in blue reflection nebulosity lit by its hottest stars.",
  "IC 1318A": "Glowing hydrogen clouds spread around the bright star Sadr in the heart of Cygnus.",
  "M 92": "One of the Milky Way’s oldest globular clusters, packed with hundreds of thousands of stars.",
  Vega: "Vega is one of the brightest stars in Earth’s night sky and was once the northern pole star.",
  "M 106": "Its unusually active core is powered by matter falling toward a central supermassive black hole.",
  "C 34": "This filament is part of the vast Veil Nebula, debris from a supernova thousands of years ago.",
  "NGC 5907": "Seen nearly edge-on, this spiral galaxy appears as a thin silver needle against deep space.",
  "SH2-142": "Young stars sculpt the gas and dust of this emission nebula into its wizard-like silhouette.",
  "C 39": "A dying Sun-like star expelled these luminous shells while its hot core became a white dwarf.",
  "NGC 281": "Dark dust lanes cut into glowing hydrogen, giving this star-forming cloud its familiar nickname.",
  "IC 5146": "The Cocoon is a stellar nursery threaded by the dark molecular cloud Barnard 168.",
  "M 27": "The first planetary nebula ever cataloged, it shows the expanding atmosphere of a dying star.",
  "M 81": "A grand-design spiral about 12 million light-years away, paired in the sky with galaxy M82.",
  "C 27": "A massive star’s powerful wind is carving a luminous bubble through surrounding gas.",
  "IC 5070": "Ionized gas and dark dust combine to trace the long beak of this Cygnus emission nebula.",
  "M 101": "This face-on spiral’s asymmetry records gravitational encounters with neighboring galaxies.",
  "IC 1396A": "A dense pillar of gas and dust is being eroded by radiation from nearby young stars.",
  "NGC 6946": "Ten observed supernovae earned this face-on spiral the name Fireworks Galaxy.",
  "M 33": "The Triangulum Galaxy is the third-largest member of our Local Group of galaxies.",
  "M 31": "The nearest large spiral galaxy is moving toward the Milky Way on a multi-billion-year timescale.",
  "M 42": "The closest massive star-forming region to Earth glows beneath Orion’s Belt.",
  "C 31": "Radiation from the runaway star AE Aurigae lights and sculpts this dusty nebula.",
  "NGC 6992": "This lace-like arc is the eastern edge of an enormous supernova remnant in Cygnus.",
  "NGC 7023": "A young star illuminates blue dust while complex carbon molecules glow reddish around it.",
  "NGC 6888": "The fast wind of a Wolf–Rayet star has swept older stellar material into a glowing shell.",
  "NGC 6960": "The bright star 52 Cygni lies in front of this delicate western strand of the Veil Nebula.",
  "Arcturus": "This orange giant is the brightest star in Boötes and one of our nearest stellar giants.",
  "C 22": "A compact planetary nebula whose vivid blue-green color comes from ionized oxygen.",
  Jupiter: "The Solar System’s largest planet rotates in under ten hours, driving powerful cloud bands.",
  "M 51": "Its smaller companion is tugging on the Whirlpool Galaxy and helping trigger new star formation.",
  "M 108": "This nearly edge-on spiral is mottled by dusty lanes and bright knots of star formation.",
  "M 110": "A small elliptical satellite of Andromeda, visible near its much larger neighbor.",
  "M 100": "A grand-design spiral in the Virgo Cluster, viewed almost face-on from Earth.",
  "NGC 4631": "Its distorted shape and vigorous star formation reflect interactions with nearby galaxies.",
  "M 102": "This lenticular galaxy is known for a prominent dust lane crossing its bright central bulge.",
  "NGC 7331": "Often called a Milky Way twin, this spiral is actually somewhat larger than our galaxy.",
  "IC 1318B": "A dark lane divides this broad hydrogen-emission region into butterfly-like wings.",
  Uranus: "Methane in its atmosphere absorbs red light, giving the ice giant its cyan appearance.",
  "M 13": "This ancient spherical swarm contains hundreds of thousands of stars in the halo of our galaxy.",
  "M 3": "One of the richest globular clusters, it contains an unusually large population of variable stars.",
  "M 29": "A young open cluster embedded in the rich star fields of the Cygnus constellation.",
  "M 97": "Two darker regions in its expanding gas shell create the planetary nebula’s owl-like face.",
};

type SkyLocation = { raDeg: number | null; decDeg: number | null; constellation: string };

const skyLocations: Record<string, SkyLocation> = {
  "IC 443": { raDeg: 94.25, decDeg: 22.57, constellation: "Gemini" },
  "IC 1318A": { raDeg: 304.5, decDeg: 41.5, constellation: "Cygnus" },
  "M 92": { raDeg: 259.28, decDeg: 43.14, constellation: "Hercules" },
  "M 106": { raDeg: 184.74, decDeg: 47.3, constellation: "Canes Venatici" },
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
};

function parseCapture(file: string): Capture {
  const match = file.match(/^Stacked_(\d+)_(.+)_([\d.]+)s_(LP|IRCUT)_(\d{8})-(\d{6})_(cleaned|hand_processed)\.(jpg|png)$/)!;
  const [, frames, rawObject, exposure, filter, date, treatment] = match;
  const isMosaic = rawObject.startsWith("mosaic_");
  const object = rawObject.replace(/^mosaic_/, "");
  const displayObject = isMosaic ? `${object} · Mosaic` : object;
  const sky = skyLocations[object] ?? skyLocations.Unknown;
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
    ...sky,
  };
}

const captures = imageFiles.map(parseCapture).sort((a, b) => a.title.localeCompare(b.title));

type Phase = "focused" | "pullback" | "traveling" | "arriving";

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

export default function Home() {
  const initial = Math.max(0, captures.findIndex((capture) => capture.object === "IC 5146"));
  const [index, setIndex] = useState(initial);
  const [originIndex, setOriginIndex] = useState(initial);
  const [targetIndex, setTargetIndex] = useState(initial);
  const [phase, setPhase] = useState<Phase>("focused");
  const [reducedMotion, setReducedMotion] = useState(false);
  const touchX = useRef<number | null>(null);
  const capture = captures[index];
  const origin = captures[originIndex];
  const target = captures[targetIndex];

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
    warm.forEach((item) => { const image = new Image(); image.src = `/images/${encodeURIComponent(item.file)}`; });
  }, [index]);

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
    "--capture-ratio": capture.imageRatio,
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
          <img src={`/images/${encodeURIComponent(capture.file)}`} alt={`${capture.title}, captured with a Seestar telescope`} />
          <figcaption>
            <span className="catalog-line"><i />{capture.object}</span>
          </figcaption>
        </figure>

        <aside className="observation-rail">
          <article className="telemetry">
            <p className="eyebrow">Observation · {capture.object}</p>
            <h1>{capture.title}</h1>
            <p className="provenance">{capture.provenance}</p>
            <dl>
              <div><dt>Constellation</dt><dd>{capture.constellation}</dd></div>
              <div><dt>Apparent position · J2000</dt><dd>{formatRa(capture.raDeg)} · {formatDec(capture.decDeg)}</dd></div>
              <div><dt>Frames</dt><dd>{capture.frames} × {capture.exposure}</dd></div>
              <div><dt>Captured</dt><dd>{capture.date}</dd></div>
            </dl>
          </article>
          <article className="field-note">
            <p className="eyebrow">Field Note · {capture.object}</p>
            <p className="fact">{capture.fact}</p>
            <p className="filter-note">{capture.filter === "LP" ? "Light-pollution filter" : "IR-cut filter"} · Seestar field observation</p>
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
