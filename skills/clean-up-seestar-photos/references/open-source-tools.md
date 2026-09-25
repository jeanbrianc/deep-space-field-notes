# Open-source astrophotography tools

## Siril

- Repository/site: https://siril.org/
- Documentation: https://siril.readthedocs.io/en/stable/
- Best fit: calibration, registration, stacking, background extraction, photometric color calibration, and histogram/asinh/GHS stretching.
- Preferred when FITS or individual light frames are available.
- Recommended order after stacking: crop borders, remove gradients, calibrate color, then stretch the histogram.

## GraXpert

- Repository: https://github.com/Steffenhir/GraXpert
- Best fit: open-source gradient extraction and optional AI denoising.
- Supports Apple Silicon releases and command-line processing.
- Treat AI output as presentational. Retain the original and disclose the operation.

## ASTRO-1

- Repository: https://github.com/ohlee-one/astro-1
- Best fit: an example automation pipeline that orchestrates Siril and GraXpert rather than reimplementing their algorithms.

## Local JPEG pass

Use `astro_cleanup.py` for fast offline JPEG finishing. It estimates dim sky pixels in Lab color space, fades background chroma correction away from highlights, applies an asinh luminance stretch, gently smooths chroma noise, preserves originals, and records parameters in JSON. It cannot perform true stacking, flat/dark calibration, plate solving, or photometric color calibration from a display JPEG.
