# Deep Space Field Notes

**A cinematic observatory journal built from real astrophotography captured under Northern Michigan skies.**

[Explore the live collection](https://deep-space-field-notes.vjgwrz72pz.chatgpt.site/)

![Deep Space Field Notes — a cinematic journey through the Northern Michigan night sky](public/og.png)

## The idea

An observing session can produce hundreds of files: short exposures, partial stacks, alternate edits, and multiple attempts at the same target. The photograph worth sharing is often buried inside that archive, and a folder full of filenames does little to convey what it felt like to stand outside and aim a telescope into the dark.

Deep Space Field Notes turns that archive into an experience.

Each stop pairs one carefully selected image with its place in the sky, capture details, processing history, and a concise astronomical story. Moving to the next image feels like pulling back from the telescope, crossing the night sky, and settling onto a new target—not paging through a conventional photo grid.

## What the product does

- Presents one deep-sky capture at a time, giving every image room to breathe.
- Connects each photograph to its constellation and catalog position.
- Explains what the viewer is seeing in approachable field notes.
- Preserves useful observing context, including frame count, exposure, filter, date, and processing method.
- Supports buttons, arrow keys, and mobile swipes for a natural gallery experience.
- Uses a cinematic Northern Michigan observatory setting to keep the viewer grounded beneath the same sky where the images were captured.

The current collection includes **33 observations** spanning galaxies, nebulae, supernova remnants, and globular clusters.

## From telescope to field note

The collection follows a deliberate curation path:

1. Find the strongest stacked image for each observing folder or target.
2. Exclude stacks with fewer than 50 frames, where noise and weak signal tend to overwhelm the subject.
3. Prefer a finished image from a hand-processed `proc` folder when one exists.
4. Otherwise, apply conservative color correction and histogram work to reduce strong red or green casts without inventing detail.
5. Keep the original capture untouched and publish only the selected processed result.
6. Add catalog coordinates, capture metadata, and an accessible astronomical field note.

The result is not intended to compete with professional observatory imagery. It is a living record of what a small smart telescope can reveal from a backyard in Northern Michigan—and a more inviting way to share that record with other people.

## Honest sky travel

The transitions between observations follow each target's catalog coordinates, so the direction and relative movement are grounded in the real celestial map. The horizon and observatory scene are cinematic rather than a live planetarium calculation: they do not claim to reproduce the exact altitude, direction, season, or time of night for every capture.

## Design principles

- **Observation before interface.** The photograph remains the focal point.
- **Context without a textbook.** Field notes favor clear, memorable explanations.
- **Quality over volume.** Weak stacks stay out of the public collection.
- **Transparent processing.** Every image is labeled as hand processed or color corrected.
- **Mobile first, desktop considered.** Portrait captures remain immersive on a phone while the wider observatory view gives them presence on larger screens.
- **Motion with restraint.** Sky travel adds a sense of place and respects reduced-motion preferences.

## Run it locally

Requires Node.js 22.13 or newer.

```bash
npm install
npm run dev
```

Then open the local address shown in the terminal. To validate a production build:

```bash
npm test
```

## Project map

- `app/page.tsx` contains the curated observation catalog and gallery experience.
- `app/globals.css` defines the cinematic observatory presentation and sky-travel motion.
- `public/images/` contains the selected processed captures.
- `tests/` checks the collection, interactions, descriptions, and required media.

## Photography

Astrophotography © Brian Jean. The images are shared here as part of Deep Space Field Notes; please ask before reusing or redistributing the original image assets.
