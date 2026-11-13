# Branding

`icon.png` is Odoo's own app icon (the purple "o" mark), used only to identify this
integration with Odoo in Flow Steward.

## Source

- Brand: Odoo, Odoo S.A. — <https://www.odoo.com/>.
- Official asset: the app icon in Odoo's own repository, `odoo/odoo`, branch `19.0`
  (commit `df56738ca8250bfc545477342e71bd7696cfc6e1`), file
  `addons/web/static/img/odoo-icon.svg`:
  <https://raw.githubusercontent.com/odoo/odoo/19.0/addons/web/static/img/odoo-icon.svg>
  (512 × 512 SVG, purple `#714B67` ring and white centre inside a white disc, transparent
  corners).
- SHA-256 of the source: `9cdedf25cece4d49ade8e2ee89341ec65e6cffc506eba39839970b75a3015774`
  (downloaded 2026-11-13).

The same directory holds raster exports of this mark,
`odoo-icon-192x192.png` (SHA-256 `af264dcc4cf4fa828321b81a10b25367aea118a13fae9f2670be2e1c0350e904`)
and `odoo-icon-512x512.png` (SHA-256 `498e823ba106d0d84b76e15fe599059dc98c1e68a64d8e85ba321e75517b54a4`).
Releases 0.1.0 to 0.1.3 shipped the 192 px PNG reduced to 128 px. From 0.1.4 the icon is
rendered from the vector original instead, so its edges come from the artwork itself
rather than from a resampled bitmap. The wordmark logos on Odoo's brand assets page are
wide, not square, and are not meant for icon use.

## What was done

- Rendered the SVG to a 256 × 256 PNG with CairoSVG 2.9.1 at the SVG's own square
  proportions, re-saved with Pillow 13.0.0 (`optimize=True`); no cropping, padding,
  recolouring or other change:

  ```python
  png = cairosvg.svg2png(url="odoo-icon.svg", output_width=256, output_height=256)
  Image.open(io.BytesIO(png)).convert("RGBA").save("icon.png", optimize=True)
  ```

- Compared with the source: same shape, proportions and colours as the SVG and as Odoo's
  own 192 px and 512 px PNG exports (identical mark bounding box); checked on light and
  dark backgrounds and at 48 px.
- SHA-256 of `icon.png`: `e4f95158dad6df4d6aa07d98073fce7f6c15c8087350ec369fb19d1b150b5641`.

## Use

Odoo and the Odoo logo are trademarks of Odoo S.A. Odoo's brand assets page,
<https://www.odoo.com/page/brand-assets> (checked 2026-11-13), asks not to recreate the
logo, to use it only in the colours provided, to keep sufficient clear space around it
and to respect a minimum height of 20 px; partner logos are reserved for official Odoo
partners. This icon is Odoo's own artwork, unaltered in colour and shape, shown at more
than 20 px, and it is not a partner logo. It only shows which product this integration
connects to; it does not imply endorsement by or partnership with Odoo S.A.
