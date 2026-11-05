/// <reference types="node" />

import { readFileSync } from "node:fs";

import { type District, decodeDistrict, type Manifest } from "../src/bundle";

/** The committed district bundle, read from disk the way the browser fetches it. */
export function committedDistrict(): District {
  const dir = new URL("../public/district/", import.meta.url);
  const manifest: Manifest = JSON.parse(
    readFileSync(new URL("kaptol-donji-grad.json", dir), "utf-8"),
  );
  const file = readFileSync(new URL(manifest.bin, dir));
  const bin = file.buffer.slice(file.byteOffset, file.byteOffset + file.byteLength);
  return decodeDistrict(manifest, bin);
}
