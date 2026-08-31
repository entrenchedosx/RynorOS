# RynorOS assets

Purpose: canonical OS resources, separate from executable kernel code.

Implemented: `branding/icon.png` is the official RynorOS icon, supplied with the
project and relocated without changing its bytes. It is a 1254 x 1254, 8-bit RGBA
PNG. `project.json` records its role and path. Its original SHA-256 is
`beac0bc23e59cdad3ddbbddcee9cb7d9444c90c15654a4f9c520d7ce61c6b353`.

Public package interface: `build/rynoros-resources.zip` contains
`assets/branding/icon.png` and `manifest.json` (OS, asset role, dimensions, size,
and SHA-256). Fixed ZIP timestamps, Unix file modes, entry order, and no compression
make the package deterministic without depending on zlib versions. The build
manifest hashes this package alongside the kernel artifacts.

Invariants: retain the original PNG; no resizing, conversion, kernel embedding,
or insertion into the BIOS image. The package is a host-distributed OS resource,
not a guest filesystem or an in-memory boot resource. No kernel code reads it.

Tests: PNG header validation, package contents/hash/dimensions, identical repeated
packages/builds, and missing/corrupt asset rejection.

Limitations: Stage 9 renders a framebuffer test and bounded text, but the icon
is still **packaged, not rendered**. There is no PNG decoder, graphical UI, guest archive reader, small-icon
variant, or resource-loader ABI. Future graphics work must choose size/decoding
requirements and can derive variants from this canonical original explicitly.

Stage 7 identity integration uses this exact PNG in a 56-pixel README title
lockup, not a replacement image or resized binary resource. The guest's serial
startup presentation pairs the same names, RynorOS and Rynorkernel, with its
current execution stage. The original two-line boot regression prefix is kept;
later `[SYSTEM]` lines identify the execution, keyboard and framebuffer stages. No raster art or fake graphics
are sent to serial, and package bytes remain unchanged.
