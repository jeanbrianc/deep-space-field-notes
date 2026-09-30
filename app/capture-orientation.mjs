import orientations from './capture-orientation.json' with { type: 'json' };

/** Presentation rotation only; original captures and alignment references stay unchanged. */
export function captureRotation(file, treatment = 'seestar') {
  return orientations[file]?.[treatment] ?? 0;
}
